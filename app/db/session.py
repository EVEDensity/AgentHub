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

import asyncpg

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

        async def _init_conn(conn: asyncpg.Connection) -> None:
            """Init callback — verify each new conn is alive before it's handed out.

            This catches a freshly-created-but-broken conn at pool creation
            time, so asyncpg replaces it with a healthy one on the first
            ``acquire()`` instead of handing a dead one to the caller.
            """
            try:
                await conn.execute("SELECT 1")
            except Exception:
                # Force the pool to throw this conn away and create a new one
                await conn.close()
                raise

        logger.info("db: connecting to PostgreSQL pool...")
        try:
            _pool = await asyncpg.create_pool(
                DATABASE_URL,
                min_size=2,
                max_size=20,
                command_timeout=30,
                # Close conns that have been idle longer than this. The server
                # may already have killed them via TCP keepalive or
                # `idle_in_transaction_session_timeout`; without this, we'd
                # hand a stale conn back to the caller.
                max_inactive_connection_lifetime=120,
                init=_init_conn,
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

# Errors that indicate a stale or broken connection — when we see one, we
# release the conn back to the pool (asyncpg will discard it) and retry the
# operation once on a fresh conn.
_RETRYABLE_DB_ERRORS: tuple[type[BaseException], ...] = (
    asyncpg.exceptions.ConnectionDoesNotExistError,
    asyncpg.exceptions.CannotConnectNowError,
    asyncpg.exceptions.PostgresConnectionError,
    asyncpg.exceptions.InterfaceError,
    asyncpg.exceptions.PostgresError,  # last-resort catch-all; refine if false-positives appear
    ConnectionResetError,
    OSError,  # covers ECONNRESET / EPIPE on Windows
)

_MAX_QUERY_ATTEMPTS = 2  # initial + 1 retry


async def _acquire_with_retry(
    pool: asyncpg.Pool,
    op_name: str,
    fn,
) -> Any:
    """Acquire a conn from the pool and run ``fn(conn)``.

    On any retryable DB error (stale conn, RST, server-gone), release the
    bad conn and try once more on a fresh one. This is the single point of
    truth for stale-conn recovery — every query helper funnels through here.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, _MAX_QUERY_ATTEMPTS + 1):
        async with pool.acquire() as conn:
            try:
                return await fn(conn)
            except _RETRYABLE_DB_ERRORS as e:
                last_exc = e
                # The `async with pool.acquire()` context manager will
                # release the conn (broken or not) on exit, and asyncpg's
                # pool implementation discards closed/broken conns. The next
                # acquire() in this loop will get a fresh one.
                logger.warning(
                    "db: %s attempt %d/%d hit stale-conn (err=%s: %s) — retrying on fresh conn",
                    op_name, attempt, _MAX_QUERY_ATTEMPTS,
                    type(e).__name__, str(e)[:120],
                )
                continue
    # Both attempts failed with a retryable error — re-raise the last one.
    assert last_exc is not None
    raise last_exc


async def afetch_all(sql: str, *args: Any) -> list[dict[str, Any]]:
    """Execute a SELECT and return all rows as dicts."""
    pool = await aget_pool()
    async def _do(conn: asyncpg.Connection):
        rows = await conn.fetch(sql, *args)
        return [dict(row) for row in rows]
    return await _acquire_with_retry(pool, "afetch_all", _do)


async def afetch_one(sql: str, *args: Any) -> dict[str, Any] | None:
    """Execute a SELECT and return the first row as a dict, or None."""
    pool = await aget_pool()
    async def _do(conn: asyncpg.Connection):
        row = await conn.fetchrow(sql, *args)
        return dict(row) if row else None
    return await _acquire_with_retry(pool, "afetch_one", _do)


async def aexecute(sql: str, *args: Any) -> None:
    """Execute an INSERT / UPDATE / DELETE statement."""
    pool = await aget_pool()
    async def _do(conn: asyncpg.Connection):
        await conn.execute(sql, *args)
    await _acquire_with_retry(pool, "aexecute", _do)


async def aexecute_insert(sql: str, *args: Any) -> str:
    """Execute an INSERT and return the new row's id as a string.

    The SQL statement **must** include ``RETURNING id``.
    """
    pool = await aget_pool()
    async def _do(conn: asyncpg.Connection):
        row = await conn.fetchrow(sql, *args)
        return str(row[0]) if row else ""
    return await _acquire_with_retry(pool, "aexecute_insert", _do)


async def aexecute_many(sql: str, args_list: list[tuple[Any, ...]]) -> None:
    """Execute a batch INSERT / UPDATE with many parameter sets."""
    pool = await aget_pool()
    async def _do(conn: asyncpg.Connection):
        await conn.executemany(sql, args_list)
    await _acquire_with_retry(pool, "aexecute_many", _do)


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
