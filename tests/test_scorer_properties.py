"""
tests/test_scorer_properties.py
─────────────────────────────────
Hypothesis property-based tests for the scoring pipeline.
These complement hand-written unit tests by generating random inputs
and asserting invariants that must always hold — regardless of values.

Install:  pip install hypothesis   (already in requirements.txt)
Run:      pytest tests/test_scorer_properties.py -v
"""

import math
import pytest
from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

from fraudshield_core.models import FeatureVector, Decision


# ── Strategies ────────────────────────────────────────────────────────────────

# A valid probability score (what the strategy returns)
score_st = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)

# A valid FeatureVector (all 21 features, all finite)
feature_vector_st = st.builds(
    FeatureVector,
    log_amount       = st.floats(0.0, 15.0,  allow_nan=False),
    amount_ratio     = st.floats(0.01, 50.0, allow_nan=False),
    amount_zscore    = st.floats(-5.0, 20.0, allow_nan=False),
    is_round_amount  = st.sampled_from([0.0, 1.0]),
    hour_of_day      = st.floats(0.0, 23.0,  allow_nan=False),
    day_of_week      = st.floats(0.0, 6.0,   allow_nan=False),
    is_weekend       = st.sampled_from([0.0, 1.0]),
    is_night         = st.sampled_from([0.0, 1.0]),
    velocity_1h      = st.floats(0.0, 500.0, allow_nan=False),
    velocity_24h     = st.floats(0.0, 500.0, allow_nan=False),
    velocity_7d      = st.floats(0.0, 500.0, allow_nan=False),
    user_avg_amount  = st.floats(0.0, 1_000_000.0, allow_nan=False),
    user_std_amount  = st.floats(0.0, 500_000.0,   allow_nan=False),
    device_age_days  = st.floats(0.0, 3650.0, allow_nan=False),
    is_new_device    = st.sampled_from([0.0, 1.0]),
    is_known_ip      = st.sampled_from([0.0, 1.0]),
    merchant_risk    = st.sampled_from([0.2, 0.5, 1.0]),
    ip_risk          = st.sampled_from([0.0, 1.0]),
    user_risk_tier   = st.floats(0.0, 1.0, allow_nan=False),
    kyc_status       = st.floats(0.0, 1.0, allow_nan=False),
    channel_risk     = st.floats(0.0, 1.0, allow_nan=False),
)


# ── Scorer threshold invariants ───────────────────────────────────────────────

class TestScorerThresholdInvariants:
    """
    The decision returned by FraudScorer.score() must always be consistent
    with the probability returned by the underlying strategy — for any score value.
    """

    @given(score=score_st)
    @settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
    def test_block_threshold_invariant(self, score):
        """score ≥ fraud_threshold → decision is always BLOCK."""
        from fraud_api.scoring.scorer import FraudScorer
        from fraud_api.scoring.strategies.base import ScoringStrategy

        class FixedStrategy(ScoringStrategy):
            name = "fixed"
            def score(self, _fv):
                return {"score": score, "reason_codes": []}

        fraud_thr  = 0.85
        review_thr = 0.50
        scorer = FraudScorer(FixedStrategy(), fraud_threshold=fraud_thr, review_threshold=review_thr)
        result = scorer.score(None)

        if score >= fraud_thr:
            assert result["decision"] == Decision.BLOCK
        elif score >= review_thr:
            assert result["decision"] == Decision.REVIEW
        else:
            assert result["decision"] == Decision.ALLOW

    @given(
        fraud_thr  = st.floats(0.5, 1.0, allow_nan=False),
        review_thr = st.floats(0.0, 0.5, allow_nan=False),
        score      = score_st,
    )
    @settings(max_examples=300)
    def test_decision_is_total_and_deterministic(self, fraud_thr, review_thr, score):
        """Every (score, threshold) combination produces exactly one decision."""
        from fraud_api.scoring.scorer import FraudScorer
        from fraud_api.scoring.strategies.base import ScoringStrategy

        assume(fraud_thr > review_thr)  # thresholds must be ordered

        class FixedStrategy(ScoringStrategy):
            name = "fixed"
            def score(self, _fv):
                return {"score": score, "reason_codes": []}

        scorer = FraudScorer(FixedStrategy(), fraud_threshold=fraud_thr, review_threshold=review_thr)
        result1 = scorer.score(None)
        result2 = scorer.score(None)

        assert result1["decision"] == result2["decision"]
        assert result1["decision"] in (Decision.ALLOW, Decision.REVIEW, Decision.BLOCK)


# ── Rule-based strategy invariants ───────────────────────────────────────────

class TestRuleBasedInvariants:
    """
    The rule-based strategy must always return a score in [0, 1]
    and a non-empty or empty list of reason codes — never crash.
    """

    @given(fv=feature_vector_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_score_is_valid_probability(self, fv):
        from fraud_api.scoring.strategies.rule_based import RuleBasedStrategy

        strategy = RuleBasedStrategy()
        result = strategy.score(fv)

        assert "score" in result
        assert 0.0 <= result["score"] <= 1.0
        assert not math.isnan(result["score"])
        assert not math.isinf(result["score"])

    @given(fv=feature_vector_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_reason_codes_are_strings(self, fv):
        from fraud_api.scoring.strategies.rule_based import RuleBasedStrategy

        strategy = RuleBasedStrategy()
        result = strategy.score(fv)

        assert isinstance(result["reason_codes"], list)
        for code in result["reason_codes"]:
            assert isinstance(code, str)

    @given(fv=feature_vector_st)
    @settings(max_examples=100)
    def test_high_risk_signals_never_allow(self, fv):
        """When all four high-risk signals are on, rule-based must not return ALLOW."""
        from fraud_api.scoring.strategies.rule_based import RuleBasedStrategy
        from fraud_api.scoring.scorer import FraudScorer

        # Force the worst-case feature values
        import dataclasses
        fv_worst = dataclasses.replace(
            fv,
            ip_risk=1.0,
            merchant_risk=1.0,
            is_new_device=1.0,
            velocity_1h=50.0,
        )
        strategy = RuleBasedStrategy()
        scorer   = FraudScorer(strategy)
        result   = scorer.score(fv_worst)

        assert result["decision"] != Decision.ALLOW, (
            f"All-risk features should not produce ALLOW (got score={result['score']:.4f})"
        )


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_zero_features_does_not_crash(self):
        """A transaction from a brand-new user with no history produces a valid score."""
        from fraud_api.scoring.strategies.rule_based import RuleBasedStrategy

        fv = FeatureVector(
            **{field: 0.0 for field in FeatureVector.FEATURE_NAMES}
        )
        strategy = RuleBasedStrategy()
        result   = strategy.score(fv)
        assert 0.0 <= result["score"] <= 1.0

    def test_maximum_features_does_not_crash(self):
        """Extreme feature values (velocity spike, max amount) produce a valid score."""
        from fraud_api.scoring.strategies.rule_based import RuleBasedStrategy

        fv = FeatureVector(
            log_amount=math.log1p(10_000_000),
            amount_ratio=1000.0,
            amount_zscore=100.0,
            is_round_amount=1.0,
            hour_of_day=3.0,
            day_of_week=6.0,
            is_weekend=1.0,
            is_night=1.0,
            velocity_1h=999.0,
            velocity_24h=999.0,
            velocity_7d=999.0,
            user_avg_amount=0.0,
            user_std_amount=0.0,
            device_age_days=0.0,
            is_new_device=1.0,
            is_known_ip=0.0,
            merchant_risk=1.0,
            ip_risk=1.0,
            user_risk_tier=1.0,
            kyc_status=1.0,
            channel_risk=1.0,
        )
        strategy = RuleBasedStrategy()
        result   = strategy.score(fv)
        assert 0.0 <= result["score"] <= 1.0
