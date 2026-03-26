"""
ml_pipeline/features/feature_store.py
──────────────────────────────────────
FeatureStore interface + implementations.

MVP:        RedisFeatureStore (Redis hash per user)
Production: FeastFeatureStore (swap one line in main.py)

PACELC choice (slide 39):
  EL — eventual consistency, low latency.
  Offline features updated daily by pipeline.
  200ms stale is acceptable for velocity/history features.
  risk_tier is NOT stored here — that's EC (PostgreSQL).
"""

from abc import ABC, abstractmethod
from typing import Optional
import json


class FeatureStore(ABC):

    @abstractmethod
    def get_offline_features(self, user_id: str) -> dict:
        """
        Returns batch-computed features for a user.
        Updated daily by pipeline-service.
        """
        ...

    @abstractmethod
    def set_offline_features(self, user_id: str, features: dict,
                              ttl_seconds: int = 86400 * 2) -> None:
        """Store offline features. TTL = 2 days (re-computed daily)."""
        ...


class RedisFeatureStore(FeatureStore):
    """
    Redis-backed feature store.
    Key pattern: features:offline:{user_id}
    Value:       JSON blob with all offline features

    In production upgrade path:
      → Feast uses Redis as its online store backend
      → Same Redis, different client (Feast SDK)
      → Zero data migration needed
    """

    def __init__(self, redis_client=None):
        if redis_client is None:
            from fraudshield_core.redis_client import get_redis
            self._redis = get_redis()
        else:
            self._redis = redis_client

    def get_offline_features(self, user_id: str) -> dict:
        key = f"features:offline:{user_id}"
        try:
            raw = self._redis.get(key)
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    def set_offline_features(self, user_id: str, features: dict,
                              ttl_seconds: int = 86400 * 2) -> None:
        key = f"features:offline:{user_id}"
        try:
            self._redis.setex(key, ttl_seconds, json.dumps(features))
        except Exception as e:
            print(f"  [FeatureStore] Failed to write {user_id}: {e}")
