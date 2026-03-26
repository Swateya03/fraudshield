"""
fraudshield_core/redis_client.py
──────────────────────
Redis connection singleton.

PACELC decision (from slides 39):
  Velocity reads/writes → EL (eventual consistency, low latency)
  We accept 200ms replication lag on velocity counters.
  Speed matters more than perfect consistency here.

  Risk tier reads → we DON'T use Redis for this.
  We use PostgreSQL (EC) because a missed block is fraud.
"""

import redis
from fraudshield_core.config import config

# Single connection pool — shared across the application
_client: redis.Redis = None


def get_redis() -> redis.Redis:
    """
    Returns the shared Redis client.
    Creates it on first call (lazy initialization).
    """
    global _client
    if _client is None:
        _client = redis.from_url(
            config.REDIS_URL,
            decode_responses=True,   # strings, not bytes
            socket_connect_timeout=2,
            socket_timeout=2,
            retry_on_timeout=True,
        )
    return _client


def ping() -> bool:
    """Health check — returns True if Redis is reachable."""
    try:
        return get_redis().ping()
    except Exception:
        return False
