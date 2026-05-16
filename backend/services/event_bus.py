"""
Server-Sent Events (SSE) bus.
All upload progress, floodwait, and log events flow through here to the frontend.
"""

import asyncio
import json
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List


class EventBus:
    """
    In-process pub/sub bus for SSE delivery.
    Multiple SSE clients subscribe via async queues.
    """

    def __init__(self):
        self._subscribers: List[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=512)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    async def emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        payload["event_type"] = event_type
        payload["timestamp"] = datetime.utcnow().isoformat()
        data = json.dumps(payload, default=str)

        dead = []
        for q in self._subscribers:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                dead.append(q)  # slow subscriber — disconnect

        for q in dead:
            self.unsubscribe(q)

    async def stream(self, q: asyncio.Queue) -> AsyncGenerator[str, None]:
        """Async generator for FastAPI StreamingResponse."""
        try:
            while True:
                data = await asyncio.wait_for(q.get(), timeout=30)
                yield f"data: {data}\n\n"
        except asyncio.TimeoutError:
            # Send keepalive ping
            yield ": ping\n\n"
        except asyncio.CancelledError:
            return


# Global singleton
_event_bus: EventBus = EventBus()


def get_event_bus() -> EventBus:
    return _event_bus
