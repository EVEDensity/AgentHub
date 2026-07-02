from __future__ import annotations

"""Database session layer — Neon PostgreSQL via HTTP SQL protocol.

Neon's native PostgreSQL wire protocol (TCP :5432) can be blocked by
certain firewalls / VPNs.  The HTTP SQL endpoint (POST /sql over HTTPS)
uses the same protocol as the official ``@neondatabase/serverless`` JS
driver and the VS Code / PyCharm IDE plugins — it tunnels PostgreSQL
queries over standard HTTPS, bypassing TCP-level issues.

API surface (unchanged from asyncpg days):
  aget_pool()                → NeonHttpPool
  aclose_pool()              → None (graceful shutdown)
  afetch_all(sql, *args)     → list[dict]
  afetch_one(sql, *args)     → dict | None
  aexecute(sql, *args)       → None
  aexecute_insert(sql, *args)→ str (new row id — SQL must include RETURNING)
  aexecute_many(sql, list)   → None
  atransaction()             → async context manager → NeonHttpConnection
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from app.config import DATABASE_URL
from app.db.neon_http import (
    NeonHttpConnection,
    NeonHttpPool,
    get_neon_http_pool,
    close_neon_http_pool,
)

logger = logging.getLogger("agenthub.db")

# ═══════════════════════════════════════════════════════════════════════
# Connection pool
# ═══════════════════════════════════════════════════════════════════════

_pool: NeonHttpPool | None = None
_pool_lock = asyncio.Lock()


async def aget_pool() -> NeonHttpPool:
    """Return the Neon HTTP pool (lazy-init, thread-safe).

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

        _pool = await get_neon_http_pool(DATABASE_URL)
        logger.info("db: Neon HTTP pool ready")
        return _pool


async def aclose_pool() -> None:
    """Close the HTTP pool gracefully (call during app shutdown)."""
    global _pool
    if _pool is not None:
        logger.info("db: closing Neon HTTP pool...")
        await close_neon_http_pool()
        _pool = None
        logger.info("db: Neon HTTP pool closed.")


# ═══════════════════════════════════════════════════════════════════════
# Async query API
#
# Each call acquires a fresh logical "connection" (stateless HTTP
# request under the hood).  There are no stale-conn issues — every
# request is independent.
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
