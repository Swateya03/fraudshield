"""
fraud_api/scoring/strategies/rule_based.py
───────────────────────────────────────────
Rule-based fallback strategy.
Always available — no model file needed.
Activates when circuit breaker opens on XGBoost strategy.
"""

from fraud_api.scoring.strategies.base import ScoringStrategy
from fraudshield_core.models import FeatureVector


class RuleBasedStrategy(ScoringStrategy):
    """
    Simple deterministic rules.
    Lower accuracy than XGBoost but always available.
    This is your circuit breaker fallback (slide 42).
    """
    name = "rule_based_fallback"

    def score(self, features: FeatureVector) -> dict:
        codes = []
        score = 0.0

        if features.is_new_device:
            score += 0.30
            codes.append({"code": "new_device", "contribution": 0.30})

        if features.velocity_1h > 5:
            score += 0.35
            codes.append({"code": "velocity_spike_1h", "contribution": 0.35})

        if features.amount_ratio > 5.0:
            score += 0.25
            codes.append({"code": "amount_spike", "contribution": 0.25})

        if features.is_late_night:
            score += 0.10
            codes.append({"code": "unusual_hour", "contribution": 0.10})

        if features.ip_fraud_history > 0.5:
            score += 0.40
            codes.append({"code": "high_risk_ip", "contribution": 0.40})

        if features.merchant_risk_score > 0.7:
            score += 0.20
            codes.append({"code": "high_risk_merchant", "contribution": 0.20})

        return {
            "score":        round(min(score, 1.0), 4),
            "reason_codes": codes,
        }
