"""
fraud_api/events/publisher.py
──────────────────────────────
EventPublisher interface + implementations.

MVP:        InMemoryPublisher (stores events in list)
Production: RedisStreamsPublisher or KafkaPublisher

Scaling rule: swap backend in main.py wire-up.
All observers stay the same.
"""

from abc import ABC, abstractmethod
from typing import List, Callable
from fraudshield_core.models import FraudEvent


# ─────────────────────────────────────────────
# Interface
# ─────────────────────────────────────────────

class EventPublisher(ABC):

    @abstractmethod
    def publish(self, event: FraudEvent) -> None:
        """Publish event to all subscribers."""
        ...


# ─────────────────────────────────────────────
# MVP Implementation: in-memory
# ─────────────────────────────────────────────

class InMemoryPublisher(EventPublisher):
    """
    Calls observers synchronously in-process.
    Good for MVP — simple, debuggable, no infrastructure.

    Production note: replace with RedisStreamsPublisher.
    Each observer becomes a separate consumer process.
    One observer failing doesn't affect others.
    """

    def __init__(self):
        self._observers: List[Callable[[FraudEvent], None]] = []
        self._events: List[FraudEvent] = []   # for testing / inspection

    def subscribe(self, handler: Callable[[FraudEvent], None]) -> None:
        self._observers.append(handler)

    def publish(self, event: FraudEvent) -> None:
        self._events.append(event)
        for handler in self._observers:
            try:
                handler(event)
            except Exception as ex:
                name = getattr(handler, "__name__", repr(handler))
                print(f"  [Publisher] Observer '{name}' failed: {ex}")

    @property
    def all_events(self) -> List[FraudEvent]:
        return list(self._events)


# ─────────────────────────────────────────────
# Production Implementation: Redis Streams
# ─────────────────────────────────────────────

class RedisStreamsPublisher(EventPublisher):
    """
    Publishes events to Redis Streams.
    Consumers subscribe via XREADGROUP.

    Uncomment and use when scaling beyond MVP.
    """

    def __init__(self, stream_name: str = "scored_transactions"):
        self._stream = stream_name
        # Import inline to avoid breaking MVP if redis-py not configured
        from fraudshield_core.redis_client import get_redis
        self._redis = get_redis()

    def publish(self, event: FraudEvent) -> None:
        import json
        try:
            self._redis.xadd(self._stream, {
                "transaction_id":  event.transaction_id,
                "user_id":         event.user_id,
                "amount":          str(event.amount),
                "score":           str(event.score),
                "decision":        event.decision.value,
                "reason_codes":    json.dumps(event.reason_codes),
                "model_version":   event.model_version,
                "latency_ms":      str(event.latency_ms),
                "scored_at":       event.scored_at.isoformat(),
            })
        except Exception as ex:
            print(f"  [RedisStreamsPublisher] Failed to publish to stream '{self._stream}': {ex}")
