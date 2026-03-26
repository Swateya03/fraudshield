"""
tests/test_scoring.py
──────────────────────
Unit tests for scoring strategies and FraudScorer.
Uses InMemoryUserRepository — zero database, instant.
"""

import pytest
from datetime import datetime
from fraudshield_core.models import (
    User, Transaction, FeatureVector,
    RiskTier, KYCStatus, Channel, Decision
)
from fraud_api.scoring.scorer import FraudScorer
from fraud_api.scoring.strategies.rule_based import RuleBasedStrategy


def make_feature_vector(
    velocity_1h:      float = 0.0,
    amount_ratio:     float = 1.0,
    is_new_device:    int   = 0,
    ip_fraud_history: float = 0.0,
    is_late_night:    int   = 0,
    merchant_risk_score: float = 0.1,
) -> FeatureVector:
    return FeatureVector(
        transaction_id   = "txn_test",
        user_id          = "u_test",
        velocity_1h      = velocity_1h,
        amount_ratio     = amount_ratio,
        is_new_device    = is_new_device,
        ip_fraud_history = ip_fraud_history,
        is_late_night    = is_late_night,
        merchant_risk_score = merchant_risk_score,
    )


class TestRuleBasedStrategy:

    def test_clean_transaction_is_allowed(self):
        strategy = RuleBasedStrategy()
        features = make_feature_vector()
        result   = strategy.score(features)
        assert result["score"] < 0.5
        assert len(result["reason_codes"]) == 0

    def test_new_device_raises_score(self):
        strategy = RuleBasedStrategy()
        features = make_feature_vector(is_new_device=1)
        result   = strategy.score(features)
        assert result["score"] >= 0.30
        assert any(rc["code"] == "new_device" for rc in result["reason_codes"])

    def test_velocity_spike_raises_score(self):
        strategy = RuleBasedStrategy()
        features = make_feature_vector(velocity_1h=10)
        result   = strategy.score(features)
        assert result["score"] >= 0.35

    def test_known_fraud_ip_raises_score_significantly(self):
        strategy = RuleBasedStrategy()
        features = make_feature_vector(ip_fraud_history=1.0)
        result   = strategy.score(features)
        assert result["score"] >= 0.40

    def test_combined_signals_blocks(self):
        """Multiple fraud signals → block."""
        strategy = RuleBasedStrategy()
        features = make_feature_vector(
            is_new_device=1, velocity_1h=8, amount_ratio=10,
            ip_fraud_history=1.0, is_late_night=1
        )
        result = strategy.score(features)
        assert result["score"] >= 0.85

    def test_score_never_exceeds_1(self):
        strategy = RuleBasedStrategy()
        features = make_feature_vector(
            velocity_1h=100, amount_ratio=50, is_new_device=1,
            ip_fraud_history=1.0, is_late_night=1
        )
        result = strategy.score(features)
        assert result["score"] <= 1.0


class TestFraudScorer:

    def test_allow_decision_for_low_score(self):
        scorer   = FraudScorer(RuleBasedStrategy())
        features = make_feature_vector()
        result   = scorer.score(features)
        assert result["decision"] == Decision.ALLOW

    def test_block_decision_for_high_score(self):
        scorer   = FraudScorer(RuleBasedStrategy())
        features = make_feature_vector(
            is_new_device=1, velocity_1h=8,
            ip_fraud_history=1.0, is_late_night=1
        )
        result = scorer.score(features)
        assert result["decision"] == Decision.BLOCK

    def test_strategy_swap_at_runtime(self):
        """FraudScorer can swap strategy without restart."""
        scorer   = FraudScorer(RuleBasedStrategy())
        assert scorer.strategy_name == "rule_based_fallback"

        scorer.set_strategy(RuleBasedStrategy())  # swap to same (for test)
        assert scorer.strategy_name == "rule_based_fallback"

    def test_result_includes_strategy_used(self):
        scorer   = FraudScorer(RuleBasedStrategy())
        features = make_feature_vector()
        result   = scorer.score(features)
        assert "strategy_used" in result
        assert result["strategy_used"] == "rule_based_fallback"
