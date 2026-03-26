"""
fraud_api/scoring/strategies/base.py
──────────────────────────────────────
ScoringStrategy interface (ABC).

Scaling rule: new fraud pattern? Write a new Strategy class.
FraudScorer never changes (Open/Closed Principle, slide 66).

Circuit breaker fires? scorer.set_strategy(RuleBasedStrategy())
ML model available again? scorer.set_strategy(XGBoostStrategy())
No restart. No deployment. One line.
"""

from abc import ABC, abstractmethod
from fraudshield_core.models import FeatureVector


class ScoringStrategy(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name — stored in fraud_scores.strategy_used column."""
        ...

    @abstractmethod
    def score(self, features: FeatureVector) -> dict:
        """
        Returns:
            {
                "score": float,          # 0.0 to 1.0
                "reason_codes": list     # [{"code": "new_device", "contribution": 0.31}]
            }
        """
        ...
