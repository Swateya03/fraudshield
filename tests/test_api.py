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
        # Lower than production (0.85/0.50) so rule-based scores reliably hit BLOCK in CI
        "FRAUD_THRESHOLD":        0.50,
        "REVIEW_THRESHOLD":       0.20,
        "CURRENT_MODEL_VERSION":  "nonexistent",  # forces rule-based fallback
    }
    originals = {k: getattr(config, k) for k in _overrides}
    for k, v in _overrides.items():
        setattr(config, k, v)
    # _authenticate reads os.getenv("API_TOKEN") at request time for live rotation;
    # the env var must also be updated so the test token is accepted.
    _orig_env_token = os.environ.get("API_TOKEN")
    os.environ["API_TOKEN"] = "test_token_123"

    # Ensure parent directory exists — local_store/ is gitignored
    _TEST_DB.parent.mkdir(parents=True, exist_ok=True)

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
    if _orig_env_token is None:
        os.environ.pop("API_TOKEN", None)
    else:
        os.environ["API_TOKEN"] = _orig_env_token
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

    def test_health_shape(self, client):
        data = client.get("/health").json()
        assert data["status"] in ("ok", "degraded")
        assert "checks" in data
        assert "db"    in data["checks"]
        assert "redis" in data["checks"]
        assert "model" in data["checks"]

    def test_health_model_check_has_strategy(self, client):
        data = client.get("/health").json()
        model = data["checks"]["model"]
        assert model["strategy"] in ("xgboost_v1", "rule_based_fallback", "rule_based")


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
        from unittest.mock import patch

        _store = {}

        class _FakeRedis:
            def get(self, key):
                return _store.get(key)
            def setex(self, key, ttl, value):
                _store[key] = value

        _fake = _FakeRedis()

        with patch("fraud_api.main.get_redis", return_value=_fake):
            payload = {**LEGIT_TXN, "transaction_id": "txn_idem_001"}
            headers = {**HEADERS, "Idempotency-Key": "idem_test_key_abc"}

            resp1 = client.post("/v1/transactions/score",
                                json=payload, headers=headers)
            resp2 = client.post("/v1/transactions/score",
                                json=payload, headers=headers)

            assert resp1.status_code == 200
            assert resp2.status_code == 200
            assert resp2.headers.get("X-Idempotent-Replay") == "true"
            assert resp1.json()["fraud_probability"] == \
                   resp2.json()["fraud_probability"]


class TestBatchScore:
    # dry_run=True avoids writing to DB, so repeated calls with the same IDs
    # don't hit the UNIQUE constraint on fraud_scores.transaction_id.
    BATCH_BODY = {
        "transactions": [
            {**FRAUD_TXN, "transaction_id": "txn_batch_001", "dry_run": True},
            {**LEGIT_TXN, "transaction_id": "txn_batch_002", "dry_run": True},
        ]
    }

    def test_batch_returns_202_accepted(self, client):
        resp = client.post("/v1/transactions/score/batch", json=self.BATCH_BODY, headers=HEADERS)
        # 200 is also acceptable — FastAPI returns 200 when no explicit status_code is set
        assert resp.status_code in (200, 202)

    def test_batch_result_count(self, client):
        resp = client.post("/v1/transactions/score/batch", json=self.BATCH_BODY, headers=HEADERS)
        data = resp.json()
        assert data["count"] == 2
        assert data["errors"] == 0
        assert len(data["results"]) == 2

    def test_batch_each_item_has_decision(self, client):
        resp = client.post("/v1/transactions/score/batch", json=self.BATCH_BODY, headers=HEADERS)
        for item in resp.json()["results"]:
            assert item["decision"] in ("allow", "review", "block")

    def test_batch_rejects_empty_list(self, client):
        resp = client.post(
            "/v1/transactions/score/batch",
            json={"transactions": []},
            headers=HEADERS,
        )
        assert resp.status_code == 422

    def test_batch_requires_auth(self, client):
        resp = client.post("/v1/transactions/score/batch", json=self.BATCH_BODY)
        assert resp.status_code == 401


class TestExplainEndpoint:
    def test_explain_404_for_unknown_txn(self, client):
        resp = client.get("/v1/transactions/txn_does_not_exist/explain", headers=HEADERS)
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"]["code"] == "TRANSACTION_NOT_FOUND"

    def test_explain_returns_reason_codes_after_score(self, client):
        # Score a transaction first so it exists in the DB
        txn_id = "txn_explain_001"
        client.post(
            "/v1/transactions/score",
            json={**LEGIT_TXN, "transaction_id": txn_id},
            headers=HEADERS,
        )
        resp = client.get(f"/v1/transactions/{txn_id}/explain", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["transaction_id"] == txn_id
        assert "fraud_probability" in data
        assert "reason_codes"      in data
        assert isinstance(data["reason_codes"], list)

    def test_explain_requires_auth(self, client):
        resp = client.get("/v1/transactions/txn_any/explain")
        assert resp.status_code == 401


class TestPromoteEndpoint:
    def test_promote_404_for_unknown_version(self, client):
        resp = client.post(
            "/v1/model/promote/v_does_not_exist",
            headers=HEADERS,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"]["code"] == "VERSION_NOT_FOUND"

    def test_promote_requires_auth(self, client):
        resp = client.post("/v1/model/promote/v1.0.0")
        assert resp.status_code == 401


class TestWebhookDelivery:
    """Unit tests for _fire_webhook — no HTTP server required."""

    def test_skips_when_url_empty(self):
        """No URL configured → returns immediately without raising."""
        import asyncio
        from fraud_api.main import _fire_webhook
        from fraudshield_core.config import config
        original = config.WEBHOOK_URL
        config.WEBHOOK_URL = ""
        try:
            asyncio.run(_fire_webhook({"decision": "block", "transaction_id": "t1"}))
        finally:
            config.WEBHOOK_URL = original

    def test_skips_non_matching_decision(self):
        """WEBHOOK_EVENTS=block but decision=allow → no HTTP call attempted."""
        import asyncio
        from unittest.mock import patch, AsyncMock
        from fraud_api.main import _fire_webhook
        from fraudshield_core.config import config

        original_url    = config.WEBHOOK_URL
        original_events = config.WEBHOOK_EVENTS
        config.WEBHOOK_URL    = "http://webhook.example.com/hook"
        config.WEBHOOK_EVENTS = frozenset({"block"})
        try:
            with patch("fraud_api.main._httpx.AsyncClient") as mock_cls:
                asyncio.run(_fire_webhook({"decision": "allow", "transaction_id": "t2"}))
                mock_cls.assert_not_called()
        finally:
            config.WEBHOOK_URL    = original_url
            config.WEBHOOK_EVENTS = original_events

    def test_posts_on_matching_decision(self):
        """decision=block with WEBHOOK_EVENTS=block → HTTP POST is made once."""
        import asyncio
        from unittest.mock import patch, AsyncMock, MagicMock
        from fraud_api.main import _fire_webhook
        from fraudshield_core.config import config

        original_url    = config.WEBHOOK_URL
        original_events = config.WEBHOOK_EVENTS
        config.WEBHOOK_URL    = "http://webhook.example.com/hook"
        config.WEBHOOK_EVENTS = frozenset({"block"})

        mock_response          = MagicMock()
        mock_response.status_code = 200
        mock_client            = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__  = AsyncMock(return_value=None)
        mock_client.post       = AsyncMock(return_value=mock_response)

        try:
            with patch("fraud_api.main._httpx.AsyncClient", return_value=mock_client):
                asyncio.run(_fire_webhook({"decision": "block", "transaction_id": "t3"}))
            mock_client.post.assert_called_once()
            url_called = mock_client.post.call_args[0][0]
            assert url_called == "http://webhook.example.com/hook"
        finally:
            config.WEBHOOK_URL    = original_url
            config.WEBHOOK_EVENTS = original_events

    def test_retries_on_server_error(self):
        """5xx response triggers up to 3 attempts (with mocked sleep)."""
        import asyncio
        from unittest.mock import patch, AsyncMock, MagicMock
        from fraud_api.main import _fire_webhook
        from fraudshield_core.config import config

        original_url    = config.WEBHOOK_URL
        original_events = config.WEBHOOK_EVENTS
        config.WEBHOOK_URL    = "http://webhook.example.com/hook"
        config.WEBHOOK_EVENTS = frozenset({"block"})

        mock_response          = MagicMock()
        mock_response.status_code = 500
        mock_client            = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__  = AsyncMock(return_value=None)
        mock_client.post       = AsyncMock(return_value=mock_response)

        try:
            with patch("fraud_api.main._httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", new_callable=AsyncMock):
                asyncio.run(_fire_webhook({"decision": "block", "transaction_id": "t4"}))
            assert mock_client.post.call_count == 3
        finally:
            config.WEBHOOK_URL    = original_url
            config.WEBHOOK_EVENTS = original_events


class TestHealthProbes:
    def test_liveness_always_200(self, client):
        resp = client.get("/health/live")
        assert resp.status_code == 200
        assert resp.json()["status"] == "alive"

    def test_readiness_shape(self, client):
        # Rule-based strategy → model always loaded; DB and Redis may or may not be up
        resp = client.get("/health/ready")
        assert resp.status_code in (200, 503)

    def test_model_card_requires_auth(self, client):
        resp = client.get("/v1/model/card")
        assert resp.status_code == 401

    def test_user_history_requires_auth(self, client):
        resp = client.get("/v1/users/u_test/history")
        assert resp.status_code == 401

    def test_user_history_returns_list(self, client):
        resp = client.get("/v1/users/u_nonexistent/history", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "history" in data
        assert isinstance(data["history"], list)