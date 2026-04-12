"""
fraud_api/scoring/feature_builder.py
──────────────────────────────────────
Builds FeatureVector from a raw Transaction.

Reads:
  Online features  → Redis (velocity, last txn amount)
  Offline features → Redis feature store key (batch-updated by pipeline-service)
  Derived features → computed inline

This is the critical path — must complete in <10ms.
"""

import json
import math
from datetime import datetime
from typing import Optional

from fraudshield_core.models import Transaction, User, Merchant, Device, FeatureVector
from fraudshield_core.redis_client import get_redis
from fraudshield_core.config import config


# Known Tor exit nodes / fraud IPs (in production: a Redis SET updated daily)
_KNOWN_FRAUD_IPS = {
    "185.220.101.5",
    "185.220.101.6",
    "192.42.116.16",
    "199.87.154.255",
    "23.129.64.131",
}

# High-risk merchant categories (in production: in DB/config)
_HIGH_RISK_CATEGORIES = {"crypto", "gift_cards", "gambling", "wire_transfer"}
_MEDIUM_RISK_CATEGORIES = {"jewelry", "electronics", "luxury"}


class FeatureBuilder:
    """
    Builds a FeatureVector from a Transaction + context.

    Online features come from Redis (sub-1ms).
    Offline features come from Redis hash set by pipeline-service (sub-1ms).
    Velocity from TransactionRepository (uses index, ~1ms).

    Circuit breaker: if Redis fails once, skip it for the rest of the request
    to avoid accumulating timeout penalties (~1s per failed call).
    """

    def __init__(self, txn_repo=None):
        self._txn_repo = txn_repo  # optional, for DB velocity fallback
        self._redis    = get_redis()
        self._redis_alive = True

    def build(self, txn: Transaction, user: User,
              merchant: Optional[Merchant] = None,
              device: Optional[Device] = None) -> FeatureVector:
        """
        Main method. Assembles all 12 features.
        Called on every transaction — must be fast.
        """
        fv = FeatureVector(
            transaction_id = txn.id,
            user_id        = txn.user_id,
        )

        # ── Online features (Redis velocity counters) ──────────────
        fv.velocity_1h  = self._get_velocity(txn.user_id, window="1h")
        fv.velocity_6h  = self._get_velocity(txn.user_id, window="6h")
        fv.velocity_24h = self._get_velocity(txn.user_id, window="24h")

        # ── Offline features (batch-computed by pipeline, stored in Redis) ──
        offline = self._get_offline_features(txn.user_id)
        fv.user_avg_amount   = offline.get("avg_amount", 500.0)
        fv.user_avg_velocity = offline.get("avg_velocity_per_day", 2.0)

        # ── Device features ────────────────────────────────────────────────
        if device:
            days_known = (datetime.utcnow() - device.first_seen_at).days
            fv.device_trust_score = min(days_known / 30.0, 1.0)
            fv.is_new_device = 0 if device.is_trusted else 1
        elif txn.device_id and "fraud" in txn.device_id:
            # Explicitly flagged fraud device from simulator
            fv.device_trust_score = 0.0
            fv.is_new_device      = 1
        else:
            # No device_id sent — treat as new unknown device
            fv.device_trust_score = 0.0
            fv.is_new_device      = 1

        # ── IP features ────────────────────────────────────────────
        if txn.ip_address:
            # In production: Redis SET lookup + MaxMind GeoIP
            fv.ip_fraud_history = 1.0 if txn.ip_address in _KNOWN_FRAUD_IPS else 0.0
        else:
            fv.ip_fraud_history = 0.5  # missing IP = slightly suspicious

        # ── Merchant features ──────────────────────────────────────────────
        # Check merchant_id directly — matches how dataset.py computed it in training
        _HIGH_RISK_IDS   = {"m_crypto", "m_giftcard", "m_jewelry", "m_luxury"}
        _MEDIUM_RISK_IDS = {"m_bestbuy", "m_electronics"}

        if txn.merchant_id in _HIGH_RISK_IDS:
            fv.merchant_risk_score = 1.0
        elif merchant and merchant.category.lower() in _MEDIUM_RISK_CATEGORIES:
            fv.merchant_risk_score = 0.5
        elif merchant:
            fv.merchant_risk_score = 0.1
        else:
            fv.merchant_risk_score = 0.1  # default safe, not 0.3

        # ── Time features ──────────────────────────────────────────
        fv.hour_of_day  = txn.created_at.hour
        fv.day_of_week  = txn.created_at.weekday()
        fv.is_late_night = 1 if txn.created_at.hour in range(0, 5) else 0

        # ── User features ──────────────────────────────────────────
        fv.user_age_days = (datetime.utcnow() - user.created_at).days
        fv.is_new_user   = 1 if fv.user_age_days < 30 else 0

        # ── Derived features ───────────────────────────────────────
        fv.amount_ratio = (txn.amount / max(fv.user_avg_amount, 1.0))

        # ── amount_zscore & geo_mismatch ──────────────────────────
        std_amount = offline.get("std_amount", 1.0) or 1.0
        fv.amount_zscore = (txn.amount - fv.user_avg_amount) / max(std_amount, 1.0)

        user_city = (offline.get("user_city") or "").lower()
        merchant_city = (merchant.city or "").lower() if merchant else ""
        fv.geo_mismatch = 1 if (user_city and merchant_city and user_city != merchant_city) else 0

        # ── Day 5 features ───────────────────────────────────────
        fv.log_amount = math.log1p(txn.amount)
        fv.is_round_amount = 1 if (txn.amount == int(txn.amount) and txn.amount % 100 == 0) else 0
        fv.is_weekend = 1 if txn.created_at.weekday() >= 5 else 0

        if merchant and merchant.registered_at:
            fv.merchant_tenure_days = max((txn.created_at - merchant.registered_at).days, 0)

        if user and user.updated_at:
            hours_since = (txn.created_at - user.updated_at).total_seconds() / 3600.0
            fv.hours_since_profile_update = max(hours_since, 0.0)

        # ── Day 6: time_since_last_txn ────────────────────────────
        last_txn_time_str = offline.get("last_txn_time")
        if last_txn_time_str:
            try:
                last_ts = datetime.fromisoformat(last_txn_time_str)
                fv.time_since_last_txn_secs = max(
                    (txn.created_at - last_ts).total_seconds(), 0.0
                )
            except (ValueError, TypeError):
                fv.time_since_last_txn_secs = 86400.0

        # Update velocity counter (fire-and-forget, async in production)
        self._increment_velocity(txn.user_id)

        return fv

    def _get_velocity(self, user_id: str, window: str) -> float:
        """Read velocity from Redis INCR counter."""
        if not self._redis_alive:
            return self._velocity_db_fallback(user_id, window)
        key = f"velocity:{user_id}:{window}"
        try:
            val = self._redis.get(key)
            return float(val) if val else 0.0
        except Exception:
            self._redis_alive = False
            return self._velocity_db_fallback(user_id, window)

    def _velocity_db_fallback(self, user_id: str, window: str) -> float:
        if self._txn_repo and window == "1h":
            return float(self._txn_repo.get_velocity(user_id, 1))
        return 0.0

    def _increment_velocity(self, user_id: str) -> None:
        """Increment velocity counters. TTL auto-expires the window."""
        if not self._redis_alive:
            return
        try:
            pipe = self._redis.pipeline()
            pipe.incr(f"velocity:{user_id}:1h");  pipe.expire(f"velocity:{user_id}:1h",  3600)
            pipe.incr(f"velocity:{user_id}:6h");  pipe.expire(f"velocity:{user_id}:6h",  21600)
            pipe.incr(f"velocity:{user_id}:24h"); pipe.expire(f"velocity:{user_id}:24h", 86400)
            pipe.execute()
        except Exception:
            self._redis_alive = False

    def _get_offline_features(self, user_id: str) -> dict:
        """
        Read batch-computed features from Redis hash.
        Set by pipeline-service/features/precompute.py daily.
        """
        if not self._redis_alive:
            return {}
        key = f"features:offline:{user_id}"
        try:
            raw = self._redis.get(key)
            if raw:
                return json.loads(raw)
        except Exception:
            self._redis_alive = False
        return {}
