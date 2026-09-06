"""Low-latency Mission event notifications for streaming consumers.

The durable event ledger remains the source of truth.  This bus is only a
process-local wake-up channel: consumers always reread the ledger after a
notification, so dropped/coalesced notifications cannot lose events.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class MissionEventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[None]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def subscribe(self, mission_id: str) -> AsyncIterator[asyncio.Queue[None]]:
        queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        async with self._lock:
            self._subscribers[mission_id].add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                subscribers = self._subscribers.get(mission_id)
                if subscribers is not None:
                    subscribers.discard(queue)
                    if not subscribers:
                        self._subscribers.pop(mission_id, None)

    async def notify(self, mission_id: str) -> None:
        if not mission_id:
            return
        async with self._lock:
            subscribers = tuple(self._subscribers.get(mission_id, ()))
        for queue in subscribers:
            # A single queued marker is sufficient.  Multiple writes are
            # coalesced and the subscriber drains the durable ledger.
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass


mission_event_bus = MissionEventBus()


__all__ = ["MissionEventBus", "mission_event_bus"]
