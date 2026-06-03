from __future__ import annotations

"""Database session layer — Neon PostgreSQL via asyncpg.

All database access is asynchronous.  The connection pool is lazy-initialised
on first use and shared across the application lifetime.

API surface:
  aget_pool()                → asyncpg.Pool
  aclose_pool()              → None (graceful shutdown)
  afetch_all(sql, *args)     → list[dict]
  afetch_one(sql, *args)     → dict | None
  aexecute(sql, *args)       → None
  aexecute_insert(sql, *args)→ str (new row id — SQL must include RETURNING)
  aexecute_many(sql, list)   → None
  atransaction()             → async context manager → asyncpg.Connection
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from app.config import DATABASE_URL

logger = logging.getLogger("agenthub.db")

# ═══════════════════════════════════════════════════════════════════════
# Connection pool
# ═══════════════════════════════════════════════════════════════════════

_pool: Any = None  # asyncpg.Pool | None
_pool_lock = asyncio.Lock()


async def aget_pool() -> Any:
    """Return the asyncpg connection pool (lazy-init, thread-safe).

    Raises RuntimeError if DATABASE_URL is not configured.
    """
    global _pool

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

        import asyncpg

        logger.info("db: connecting to PostgreSQL pool...")
        try:
            _pool = await asyncpg.create_pool(
                DATABASE_URL,
                min_size=2,
                max_size=20,
                command_timeout=30,
                server_settings={
                    "application_name": "agenthub",
                    "timezone": "Asia/Shanghai",
                },
            )
            # Verify connectivity
            async with _pool.acquire() as conn:
                version = await conn.fetchval("SELECT version()")
                logger.info("db: PostgreSQL pool ready — %s", version)
        except Exception:
            logger.exception("db: failed to connect to PostgreSQL")
            _pool = None
            raise

    return _pool


async def aclose_pool() -> None:
    """Close the asyncpg pool gracefully (call during app shutdown)."""
    global _pool
    if _pool is not None:
        logger.info("db: closing PostgreSQL pool...")
        await _pool.close()
        _pool = None
        logger.info("db: PostgreSQL pool closed.")


# ═══════════════════════════════════════════════════════════════════════
# Async query API
# ═══════════════════════════════════════════════════════════════════════


async def afetch_all(sql: str, *args: Any) -> list[dict[str, Any]]:
    """Execute a SELECT and return all rows as dicts."""
    pool = await aget_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [dict(row) for row in rows]


async def afetch_one(sql: str, *args: Any) -> dict[str, Any] | None:
    """Execute a SELECT and return the first row as a dict, or None."""
    pool = await aget_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
        return dict(row) if row else None


async def aexecute(sql: str, *args: Any) -> None:
    """Execute an INSERT / UPDATE / DELETE statement."""
    pool = await aget_pool()
    async with pool.acquire() as conn:
        await conn.execute(sql, *args)


async def aexecute_insert(sql: str, *args: Any) -> str:
    """Execute an INSERT and return the new row's id as a string.

    The SQL statement **must** include ``RETURNING id``.
    """
    pool = await aget_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
        return str(row[0]) if row else ""


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
