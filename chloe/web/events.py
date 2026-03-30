"""Server-Sent Events broadcaster for live dashboard updates."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator


class EventBroadcaster:
    """Fan-out SSE events to all connected clients."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers = [s for s in self._subscribers if s is not q]

    async def publish(self, event_type: str, data: dict) -> None:
        payload = json.dumps(data)
        for q in self._subscribers:
            await q.put((event_type, payload))

    async def stream(self, q: asyncio.Queue) -> AsyncGenerator[str, None]:
        try:
            while True:
                event_type, payload = await q.get()
                yield f"event: {event_type}\ndata: {payload}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            self.unsubscribe(q)
