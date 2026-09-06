"""Low-latency Mission event notifications for streaming consumers.

The durable event ledger remains the source of truth.  This bus is only a
process-local wake-up channel: consumers always reread the ledger after a
notification, so dropped/coalesced notifications cannot lose events.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

logger = logging.getLogger("agenthub.mission_event_bus")


class MissionEventNotifier:
    """Cross-process notification contract; implementations are best-effort."""

    async def publish(self, mission_id: str) -> None:  # pragma: no cover - protocol default
        raise NotImplementedError


class PostgresMissionEventNotifier(MissionEventNotifier):
    """PostgreSQL LISTEN/NOTIFY bridge to the local coalescing bus."""

    channel = "agenthub_mission_events"

    def __init__(self, database_url: str, bus: "MissionEventBus") -> None:
        self.database_url = database_url
        self.bus = bus
        self._connection = None
        self._task: asyncio.Task | None = None

    async def publish(self, mission_id: str) -> None:
        if not mission_id:
            return
        try:
            import asyncpg
            conn = await asyncpg.connect(self.database_url)
            try:
                await conn.execute("SELECT pg_notify($1, $2)", self.channel, mission_id)
            finally:
                await conn.close()
        except Exception:
            logger.debug("postgres notify unavailable", exc_info=True)

    async def start(self) -> None:
        import asyncpg
        self._connection = await asyncpg.connect(self.database_url)
        await self._connection.add_listener(self.channel, self._on_notify)

    async def stop(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    def _on_notify(self, _connection, _pid: int, _channel: str, payload: str) -> None:
        mission_id = str(payload or "").strip()
        if mission_id:
            asyncio.create_task(self.bus.notify(mission_id))


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


__all__ = ["MissionEventBus", "MissionEventNotifier", "PostgresMissionEventNotifier", "mission_event_bus"]
