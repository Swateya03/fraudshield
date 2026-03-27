"""
tests/test_api.py
──────────────────
Integration tests for the fraud scoring API.
Uses real SQLite + InMemory strategy (no model file needed).
"""

import os
import pytest
from fastapi.testclient import TestClient
from pathlib import Path

_TEST_DB = Path(__file__).resolve().parent.parent / "local_store" / "test_api.db"


@pytest.fixture(scope="module")
def client():
    """Create test client with rule-based strategy (no model file needed)."""
    from fraudshield_core.config import config

    # Clean up any leftover test DB from a previous run
    if _TEST_DB.exists():
        os.remove(_TEST_DB)

    _overrides = {
        "API_TOKEN":              "test_token_123",
        "DB_URL":                 f"sqlite:///{_TEST_DB.as_posix()}",
        "FRAUD_THRESHOLD":        0.50,
        "REVIEW_THRESHOLD":       0.20,
        "CURRENT_MODEL_VERSION":  "nonexistent",  # forces rule-based fallback
    }
    originals = {k: getattr(config, k) for k in _overrides}
    for k, v in _overrides.items():
        setattr(config, k, v)

    # Recreate engine with test DB URL
    from fraudshield_core import db as db_module
    from sqlalchemy import create_engine
    test_engine = create_engine(
        config.DB_URL,
        connect_args={"check_same_thread": False},
        echo=False,
        pool_pre_ping=True,
    )
    db_module.engine = test_engine
    db_module.metadata.create_all(test_engine)

    from fraud_api.main import app
    yield TestClient(app)

    test_engine.dispose()
    for k, v in originals.items():
        setattr(config, k, v)
    try:
        if _TEST_DB.exists():
            os.remove(_TEST_DB)
    except PermissionError:
        pass


HEADERS = {
    "Authorization":  "Bearer test_token_123",
    "Content-Type":   "application/json",
}

FRAUD_TXN = {
    "transaction_id": "txn_ci_fraud_001",
    "user_id":        "u_ci_test",
    "merchant_id":    "m_crypto",
    "amount":         45000.00,
    "currency":       "INR",
    "channel":        "online",
    "ip_address":     "185.220.101.5",
}

LEGIT_TXN = {
    "transaction_id": "txn_ci_legit_001",
    "user_id":        "u_ci_test",
    "merchant_id":    "m_grocery",
    "amount":         450.00,
    "currency":       "INR",
    "channel":        "online",
    "ip_address":     "203.112.45.67",
}


class TestHealthCheck:
    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_has_strategy(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert "strategy" in data
        assert data["strategy"] in ("xgboost_v1", "rule_based_fallback")


class TestAuthentication:
    def test_missing_auth_returns_401(self, client):
        resp = client.post("/v1/transactions/score", json=FRAUD_TXN)
        assert resp.status_code == 401

    def test_wrong_token_returns_401(self, client):
        resp = client.post(
            "/v1/transactions/score",
            json=FRAUD_TXN,
            headers={"Authorization": "Bearer wrong_token"}
        )
        assert resp.status_code == 401

    def test_valid_token_passes(self, client):
        resp = client.post(
            "/v1/transactions/score",
            json=FRAUD_TXN,
            headers=HEADERS
        )
        assert resp.status_code == 200


class TestValidation:
    def test_missing_amount_returns_422(self, client):
        bad = {k: v for k, v in FRAUD_TXN.items() if k != "amount"}
        resp = client.post(
            "/v1/transactions/score",
            json=bad, headers=HEADERS
        )
        assert resp.status_code == 422

    def test_negative_amount_returns_422(self, client):
        bad = {**FRAUD_TXN, "amount": -100,
               "transaction_id": "txn_neg_001"}
        resp = client.post(
            "/v1/transactions/score",
            json=bad, headers=HEADERS
        )
        assert resp.status_code == 422

    def test_invalid_currency_returns_422(self, client):
        bad = {**FRAUD_TXN, "currency": "GBP",
               "transaction_id": "txn_cur_001"}
        resp = client.post(
            "/v1/transactions/score",
            json=bad, headers=HEADERS
        )
        assert resp.status_code == 422

    def test_invalid_channel_returns_422(self, client):
        bad = {**FRAUD_TXN, "channel": "carrier_pigeon",
               "transaction_id": "txn_ch_001"}
        resp = client.post(
            "/v1/transactions/score",
            json=bad, headers=HEADERS
        )
        assert resp.status_code == 422


class TestScoring:
    def test_response_has_required_fields(self, client):
        resp = client.post(
            "/v1/transactions/score",
            json={**FRAUD_TXN, "transaction_id": "txn_fields_001"},
            headers=HEADERS
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "fraud_probability" in data
        assert "decision"          in data
        assert "reason_codes"      in data
        assert "latency_ms"        in data
        assert "request_id"        in data

    def test_fraud_probability_between_0_and_1(self, client):
        resp = client.post(
            "/v1/transactions/score",
            json={**FRAUD_TXN, "transaction_id": "txn_prob_001"},
            headers=HEADERS
        )
        data = resp.json()
        assert 0.0 <= data["fraud_probability"] <= 1.0

    def test_decision_is_valid_enum(self, client):
        resp = client.post(
            "/v1/transactions/score",
            json={**FRAUD_TXN, "transaction_id": "txn_enum_001"},
            headers=HEADERS
        )
        assert resp.json()["decision"] in ("allow", "review", "block")

    def test_latency_within_sla(self, client):
        resp = client.post(
            "/v1/transactions/score",
            json={**FRAUD_TXN, "transaction_id": "txn_sla_001"},
            headers=HEADERS
        )
        assert resp.json()["latency_ms"] < 200

    def test_idempotency_key_returns_same_result(self, client):
        payload = {**LEGIT_TXN, "transaction_id": "txn_idem_001"}
        headers = {**HEADERS, "Idempotency-Key": "idem_test_key_abc"}

        resp1 = client.post("/v1/transactions/score",
                            json=payload, headers=headers)
        resp2 = client.post("/v1/transactions/score",
                            json=payload, headers=headers)

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.json()["fraud_probability"] == \
               resp2.json()["fraud_probability"]