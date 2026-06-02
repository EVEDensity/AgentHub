from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Any

from app.config import DB_PATH

logger = logging.getLogger("agenthub.db")

# ── Async lock to serialise all database *write* operations ──────────
# SQLite allows only one writer at a time (even in WAL mode).  Without
# this lock, concurrent async tasks (agent streaming, background memory,
# auto-naming, tool-call logging) each open their own connection and
# race to write — causing ``sqlite3.OperationalError: database is locked``.
_write_lock = asyncio.Lock()

# Max number of retries for "database is locked" errors (with backoff)
_MAX_RETRIES = 5
_RETRY_BASE_DELAY = 0.05  # 50 ms


def get_connection() -> sqlite3.Connection:
    """Return a new SQLite connection with WAL mode and generous busy timeout.

    The connection is in autocommit mode (isolation_level='' by default), so
    every INSERT / UPDATE / DELETE is a transaction of its own that commits
    immediately.  Always use this as a context manager (``with get_connection()
    as conn:``) so the connection is properly closed — a bare
    ``get_connection().execute(...)`` LEAKS the connection and may hold a
    write lock indefinitely.
    """
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")   # 30 s — generous safety net
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@asynccontextmanager
async def write_conn():
    """Async context manager that provides a connection for write ops.

    Serialises all writes through a module-level ``asyncio.Lock`` so that
    only one coroutine can hold a write connection at a time.  Reads can
    proceed concurrently in WAL mode.
    """
    async with _write_lock:
        conn = get_connection()
        try:
            yield conn
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower():
                logger.warning("db write_conn: database locked, retrying...")
                conn.close()
                # retry once after a short sleep
                await asyncio.sleep(0.1)
                conn = get_connection()
                yield conn
                return
            raise
        finally:
            conn.close()


def _execute_with_retry(
    conn: sqlite3.Connection,
    sql: str,
    args: tuple[Any, ...] = (),
) -> sqlite3.Cursor:
    """Execute SQL with retry when the database is locked."""
    for attempt in range(_MAX_RETRIES):
        try:
            return conn.execute(sql, args)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == _MAX_RETRIES - 1:
                raise
            delay = _RETRY_BASE_DELAY * (2 ** attempt)
            time.sleep(delay)
    raise RuntimeError("unreachable")  # pragma: no cover


def dict_rows(sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with get_connection() as conn:
        return [dict(row) for row in conn.execute(sql, args)]


def one_row(sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(sql, args).fetchone()
        return dict(row) if row else None


def transaction() -> Iterator[sqlite3.Connection]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


