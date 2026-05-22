"""
tests/test_redis_features.py
──────────────────────────────
Integration tests requiring a real Redis instance.
Skipped automatically when Redis is unavailable (local dev without Redis).

Run in CI via the redis-integration job in test.yml.
Run locally:  pytest tests/test_redis_features.py -v
"""

import json
import pytest


def _redis_available() -> bool:
    try:
        import redis
        redis.from_url("redis://localhost:6379").ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _redis_available(),
    reason="Redis not available — skipping Redis integration tests",
)

# Explicit set of keys created by these tests — cleaned before and after each test.
_TEST_KEYS = [
    "velocity:u_redis_test:1h",
    "velocity:u_redis_test:6h",
    "velocity:u_redis_test:24h",
    "features:offline:u_redis_test",
    "idempotency:test_idem_001",
    "idempotency:test_idem_002",
    "sse_token:test_sse_token_abc123",
    "sse_token:test_sse_token_xyz789",
]


@pytest.fixture(autouse=True)
def clean_test_keys():
    from fraudshield_core.redis_client import get_redis
    r = get_redis()
    for k in _TEST_KEYS:
        r.delete(k)
    yield
    for k in _TEST_KEYS:
        r.delete(k)


# ─────────────────────────────────────────────
# Velocity counters
# ─────────────────────────────────────────────

class TestVelocityCounters:

    def test_increment_and_read_1h(self):
        from fraud_api.repository.inmemory_repo import InMemoryTransactionRepository
        from fraud_api.scoring.feature_builder import FeatureBuilder

        fb = FeatureBuilder(txn_repo=InMemoryTransactionRepository())
        for _ in range(3):
            fb._increment_velocity("u_redis_test")

        assert fb._get_velocity("u_redis_test", "1h") == 3.0

    def test_all_windows_tracked_independently(self):
        from fraud_api.repository.inmemory_repo import InMemoryTransactionRepository
        from fraud_api.scoring.feature_builder import FeatureBuilder

        fb = FeatureBuilder(txn_repo=InMemoryTransactionRepository())
        fb._increment_velocity("u_redis_test")

        assert fb._get_velocity("u_redis_test", "1h")  == 1.0
        assert fb._get_velocity("u_redis_test", "6h")  == 1.0
        assert fb._get_velocity("u_redis_test", "24h") == 1.0

    def test_new_user_velocity_is_zero(self):
        from fraud_api.repository.inmemory_repo import InMemoryTransactionRepository
        from fraud_api.scoring.feature_builder import FeatureBuilder

        fb = FeatureBuilder(txn_repo=InMemoryTransactionRepository())
        assert fb._get_velocity("u_redis_test", "1h") == 0.0

    def test_velocity_keys_have_ttl(self):
        from fraud_api.repository.inmemory_repo import InMemoryTransactionRepository
        from fraud_api.scoring.feature_builder import FeatureBuilder
        from fraudshield_core.redis_client import get_redis

        fb = FeatureBuilder(txn_repo=InMemoryTransactionRepository())
        fb._increment_velocity("u_redis_test")

        r = get_redis()
        assert r.ttl("velocity:u_redis_test:1h")  > 0
        assert r.ttl("velocity:u_redis_test:6h")  > 0
        assert r.ttl("velocity:u_redis_test:24h") > 0


# ─────────────────────────────────────────────
# Offline feature store
# ─────────────────────────────────────────────

class TestOfflineFeatureStore:

    def test_round_trip(self):
        from fraud_api.repository.inmemory_repo import InMemoryTransactionRepository
        from fraud_api.scoring.feature_builder import FeatureBuilder
        from fraudshield_core.redis_client import get_redis

        payload = {
            "avg_amount": 1200.0,
            "std_amount": 300.0,
            "avg_velocity_per_day": 3.5,
            "last_txn_time": "2026-01-01T10:00:00",
        }
        get_redis().set("features:offline:u_redis_test", json.dumps(payload))

        fb = FeatureBuilder(txn_repo=InMemoryTransactionRepository())
        result = fb._get_offline_features("u_redis_test")

        assert result["avg_amount"] == 1200.0
        assert result["std_amount"] == 300.0
        assert result["avg_velocity_per_day"] == 3.5
        assert result["last_txn_time"] == "2026-01-01T10:00:00"

    def test_missing_key_returns_empty_dict(self):
        from fraud_api.repository.inmemory_repo import InMemoryTransactionRepository
        from fraud_api.scoring.feature_builder import FeatureBuilder

        fb = FeatureBuilder(txn_repo=InMemoryTransactionRepository())
        result = fb._get_offline_features("u_redis_test")
        assert result == {}

    def test_offline_features_feed_into_feature_vector(self):
        """avg_amount from Redis flows through to FeatureVector.user_avg_amount."""
        from fraud_api.repository.inmemory_repo import InMemoryTransactionRepository
        from fraud_api.scoring.feature_builder import FeatureBuilder
        from fraudshield_core.redis_client import get_redis
        from fraudshield_core.models import Transaction, Channel, User, RiskTier, KYCStatus
        from datetime import datetime

        get_redis().set(
            "features:offline:u_redis_test",
            json.dumps({"avg_amount": 999.0, "std_amount": 1.0}),
        )

        user = User(
            id="u_redis_test", email="t@t.com", phone=None,
            risk_tier=RiskTier.LOW, kyc_status=KYCStatus.VERIFIED,
            created_at=datetime(2020, 1, 1), updated_at=datetime(2024, 1, 1),
        )
        txn = Transaction(
            id="txn_r1", user_id="u_redis_test", merchant_id="m_grocery",
            device_id=None, amount=500.0, currency="INR",
            channel=Channel.ONLINE, ip_address=None,
            created_at=datetime.utcnow(),
        )

        fb = FeatureBuilder(txn_repo=InMemoryTransactionRepository())
        fv = fb.build(txn, user)

        assert fv.user_avg_amount == 999.0


# ─────────────────────────────────────────────
# Idempotency cache
# ─────────────────────────────────────────────

class TestIdempotencyCache:

    def test_store_and_retrieve(self):
        from fraudshield_core.redis_client import get_redis
        from fraudshield_core.config import config

        r = get_redis()
        payload = {"transaction_id": "txn_001", "decision": "block", "fraud_probability": 0.94}
        r.setex("idempotency:test_idem_001", config.REDIS_TTL_IDEMPOTENCY, json.dumps(payload))

        cached = r.get("idempotency:test_idem_001")
        assert cached is not None
        data = json.loads(cached)
        assert data["decision"] == "block"
        assert data["fraud_probability"] == 0.94

    def test_key_has_correct_ttl(self):
        from fraudshield_core.redis_client import get_redis
        from fraudshield_core.config import config

        r = get_redis()
        r.setex("idempotency:test_idem_002", config.REDIS_TTL_IDEMPOTENCY, json.dumps({"x": 1}))

        ttl = r.ttl("idempotency:test_idem_002")
        assert 0 < ttl <= config.REDIS_TTL_IDEMPOTENCY

    def test_missing_key_returns_none(self):
        from fraudshield_core.redis_client import get_redis

        result = get_redis().get("idempotency:test_idem_001")
        assert result is None


# ─────────────────────────────────────────────
# SSE short-lived token cache
# ─────────────────────────────────────────────

class TestSSETokenCache:
    """
    /v1/stream/token issues a 60-second Redis-backed UUID token.
    These tests exercise the low-level Redis contract directly.
    """

    _TTL = 60  # matches SSE_TOKEN_TTL in main.py

    def test_store_and_validate(self):
        from fraudshield_core.redis_client import get_redis

        r = get_redis()
        token = "test_sse_token_abc123"
        r.setex(f"sse_token:{token}", self._TTL, "1")

        exists = r.exists(f"sse_token:{token}")
        assert exists == 1, "Token should exist in Redis after setex"

    def test_token_ttl_is_bounded(self):
        from fraudshield_core.redis_client import get_redis

        r = get_redis()
        token = "test_sse_token_abc123"
        r.setex(f"sse_token:{token}", self._TTL, "1")

        ttl = r.ttl(f"sse_token:{token}")
        assert 0 < ttl <= self._TTL, f"TTL should be 1–{self._TTL}s, got {ttl}"

    def test_token_consumed_on_delete(self):
        """Simulate one-time token consumption: delete after first use."""
        from fraudshield_core.redis_client import get_redis

        r = get_redis()
        token = "test_sse_token_xyz789"
        r.setex(f"sse_token:{token}", self._TTL, "1")

        # First use: token present → delete (consume)
        deleted = r.delete(f"sse_token:{token}")
        assert deleted == 1, "delete should return 1 for an existing key"

        # Second use: token gone → rejected
        exists = r.exists(f"sse_token:{token}")
        assert exists == 0, "Token should be absent after consumption"

    def test_expired_token_not_present(self):
        """
        Verify Redis TTL expiry contract.
        We set a 1-second TTL and confirm the key disappears within 2 seconds.
        (Uses a very short TTL only in tests — never do this in production code.)
        """
        import time
        from fraudshield_core.redis_client import get_redis

        r = get_redis()
        token = "test_sse_token_abc123"
        r.setex(f"sse_token:{token}", 1, "1")  # 1-second TTL

        time.sleep(2)  # wait for expiry

        exists = r.exists(f"sse_token:{token}")
        assert exists == 0, "Token should have expired after TTL elapsed"
