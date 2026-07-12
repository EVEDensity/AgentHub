"""asyncpg pool adapter — local PostgreSQL.

Mirrors the :class:`NeonHttpPool` / :class:`NeonHttpConnection` API so
``session.py`` can swap between Neon HTTP SQL (cloud) and asyncpg (local)
transparently.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

logger = logging.getLogger("agenthub.db.asyncpg")

# ═══════════════════════════════════════════════════════════════════════
# Connection wrapper — mirrors NeonHttpConnection API
# ═══════════════════════════════════════════════════════════════════════


class AsyncPgConnection:
    """Wraps ``asyncpg.Connection`` to match NeonHttpConnection's API."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn
        self._closed = False

    # ── Query API (matches NeonHttpConnection) ─────────────────────────

    async def execute(self, sql: str, *args: Any) -> str:
        """Execute a statement and return the command tag (e.g. 'INSERT 0 1')."""
        self._check_open()
        result = await self._conn.execute(sql, *args)
        return result

    async def executemany(self, sql: str, args_list: list[tuple[Any, ...]]) -> None:
        """Execute a statement with many parameter sets."""
        self._check_open()
        await self._conn.executemany(sql, args_list)

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        """Execute a SELECT and return all rows as dicts."""
        self._check_open()
        rows = await self._conn.fetch(sql, *args)
        return [dict(r) for r in rows]

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        """Execute a SELECT and return the first row as a dict, or None."""
        self._check_open()
        row = await self._conn.fetchrow(sql, *args)
        return dict(row) if row is not None else None

    async def fetchval(self, sql: str, *args: Any) -> Any:
        """Execute a SELECT and return the first value of the first row."""
        self._check_open()
        return await self._conn.fetchval(sql, *args)

    def transaction(self):
        """Return an async context manager for a transaction block."""
        return _AsyncPgTransaction(self)

    async def close(self) -> None:
        """Release the underlying connection back to the pool."""
        self._closed = True
        await self._conn.close()

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("Connection is closed")

    # ── Context manager ───────────────────────────────────────────────

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


# ═══════════════════════════════════════════════════════════════════════
# Transaction emulation — mirrors _NeonHttpTransaction
# ═══════════════════════════════════════════════════════════════════════


class _AsyncPgTransaction:
    """Async context manager for transactions — mirrors _NeonHttpTransaction."""

    def __init__(self, conn: AsyncPgConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> AsyncPgConnection:
        await self._conn.execute("BEGIN")
        return self._conn

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            try:
                await self._conn.execute("ROLLBACK")
            except Exception:
                logger.warning("asyncpg: ROLLBACK failed", exc_info=True)
        else:
            await self._conn.execute("COMMIT")


# ═══════════════════════════════════════════════════════════════════════
# Pool wrapper — mirrors NeonHttpPool API
# ═══════════════════════════════════════════════════════════════════════


class AsyncPgPool:
    """An ``asyncpg.Pool`` wrapper that mirrors NeonHttpPool's API.

    Usage::

        pool = AsyncPgPool()
        await pool.initialize(database_url)

        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT $1::text AS hello", "world")
    """

    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None
        self._initialized = False
        self._database_url: str = ""

    async def initialize(self, database_url: str) -> None:
        """Create the asyncpg connection pool (call once on app startup)."""
        try:
            self._pool = await asyncpg.create_pool(
                database_url,
                min_size=2,
                max_size=20,
                command_timeout=60,
            )
        except Exception as exc:
            logger.error("asyncpg: pool creation failed: %s", exc)
            raise

        # Quick connectivity check
        async with self._pool.acquire() as conn:
            ver = await conn.fetchval("SELECT version()")
            logger.info("asyncpg: connected (pool min=2 max=20) — %s", ver)

        self._initialized = True
        self._database_url = database_url

    @asynccontextmanager
    async def acquire(self):
        """Yield an AsyncPgConnection from the pool."""
        if self._pool is None:
            raise RuntimeError("Pool not initialized — call await pool.initialize(url) first")
        async with self._pool.acquire() as raw_conn:
            wrapped = AsyncPgConnection(raw_conn)
            try:
                yield wrapped
            finally:
                pass  # raw_conn is released by the inner context manager

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
        self._initialized = False
        logger.info("asyncpg: pool closed")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


# ═══════════════════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════════════════

_pool: AsyncPgPool | None = None
_pool_lock = asyncio.Lock()


async def get_asyncpg_pool(database_url: str) -> AsyncPgPool:
    """Get or create the global AsyncPgPool singleton."""
    global _pool
    if _pool is not None:
        return _pool

    async with _pool_lock:
        if _pool is not None:
            return _pool
        _pool = AsyncPgPool()
        await _pool.initialize(database_url)
        return _pool


async def close_asyncpg_pool() -> None:
    """Shut down the asyncpg pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
