from __future__ import annotations

"""Database session layer — auto-detects Neon cloud vs local PostgreSQL.

- ``DATABASE_URL`` containing ``neon.tech`` → Neon HTTP SQL (firewall bypass)
- ``DATABASE_URL`` containing ``127.0.0.1`` / ``localhost`` → asyncpg (direct TCP)

API surface (unchanged):
  aget_pool()                → NeonHttpPool | AsyncPgPool
  aclose_pool()              → None (graceful shutdown)
  afetch_all(sql, *args)     → list[dict]
  afetch_one(sql, *args)     → dict | None
  aexecute(sql, *args)       → None
  aexecute_insert(sql, *args)→ str (new row id — SQL must include RETURNING)
  aexecute_many(sql, list)   → None
  atransaction()             → async context manager → connection
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from app.config import DATABASE_URL, DB_BACKEND, SQLITE_PATH
from app.db.sqlite_pool import SQLitePool

logger = logging.getLogger("agenthub.db")

# Union type for the pool
PoolType = Any

# ═══════════════════════════════════════════════════════════════════════
# Auto-detection
# ═══════════════════════════════════════════════════════════════════════


def _is_local_db(url: str) -> bool:
    """Return True if the DATABASE_URL points to a local PostgreSQL instance."""
    if not url:
        return False
    url_lower = url.lower()
    return (
        "127.0.0.1" in url_lower
        or "localhost" in url_lower
        or "::1" in url_lower
    )


def _is_neon_cloud(url: str) -> bool:
    """Return True if the DATABASE_URL points to a Neon cloud instance."""
    if not url:
        return False
    return "neon.tech" in url.lower()


# ═══════════════════════════════════════════════════════════════════════
# Connection pool
# ═══════════════════════════════════════════════════════════════════════

_pool: PoolType | None = None
_sqlite_pool: SQLitePool | None = None
_pool_lock = asyncio.Lock()


async def aget_pool() -> PoolType:
    """Return the database pool (lazy-init, thread-safe).

    Auto-detects local vs Neon cloud based on DATABASE_URL.
    Raises RuntimeError if DATABASE_URL is not configured.
    """
    global _pool, _sqlite_pool

    use_sqlite = DB_BACKEND == "sqlite" or (
        DB_BACKEND == "auto" and not DATABASE_URL
    )
    if use_sqlite:
        if _sqlite_pool is None:
            _sqlite_pool = SQLitePool(SQLITE_PATH)
            await _sqlite_pool.initialize()
        return _sqlite_pool  # type: ignore[return-value]

    from app.db.asyncpg_pool import get_asyncpg_pool
    from app.db.neon_http import get_neon_http_pool

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set — PostgreSQL is required. "
            "Create a .env file with DATABASE_URL=postgresql://..."
        )

    if _pool is not None:
        return _pool

    async with _pool_lock:
        if _pool is not None:
            return _pool

        if _is_local_db(DATABASE_URL):
            logger.info("db: detected local PostgreSQL → using asyncpg")
            _pool = await get_asyncpg_pool(DATABASE_URL)
        elif _is_neon_cloud(DATABASE_URL):
            logger.info("db: detected Neon cloud → using HTTP SQL")
            _pool = await get_neon_http_pool(DATABASE_URL)
        else:
            # Default to asyncpg for non-Neon remote hosts
            logger.info("db: remote PostgreSQL → using asyncpg")
            _pool = await get_asyncpg_pool(DATABASE_URL)

        return _pool


def is_sqlite_backend() -> bool:
    """Return True when the configured backend is the local SQLite adapter.

    Mirrors the branch condition of :func:`aget_pool` without initializing
    a pool, so request paths and tests can branch on the dialect safely.
    """
    return DB_BACKEND == "sqlite" or (DB_BACKEND == "auto" and not DATABASE_URL)


async def aclose_pool() -> None:
    """Close the database pool gracefully (call during app shutdown)."""
    global _pool, _sqlite_pool
    if _sqlite_pool is not None:
        await _sqlite_pool.close()
        _sqlite_pool = None
    if _pool is not None:
        logger.info("db: closing pool...")
        await _pool.close()
        _pool = None
        logger.info("db: pool closed.")


# ═══════════════════════════════════════════════════════════════════════
# Async query API
#
# Each call acquires a fresh connection from the pool.
# ═══════════════════════════════════════════════════════════════════════


async def afetch_all(sql: str, *args: Any) -> list[dict[str, Any]]:
    """Execute a SELECT and return all rows as dicts."""
    pool = await aget_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(sql, *args)


async def afetch_one(sql: str, *args: Any) -> dict[str, Any] | None:
    """Execute a SELECT and return the first row as a dict, or None."""
    pool = await aget_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(sql, *args)


async def aexecute(sql: str, *args: Any) -> None:
    """Execute an INSERT / UPDATE / DELETE statement."""
    pool = await aget_pool()
    async with pool.acquire() as conn:
        await conn.execute(sql, *args)


def _first_column(row: Any) -> str:
    """Return the row's first column as a string.

    asyncpg ``Record`` supports positional access (``row[0]``); the sqlite
    wrapper returns plain dicts (``dict(sqlite3.Row)``), so take the first
    value instead of indexing.
    """
    if not row:
        return ""
    if isinstance(row, dict):
        return str(next(iter(row.values())))
    return str(row[0])


async def aexecute_insert(sql: str, *args: Any) -> str:
    """Execute an INSERT and return the new row's id as a string.

    The SQL statement **must** include ``RETURNING id``.
    """
    pool = await aget_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
        return _first_column(row)


async def aexecute_many(sql: str, args_list: list[tuple[Any, ...]]) -> None:
    """Execute a batch INSERT / UPDATE with many parameter sets."""
    pool = await aget_pool()
    async with pool.acquire() as conn:
        await conn.executemany(sql, args_list)


@asynccontextmanager
async def atransaction():
    """Async context manager wrapping a PostgreSQL transaction.

    Usage::

        async with atransaction() as conn:
            await conn.execute("INSERT INTO ...", ...)
            await conn.execute("UPDATE ...", ...)
    """
    pool = await aget_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            yield conn
