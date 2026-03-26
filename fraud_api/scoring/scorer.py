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

from fraudshield_core.models import FeatureVector, Decision
from fraudshield_core.config import config
from fraud_api.scoring.strategies.base import ScoringStrategy


class FraudScorer:
    """
    Context class for Strategy pattern (slide 66-67).

    Knows NOTHING about which algorithm is running.
    Just calls strategy.score() and applies the threshold.

    Circuit breaker pattern:
        scorer.set_strategy(RuleBasedStrategy())  ← when ML is down
        scorer.set_strategy(XGBoostStrategy())    ← when ML recovers
    """

    def __init__(self, strategy: ScoringStrategy,
                 fraud_threshold: float  = None,
                 review_threshold: float = None):
        self._strategy        = strategy
        self.fraud_threshold  = fraud_threshold  or config.FRAUD_THRESHOLD
        self.review_threshold = review_threshold or config.REVIEW_THRESHOLD

    @property
    def strategy_name(self) -> str:
        return self._strategy.name

    def set_strategy(self, strategy: ScoringStrategy) -> None:
        """Hot-swap algorithm. No restart needed."""
        print(f"  [FraudScorer] Switching: {self._strategy.name} → {strategy.name}")
        self._strategy = strategy

    def score(self, features: FeatureVector) -> dict:
        """
        1. Call current strategy
        2. Apply threshold → decision
        3. Return enriched result
        """
        result   = self._strategy.score(features)
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
            "strategy_used":  self._strategy.name,
        }
