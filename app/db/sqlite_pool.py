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
    transaction, so the open/closed state must be tracked at connection
    level instead of per adapter instance.
    """

    __slots__ = ("depth", "rollback_pending")

    def __init__(self) -> None:
        self.depth = 0
        self.rollback_pending = False

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

    async def execute(self, statement: str, *args: Any) -> str:
        query, values = _sql(statement, args)
        async with self._lock:
            cursor = await asyncio.to_thread(self._connection.execute, query, values)
            if not self._tx_state.active:
                await asyncio.to_thread(self._connection.commit)
            return f"OK {cursor.rowcount}"

    async def executemany(self, statement: str, args_list: list[tuple[Any, ...]]) -> None:
        query, _ = _sql(statement)
        async with self._lock:
            await asyncio.to_thread(self._connection.executemany, query, args_list)
            if not self._tx_state.active:
                await asyncio.to_thread(self._connection.commit)

    async def fetch(self, statement: str, *args: Any) -> list[dict[str, Any]]:
        query, values = _sql(statement, args)
        async with self._lock:
            cursor = await asyncio.to_thread(self._connection.execute, query, values)
            rows = await asyncio.to_thread(cursor.fetchall)
            return [dict(row) for row in rows]

    async def fetchrow(self, statement: str, *args: Any) -> dict[str, Any] | None:
        rows = await self.fetch(statement, *args)
        return rows[0] if rows else None

    async def fetchval(self, statement: str, *args: Any) -> Any:
        row = await self.fetchrow(statement, *args)
        return next(iter(row.values())) if row else None

    def transaction(self) -> "SQLiteTransaction":
        return SQLiteTransaction(self)

    async def _begin(self) -> None:
        async with self._lock:
            if self._tx_state.active:
                # Nested or concurrent BEGIN reuses the already-open
                # transaction; only the reference count grows.
                self._tx_state.depth += 1
                return
            await asyncio.to_thread(self._connection.execute, "BEGIN")
            self._tx_state.depth = 1

    async def _finish(self, rollback: bool) -> None:
        async with self._lock:
            if not self._tx_state.active:
                return
            self._tx_state.depth -= 1
            if self._tx_state.depth > 0:
                # A failed inner scope must not be committed by the outer
                # scope, so mark the shared transaction rollback-only.
                self._tx_state.rollback_pending = self._tx_state.rollback_pending or rollback
                return
            perform_rollback = rollback or self._tx_state.rollback_pending
            self._tx_state.rollback_pending = False
            operation = self._connection.rollback if perform_rollback else self._connection.commit
            await asyncio.to_thread(operation)

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._connection.close)


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
            await asyncio.to_thread(self._connection.close)
            self._connection = None
