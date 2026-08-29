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


class SQLiteConnection:
    def __init__(self, connection: sqlite3.Connection, lock: asyncio.Lock) -> None:
        self._connection = connection
        self._lock = lock
        self._in_transaction = False

    async def execute(self, statement: str, *args: Any) -> str:
        query, values = _sql(statement, args)
        async with self._lock:
            cursor = await asyncio.to_thread(self._connection.execute, query, values)
            if not self._in_transaction:
                await asyncio.to_thread(self._connection.commit)
            return f"OK {cursor.rowcount}"

    async def executemany(self, statement: str, args_list: list[tuple[Any, ...]]) -> None:
        query, _ = _sql(statement)
        async with self._lock:
            await asyncio.to_thread(self._connection.executemany, query, args_list)
            if not self._in_transaction:
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
            await asyncio.to_thread(self._connection.execute, "BEGIN")
            self._in_transaction = True

    async def _finish(self, rollback: bool) -> None:
        async with self._lock:
            operation = self._connection.rollback if rollback else self._connection.commit
            await asyncio.to_thread(operation)
            self._in_transaction = False

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

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await asyncio.to_thread(
            sqlite3.connect,
            self.path,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        async with self._lock:
            await asyncio.to_thread(self._connection.execute, "PRAGMA foreign_keys = ON")
            await asyncio.to_thread(
                self._connection.execute,
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)",
            )
            await asyncio.to_thread(self._connection.commit)

    @asynccontextmanager
    async def acquire(self):
        if self._connection is None:
            raise RuntimeError("SQLite pool is not initialized")
        yield SQLiteConnection(self._connection, self._lock)

    async def close(self) -> None:
        if self._connection is not None:
            await asyncio.to_thread(self._connection.close)
            self._connection = None
