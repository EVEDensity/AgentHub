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

    def __init__(
        self,
        database_url: str,
        bus: "MissionEventBus",
        *,
        connect_timeout: float = 10.0,
        reconnect_min_seconds: float = 0.25,
        reconnect_max_seconds: float = 5.0,
        connect: object | None = None,
    ) -> None:
        if connect_timeout <= 0:
            raise ValueError("connect_timeout must be positive")
        if reconnect_min_seconds <= 0 or reconnect_max_seconds < reconnect_min_seconds:
            raise ValueError("invalid reconnect interval")
        self.database_url = database_url
        self.bus = bus
        self._connect_timeout = connect_timeout
        self._reconnect_min_seconds = reconnect_min_seconds
        self._reconnect_max_seconds = reconnect_max_seconds
        self._connect_override = connect
        self._connection = None
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._reconnect = asyncio.Event()

    async def publish(self, mission_id: str) -> None:
        if not mission_id:
            return
        try:
            conn = await self._connect()
            try:
                await conn.execute("SELECT pg_notify($1, $2)", self.channel, mission_id)
            finally:
                await conn.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "postgres notify unavailable for mission %s: %s",
                mission_id,
                type(exc).__name__,
            )

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._ready.clear()
        self._stop.clear()
        self._reconnect.clear()
        self._task = asyncio.create_task(
            self._listen_loop(), name="agenthub-postgres-mission-events"
        )
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self._connect_timeout)
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        self._stop.set()
        self._reconnect.set()
        task = self._task
        self._task = None
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=self._connect_timeout)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                pass
        await self._close_connection()

    def _on_notify(self, _connection, _pid: int, _channel: str, payload: str) -> None:
        mission_id = str(payload or "").strip()
        if mission_id:
            asyncio.create_task(self.bus.notify(mission_id))

    def _on_termination(self, _connection) -> None:
        self._reconnect.set()

    async def _connect(self):
        if self._connect_override is not None:
            return await self._connect_override(self.database_url)  # type: ignore[operator]
        import asyncpg

        return await asyncpg.connect(
            self.database_url,
            timeout=self._connect_timeout,
        )

    async def _listen_loop(self) -> None:
        delay = self._reconnect_min_seconds
        while not self._stop.is_set():
            try:
                connection = await self._connect()
                self._connection = connection
                self._reconnect.clear()
                await connection.add_listener(self.channel, self._on_notify)
                add_termination_listener = getattr(
                    connection, "add_termination_listener", None
                )
                if callable(add_termination_listener):
                    add_termination_listener(self._on_termination)
                self._ready.set()
                delay = self._reconnect_min_seconds
                await self._reconnect.wait()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "postgres mission listener disconnected: %s; retrying in %.2fs",
                    type(exc).__name__,
                    delay,
                )
            finally:
                await self._close_connection()
            if self._stop.is_set():
                return
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, self._reconnect_max_seconds)

    async def _close_connection(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is None:
            return
        try:
            await connection.close()
        except Exception:
            logger.debug("postgres mission listener close failed", exc_info=True)


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
