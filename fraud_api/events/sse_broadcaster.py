"""
fraud_api/events/sse_broadcaster.py
─────────────────────────────────────
Pub/sub for Server-Sent Events.

Primary path  → Redis PUBLISH/SUBSCRIBE: works across multiple API replicas.
Fallback path → In-process asyncio.Queue list: used when Redis is unavailable.

broadcast() is always synchronous (called from the sync scoring path).
The SSE generator in main.py is async and chooses the path at connection time.
"""

import asyncio
import json
from typing import List

from fraudshield_core.redis_client import get_redis

CHANNEL = "fraudshield:live"


class SSEBroadcaster:
    def __init__(self):
        self._queues: List[asyncio.Queue] = []  # fallback when Redis is down

    def broadcast(self, data: dict) -> None:
        payload = json.dumps(data)
        try:
            get_redis().publish(CHANNEL, payload)
            return  # Redis delivered — async subscribers on all replicas receive it
        except Exception:
            pass
        # Redis unavailable — push to local in-process queues
        for q in list(self._queues):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass  # slow client — drop rather than block

    def subscribe_local(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=100)
        self._queues.append(q)
        return q

    def unsubscribe_local(self, q: asyncio.Queue) -> None:
        try:
            self._queues.remove(q)
        except ValueError:
            pass


broadcaster = SSEBroadcaster()
