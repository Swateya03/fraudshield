"""
fraud_api/events/sse_broadcaster.py
─────────────────────────────────────
In-process pub/sub for Server-Sent Events.

Each connected LiveFeed client gets its own asyncio.Queue.
broadcast() is called synchronously from within the async
score_transaction endpoint — safe because we're in the event loop.
"""

import asyncio
from typing import List


class SSEBroadcaster:
    def __init__(self):
        self._queues: List[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=100)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._queues.remove(q)
        except ValueError:
            pass

    def broadcast(self, data: dict) -> None:
        for q in list(self._queues):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass  # slow client — skip rather than block


broadcaster = SSEBroadcaster()
