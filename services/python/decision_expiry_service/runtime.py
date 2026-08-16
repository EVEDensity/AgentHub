from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.config import DATABASE_URL
from app.db.session import aclose_pool, aget_pool
from app.repositories import MissionRepository
from app.services.decision_expiry_supervisor import (
    DecisionExpirySupervisor,
    DecisionExpirySupervisorSnapshot,
)
from app.services.mission_service import MissionService

from .config import DecisionExpiryServiceSettings

AsyncStartupHook = Callable[[], Awaitable[object]]
AsyncShutdownHook = Callable[[], Awaitable[None]]


class DecisionExpirySupervisorPort(Protocol):
    @property
    def snapshot(self) -> DecisionExpirySupervisorSnapshot: ...

    async def run(self) -> None: ...

    def request_stop(self) -> None: ...


@dataclass(slots=True)
class DecisionExpiryServiceRuntime:
    """Own process lifecycle around a stateless Mission Control supervisor."""

    worker: DecisionExpirySupervisorPort
    shutdown_timeout_seconds: float
    initialize_database: AsyncStartupHook | None = None
    close_database: AsyncShutdownHook | None = None
    _worker_task: asyncio.Task[None] | None = field(default=None, init=False)
    _database_initialized: bool = field(default=False, init=False)

    @property
    def snapshot(self) -> DecisionExpirySupervisorSnapshot:
        return self.worker.snapshot

    @property
    def healthy(self) -> bool:
        task = self._worker_task
        return task is not None and not task.done()

    @property
    def ready(self) -> bool:
        snapshot = self.snapshot
        return self.healthy and snapshot.running and snapshot.ready

    async def start(self) -> None:
        if self._worker_task is not None:
            raise RuntimeError("Decision expiry service runtime is already started")
        try:
            if self.initialize_database is not None:
                await self.initialize_database()
                self._database_initialized = True
            self._worker_task = asyncio.create_task(
                self.worker.run(),
                name="agenthub-decision-expiry-supervisor",
            )
            await asyncio.sleep(0)
            if self._worker_task.done():
                await self._worker_task
                raise RuntimeError("Decision expiry supervisor stopped during startup")
        except BaseException:
            await self._close_database()
            raise

    async def stop(self) -> None:
        task = self._worker_task
        try:
            if task is not None and not task.done():
                self.worker.request_stop()
                try:
                    await asyncio.wait_for(
                        asyncio.shield(task),
                        timeout=self.shutdown_timeout_seconds,
                    )
                except TimeoutError:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            elif task is not None:
                await asyncio.gather(task, return_exceptions=True)
        finally:
            await self._close_database()

    async def _close_database(self) -> None:
        if self._database_initialized and self.close_database is not None:
            self._database_initialized = False
            await self.close_database()


def build_decision_expiry_runtime(
    settings: DecisionExpiryServiceSettings,
) -> DecisionExpiryServiceRuntime:
    """Compose the supervisor directly over Mission Control persistence."""

    if not DATABASE_URL.strip():
        raise ValueError("DATABASE_URL must be configured for Decision expiry")
    command = MissionService(MissionRepository())
    worker = DecisionExpirySupervisor(
        command,
        idle_delay_seconds=settings.idle_delay_seconds,
        max_delay_seconds=settings.max_delay_seconds,
    )
    return DecisionExpiryServiceRuntime(
        worker=worker,
        shutdown_timeout_seconds=settings.shutdown_timeout_seconds,
        initialize_database=aget_pool,
        close_database=aclose_pool,
    )


__all__ = [
    "DecisionExpiryServiceRuntime",
    "build_decision_expiry_runtime",
]
