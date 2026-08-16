from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.db.asyncpg_pool import AsyncPgPool
from app.repositories import MissionRepository
from app.services.decision_expiry_supervisor import (
    DecisionExpirySupervisor,
    DecisionExpirySupervisorSnapshot,
)
from app.services.mission_service import MissionService

from .config import DecisionExpiryServiceSettings, read_database_url_file

AsyncStartupHook = Callable[[], Awaitable[object]]
AsyncShutdownHook = Callable[[], Awaitable[None]]


class DatabaseConnectionPort(Protocol):
    async def execute(self, sql: str, *args: Any) -> Any: ...

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]: ...

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None: ...

    def transaction(self) -> AbstractAsyncContextManager[Any]: ...


class DatabasePoolPort(Protocol):
    async def initialize(self, database_url: str) -> None: ...

    def acquire(self) -> AbstractAsyncContextManager[DatabaseConnectionPort]: ...

    async def close(self) -> None: ...


class DecisionExpiryDatabase:
    """Own a direct PostgreSQL pool with real connection-level transactions."""

    def __init__(self, database_url: str, *, pool: DatabasePoolPort | None = None):
        self._database_url = database_url
        self._pool = pool or AsyncPgPool()
        self._initialization_attempted = False

    async def initialize(self) -> None:
        if self._initialization_attempted:
            raise RuntimeError("Decision expiry database initialization was repeated")
        self._initialization_attempted = True
        await self._pool.initialize(self._database_url)

    async def close(self) -> None:
        await self._pool.close()

    async def execute(self, sql: str, *args: Any) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute(sql, *args)

    async def fetch_one(self, sql: str, *args: Any) -> dict[str, Any] | None:
        async with self._pool.acquire() as connection:
            return await connection.fetchrow(sql, *args)

    async def fetch_all(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        async with self._pool.acquire() as connection:
            return await connection.fetch(sql, *args)

    @asynccontextmanager
    async def transaction(self):
        async with self._pool.acquire() as connection, connection.transaction():
            yield connection


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
                self._database_initialized = True
                await self.initialize_database()
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
    *,
    pool: DatabasePoolPort | None = None,
) -> DecisionExpiryServiceRuntime:
    """Compose the supervisor directly over Mission Control persistence."""

    database = DecisionExpiryDatabase(
        read_database_url_file(settings.database_url_file),
        pool=pool,
    )
    repository = MissionRepository(
        execute=database.execute,
        fetch_one=database.fetch_one,
        fetch_all=database.fetch_all,
        transaction_factory=database.transaction,
    )
    command = MissionService(repository)
    worker = DecisionExpirySupervisor(
        command,
        idle_delay_seconds=settings.idle_delay_seconds,
        max_delay_seconds=settings.max_delay_seconds,
    )
    return DecisionExpiryServiceRuntime(
        worker=worker,
        shutdown_timeout_seconds=settings.shutdown_timeout_seconds,
        initialize_database=database.initialize,
        close_database=database.close,
    )


__all__ = [
    "DecisionExpiryDatabase",
    "DecisionExpiryServiceRuntime",
    "build_decision_expiry_runtime",
]
