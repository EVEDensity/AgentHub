"""Small async-compatible SQLite adapter for the local desktop profile."""

from __future__ import annotations

import asyncio
import re
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

_PARAMETER = re.compile(r"\$(\d+)")
_CAST = re.compile(r"::[a-zA-Z_][a-zA-Z0-9_]*")
# PostgreSQL row-lock clauses have no SQLite equivalent; the local profile's
# single serialized connection already orders concurrent access.
_ROW_LOCK = re.compile(r"\bFOR\s+UPDATE\b[^;]*", re.IGNORECASE)


def _sql(statement: str, args: tuple[Any, ...] = ()) -> tuple[str, tuple[Any, ...]]:
    statement = _ROW_LOCK.sub("", statement)
    statement = _CAST.sub("", statement)
    positions: list[int] = []

    def replace(match: re.Match[str]) -> str:
        positions.append(int(match.group(1)) - 1)
        return "?"

    converted = _PARAMETER.sub(replace, statement)
    return converted, tuple(args[index] for index in positions)


class _SharedTransactionState:
    """Transaction bookkeeping for one underlying sqlite3 connection.

    ``SQLitePool.acquire()`` hands a fresh adapter to every caller while all
    of them share a single serialized ``sqlite3.Connection`` and its one
    transaction. Ownership belongs to one asyncio task; only that task can
    nest scopes. Other tasks wait at the shared gate, including plain reads
    and writes outside transaction contexts.
    """

    __slots__ = ("depth", "gate", "owner", "rollback_pending")

    def __init__(self) -> None:
        self.depth = 0
        self.rollback_pending = False
        self.owner: asyncio.Task | None = None
        self.gate = asyncio.Lock()

    @property
    def active(self) -> bool:
        return self.depth > 0


class SQLiteConnection:
    def __init__(
        self,
        connection: sqlite3.Connection,
        lock: asyncio.Lock,
        state: _SharedTransactionState | None = None,
    ) -> None:
        self._connection = connection
        self._lock = lock
        self._tx_state = state if state is not None else _SharedTransactionState()

    @asynccontextmanager
    async def _operation(self):
        # Ownership is the actual task, not inherited ContextVar state. A
        # child task must wait for its parent's transaction to finish.
        if self._tx_state.owner is asyncio.current_task():
            async with self._lock:
                yield
        else:
            async with self._tx_state.gate, self._lock:
                try:
                    yield
                except BaseException:
                    # A cancelled worker may have finished a write in its
                    # thread. Clear that implicit transaction before releasing
                    # the connection to another task.
                    await self._run(self._connection.rollback)
                    raise

    @staticmethod
    async def _run(operation, *args):
        # Cancellation cannot release the connection while a thread still
        # operates on it. Finish the SQLite call, then propagate cancellation.
        task = asyncio.create_task(asyncio.to_thread(operation, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            task.result()
            raise

    async def execute(self, statement: str, *args: Any) -> str:
        query, values = _sql(statement, args)
        async with self._operation():
            cursor = await self._run(self._connection.execute, query, values)
            if not self._tx_state.active:
                await self._run(self._connection.commit)
            return f"OK {cursor.rowcount}"

    async def executemany(self, statement: str, args_list: list[tuple[Any, ...]]) -> None:
        query, _ = _sql(statement)
        async with self._operation():
            await self._run(self._connection.executemany, query, args_list)
            if not self._tx_state.active:
                await self._run(self._connection.commit)

    async def fetch(self, statement: str, *args: Any) -> list[dict[str, Any]]:
        query, values = _sql(statement, args)
        async with self._operation():
            cursor = await self._run(self._connection.execute, query, values)
            rows = await self._run(cursor.fetchall)
            if not self._tx_state.active:
                await self._run(self._connection.commit)
            return [dict(row) for row in rows]

    async def fetchrow(self, statement: str, *args: Any) -> dict[str, Any] | None:
        rows = await self.fetch(statement, *args)
        return rows[0] if rows else None

    async def fetchval(self, statement: str, *args: Any) -> Any:
        row = await self.fetchrow(statement, *args)
        return next(iter(row.values())) if row else None

    def transaction(self) -> SQLiteTransaction:
        return SQLiteTransaction(self)

    async def _begin(self) -> None:
        if self._tx_state.owner is asyncio.current_task():
            self._tx_state.depth += 1
            return
        await self._tx_state.gate.acquire()
        try:
            async with self._lock:
                try:
                    await self._run(self._connection.execute, "BEGIN")
                except BaseException:
                    await self._run(self._connection.rollback)
                    raise
                self._tx_state.owner = asyncio.current_task()
                self._tx_state.depth = 1
        except BaseException:
            self._tx_state.gate.release()
            raise

    async def _finish(self, rollback: bool) -> None:
        async with self._lock:
            if not self._tx_state.active:
                return
            if self._tx_state.owner is not asyncio.current_task():
                raise RuntimeError("SQLite transaction must finish in its owning task")
            self._tx_state.depth -= 1
            if self._tx_state.depth > 0:
                # A failed inner scope must not be committed by the outer
                # scope, so mark the shared transaction rollback-only.
                self._tx_state.rollback_pending = self._tx_state.rollback_pending or rollback
                return
            perform_rollback = rollback or self._tx_state.rollback_pending
            operation = self._connection.rollback if perform_rollback else self._connection.commit
            try:
                await self._run(operation)
            finally:
                self._tx_state.rollback_pending = False
                self._tx_state.owner = None
                self._tx_state.gate.release()

    async def close(self) -> None:
        if self._tx_state.owner is asyncio.current_task():
            raise RuntimeError("Cannot close SQLite during the current task's transaction")
        async with self._tx_state.gate, self._lock:
            await self._run(self._connection.close)


class SQLiteTransaction:
    def __init__(self, connection: SQLiteConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> SQLiteConnection:
        await self._connection._begin()
        return self._connection

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self._connection._finish(exc_type is not None)


class SQLitePool:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._connection: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()
        self._tx_state = _SharedTransactionState()

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await asyncio.to_thread(
            sqlite3.connect,
            self.path,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._tx_state = _SharedTransactionState()
        async with self._lock:
            await asyncio.to_thread(self._connection.execute, "PRAGMA foreign_keys = ON")
            # WAL + synchronous=NORMAL (P3-2a): durable-but-fast local profile.
            # Both pragmas are idempotent; WAL survives across connections so
            # re-opening the same database keeps the mode.
            await asyncio.to_thread(
                self._connection.execute, "PRAGMA journal_mode = WAL"
            )
            await asyncio.to_thread(
                self._connection.execute, "PRAGMA synchronous = NORMAL"
            )
            await asyncio.to_thread(
                self._connection.execute,
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)",
            )
            await asyncio.to_thread(self._connection.commit)

    @asynccontextmanager
    async def acquire(self):
        if self._connection is None:
            raise RuntimeError("SQLite pool is not initialized")
        yield SQLiteConnection(self._connection, self._lock, self._tx_state)

    async def close(self) -> None:
        if self._connection is not None:
            await SQLiteConnection(self._connection, self._lock, self._tx_state).close()
            self._connection = None
