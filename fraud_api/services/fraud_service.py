"""
fraud_api/services/fraud_service.py
─────────────────────────────────────
FraudService — the main orchestrator.

Wires together:
  Repository  → fetch user, save score
  FeatureBuilder → build feature vector
  FraudScorer → run strategy
  EventPublisher → notify observers

This is the class from your LLD class diagram (slide 72).
The sequence diagram (slide 73) maps directly to process().
"""

import uuid
import time
from datetime import datetime
from typing import Optional

from fraudshield_core.models import (
    Transaction, FraudScore, FraudEvent,
    Decision, User, Merchant, Device
)
from fraud_api.repository.base import (
    UserRepository, TransactionRepository,
    FraudScoreRepository, FraudLabelRepository,
    MerchantRepository,
)
from fraud_api.scoring.scorer import FraudScorer
from fraud_api.scoring.feature_builder import FeatureBuilder
from fraud_api.events.publisher import EventPublisher


class FraudService:
    """
    Main orchestrator. Implements the happy path from sequence diagram:

    1. Fetch user        (Repository)
    2. Build features    (FeatureBuilder → Redis + DB)
    3. Score             (FraudScorer → Strategy)
    4. Save score        (Repository)
    5. Publish event     (EventPublisher → Observers)
    6. Return result

    Short-circuits:
      - User blocked → score=1.0 immediately, skip ML
      - Feature build fails → use zero-vector (safe default)
      - Scoring fails → circuit breaker → RuleBasedStrategy
    """

    def __init__(self,
                 user_repo:    UserRepository,
                 txn_repo:     TransactionRepository,
                 score_repo:   FraudScoreRepository,
                 scorer:       FraudScorer,
                 feature_builder: FeatureBuilder,
                 publisher:    EventPublisher,
                 merchant_repo: MerchantRepository = None):
        self.user_repo        = user_repo
        self.txn_repo         = txn_repo
        self.score_repo       = score_repo
        self.scorer           = scorer
        self.feature_builder  = feature_builder
        self.publisher        = publisher
        self.merchant_repo    = merchant_repo

    def process(self, txn: Transaction,
                merchant:  Optional[Merchant] = None,
                device:    Optional[Device]   = None,
                dry_run:   bool               = False) -> FraudScore:
        """
        Score a transaction end-to-end.
        Returns FraudScore with decision and explanation.
        """
        start_ms = time.time() * 1000

        # ── Step 1: Save transaction ────────────────────────────
        if not dry_run:
            self.txn_repo.save(txn)

        # ── Step 2: Fetch user ──────────────────────────────────
        user = self.user_repo.get_by_id(txn.user_id)
        if user is None:
            user = self._unknown_user(txn.user_id)

        # ── Step 3: Short-circuit if blocked ────────────────────
        # Slide 73 Seq 2: blocked user path
        if user.is_blocked():
            return self._blocked_result(txn, start_ms, dry_run=dry_run)

        # ── Step 3b: Look up merchant ─────────────────────────────
        if merchant is None and self.merchant_repo:
            merchant = self.merchant_repo.get_by_id(txn.merchant_id)

        # ── Step 4: Build feature vector ────────────────────────
        features = self.feature_builder.build(txn, user, merchant, device)

        # ── Step 5: Score ────────────────────────────────────────
        # Circuit breaker: if XGBoost fails, scorer auto-switches to fallback
        try:
            result = self.scorer.score(features)
        except Exception as ex:
            print(f"  [FraudService] Scoring error: {ex} — using fallback")
            from fraud_api.scoring.strategies.rule_based import RuleBasedStrategy
            self.scorer.set_strategy(RuleBasedStrategy())
            result = self.scorer.score(features)

        # ── Step 6: Build FraudScore object ─────────────────────
        latency_ms = int(time.time() * 1000 - start_ms)
        fraud_score = FraudScore(
            id             = str(uuid.uuid4()),
            transaction_id = txn.id,
            score          = result["score"],
            decision       = result["decision"],
            reason_codes   = result["reason_codes"],
            model_version  = result.get("strategy_used", "unknown"),  # reads actual strategy,
            strategy_used  = result["strategy_used"],
            latency_ms     = latency_ms,
            scored_at      = datetime.utcnow(),
        )

        # ── Step 7: Save score ───────────────────────────────────
        if not dry_run:
            self.score_repo.save(fraud_score)

        # ── Step 8: Publish event ────────────────────────────────
        if dry_run:
            return fraud_score

        event = FraudEvent(
            transaction_id = txn.id,
            user_id        = txn.user_id,
            merchant_id    = txn.merchant_id,
            amount         = txn.amount,
            score          = fraud_score.score,
            decision       = fraud_score.decision,
            reason_codes   = fraud_score.reason_codes,
            model_version  = fraud_score.model_version,
            latency_ms     = latency_ms,
            scored_at      = fraud_score.scored_at,
        )
        try:
            self.publisher.publish(event)
        except Exception as ex:
            # Score is already committed — log and continue rather than surfacing a 500
            print(f"  [FraudService] Event publish failed, score preserved: {ex}")

        return fraud_score

    def _unknown_user(self, user_id: str) -> User:
        """Fallback user when user_id not in database."""
        from fraudshield_core.models import RiskTier, KYCStatus
        return User(
            id=user_id, email="unknown", phone=None,
            risk_tier=RiskTier.UNKNOWN, kyc_status=KYCStatus.PENDING,
            created_at=datetime.utcnow(), updated_at=datetime.utcnow()
        )

    def _blocked_result(self, txn: Transaction, start_ms: float, dry_run: bool = False) -> FraudScore:
        """Short-circuit for explicitly blocked users."""
        latency_ms  = int(time.time() * 1000 - start_ms)
        fraud_score = FraudScore(
            id             = str(uuid.uuid4()),
            transaction_id = txn.id,
            score          = 1.0,
            decision       = Decision.BLOCK,
            reason_codes   = [{"code": "user_explicitly_blocked", "contribution": 1.0}],
            model_version  = "rule",
            strategy_used  = "blocked_user_shortcircuit",
            latency_ms     = latency_ms,
            scored_at      = datetime.utcnow(),
        )
        if not dry_run:
            self.score_repo.save(fraud_score)
            event = FraudEvent(
                transaction_id = txn.id,
                user_id        = txn.user_id,
                merchant_id    = txn.merchant_id,
                amount         = txn.amount,
                score          = fraud_score.score,
                decision       = fraud_score.decision,
                reason_codes   = fraud_score.reason_codes,
                model_version  = fraud_score.model_version,
                latency_ms     = latency_ms,
                scored_at      = fraud_score.scored_at,
            )
            try:
                self.publisher.publish(event)
            except Exception as ex:
                print(f"  [FraudService] Event publish failed (blocked), score preserved: {ex}")
        return fraud_score
