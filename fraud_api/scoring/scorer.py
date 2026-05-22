"""
fraud_api/scoring/scorer.py
────────────────────────────
FraudScorer — the Strategy Pattern context class.
Owns a ScoringStrategy. Hot-swaps it at runtime.

fraud_api/scoring/feature_builder.py
──────────────────────────────────────
FeatureBuilder — builds FeatureVector from raw Transaction.
Reads from feature store (Redis) + transaction repository.
"""

# ─────────────────────────────────────────────
# scorer.py
# ─────────────────────────────────────────────

import random

from fraudshield_core.models import FeatureVector, Decision
from fraudshield_core.config import config
from fraud_api.scoring.strategies.base import ScoringStrategy


class FraudScorer:
    """
    Context class for Strategy pattern.

    Supports champion/challenger A/B routing: when a challenger strategy is
    registered, CHALLENGER_TRAFFIC_PCT fraction of requests are scored by the
    challenger instead of the champion. Both use the same thresholds.

    Circuit breaker:
        scorer.set_strategy(RuleBasedStrategy())  ← when ML is down
        scorer.set_strategy(XGBoostStrategy())    ← when ML recovers
    """

    def __init__(self, strategy: ScoringStrategy,
                 fraud_threshold:  float = None,
                 review_threshold: float = None):
        self._champion        = strategy
        self._challenger      = None
        self._challenger_pct  = 0.0
        self.fraud_threshold  = fraud_threshold  or config.FRAUD_THRESHOLD
        self.review_threshold = review_threshold or config.REVIEW_THRESHOLD

    @property
    def strategy_name(self) -> str:
        return self._champion.name

    def set_strategy(self, strategy: ScoringStrategy) -> None:
        """Hot-swap champion. No restart needed."""
        print(f"  [FraudScorer] Champion: {self._champion.name} -> {strategy.name}")
        self._champion = strategy

    def set_challenger(self, strategy: ScoringStrategy, traffic_pct: float) -> None:
        """
        Register a challenger strategy for A/B routing.
        traffic_pct: fraction of requests routed to challenger (0.0–1.0).
        Set strategy=None to disable A/B routing.
        """
        self._challenger     = strategy
        self._challenger_pct = max(0.0, min(1.0, traffic_pct))
        if strategy:
            print(f"  [FraudScorer] Challenger: {strategy.name} "
                  f"({self._challenger_pct*100:.0f}% traffic)")
        else:
            print("  [FraudScorer] Challenger disabled")

    def score(self, features: FeatureVector) -> dict:
        """
        1. Route to champion or challenger (A/B)
        2. Apply threshold → decision
        3. Return enriched result including ab_variant
        """
        use_challenger = (
            self._challenger is not None
            and self._challenger_pct > 0.0
            and random.random() < self._challenger_pct
        )
        strategy   = self._challenger if use_challenger else self._champion
        ab_variant = "challenger" if use_challenger else "champion"

        result   = strategy.score(features)
        sc       = result["score"]
        decision = (
            Decision.BLOCK  if sc >= self.fraud_threshold  else
            Decision.REVIEW if sc >= self.review_threshold else
            Decision.ALLOW
        )
        return {
            "score":          sc,
            "decision":       decision,
            "reason_codes":   result["reason_codes"],
            "strategy_used":  strategy.name,
            "ab_variant":     ab_variant,
        }
