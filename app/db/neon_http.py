"""Neon HTTP SQL client — PostgreSQL over HTTPS.

Replaces direct TCP connections to Neon when the native PostgreSQL wire
protocol is unreachable (common behind certain firewalls / VPNs).

Uses the same HTTP SQL endpoint that the official ``@neondatabase/serverless``
JS driver and the VS Code / PyCharm IDE plugins rely on.

Protocol reference:
  POST https://<endpoint>/sql
  Header:  Neon-Connection-String: postgresql://user:pass@host/db
  Body:    {"query":"SELECT $1", "params":[val], "rowAsArray":false}

Each HTTP request is stateless — there is no persistent connection or pooling.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse, urlunparse, parse_qs


# ═══════════════════════════════════════════════════════════════════════
# Record — dict subclass with index access (mimics asyncpg.Record)
# ═══════════════════════════════════════════════════════════════════════


class Record(dict):
    """A dict subclass that also supports positional index access.

    This mimics ``asyncpg.Record`` so existing code that uses either
    ``row[0]`` (positional) or ``row["col"]`` (by name) works unchanged.
    """

    __slots__ = ("_keys",)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Cache key order for O(1) index lookups
        object.__setattr__(self, "_keys", list(super().keys()))

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return super().__getitem__(self._keys[key])
        return super().__getitem__(key)

    def __iter__(self) -> Iterator:
        return iter(self._keys)

    def keys(self):  # type: ignore[override]
        return self._keys

    def values(self):  # type: ignore[override]
        for k in self._keys:
            yield super().__getitem__(k)

    def items(self):  # type: ignore[override]
        for k in self._keys:
            yield k, super().__getitem__(k)

logger = logging.getLogger("agenthub.neon_http")

# ── SSL context (reused across requests) ───────────────────────────────
_ssl_context: ssl.SSLContext | None = None


def _get_ssl_context() -> ssl.SSLContext:
    """Lazy-init SSL context that tolerates Neon's SNI-based routing."""
    global _ssl_context
    if _ssl_context is None:
        _ssl_context = ssl.create_default_context()
        # Neon uses SNI for compute routing — the certificate CN may not
        # match the endpoint hostname, so we skip hostname verification.
        _ssl_context.check_hostname = False
        _ssl_context.verify_mode = ssl.CERT_NONE
    return _ssl_context


# ── SQL parsing helpers ────────────────────────────────────────────────


def _replace_positional(sql: str, args: tuple[Any, ...]) -> str:
    r"""Replace ``$1``, ``$2``, … with ``$N`` placeholders.

    asyncpg uses ``$1`` positional style; the Neon HTTP endpoint accepts
    the same format.  This is a no-op — we keep the query as-is and pass
    *args* in the ``params`` array.
    """
    return sql


# ── HTTP fetch (using stdlib, no extra deps) ───────────────────────────


# Neondb connection string without query parameters (extracted at init time)
_CONN_STR: str = ""
_ENDPOINT_URL: str = ""
_REQUEST_HEADERS: dict[str, str] = {}


def _configure(database_url: str) -> None:
    """Extract connection info from *database_url* and prepare HTTP config."""
    global _CONN_STR, _ENDPOINT_URL, _REQUEST_HEADERS

    parsed = urlparse(database_url)
    host = parsed.hostname or ""
    user = parsed.username or ""
    password = parsed.password or ""
    dbname = parsed.path.lstrip("/") or "neondb"

    # Build a clean conn-string for the Neon-Connection-String header.
    # Strip query params that only apply to TCP (sslmode, channel_binding, etc.)
    clean = parsed._replace(
        query="",
        fragment="",
    )
    conn_str = urlunparse(clean)

    _CONN_STR = conn_str
    _ENDPOINT_URL = f"https://{host}/sql"
    _REQUEST_HEADERS = {
        "Content-Type": "application/json",
        "Neon-Connection-String": conn_str,
    }
    logger.info("neon_http: configured endpoint %s (user=%s db=%s)", host, user, dbname)


def _jsonify_param(value: Any) -> Any:
    """Convert a Python value to something JSON-serializable for Neon HTTP.

    - ``bytes`` → hex-encoded BYTEA string (``\\x...``)
    - Everything else → passed through unchanged
    """
    if isinstance(value, bytes):
        return "\\x" + value.hex()
    return value


async def _http_query(
    sql: str,
    args: tuple[Any, ...] = (),
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Send a single SQL statement over HTTP and return the parsed JSON.

    Returns the full Neon JSON response dict with keys:
      ``command``, ``fields``, ``rows``, ``rowCount``, ``rowAsArray``.

    On error the response contains ``message``, ``code``, etc.
    """
    if not _ENDPOINT_URL:
        raise RuntimeError("neon_http not configured — call _configure(url) first")

    body = json.dumps({
        "query": sql,
        "params": [_jsonify_param(p) for p in args],
    }).encode()

    # Use asyncio for non-blocking HTTP in async context
    import urllib.request

    loop = asyncio.get_running_loop()

    def _sync_request() -> dict[str, Any]:
        req = urllib.request.Request(
            _ENDPOINT_URL,
            data=body,
            headers=_REQUEST_HEADERS,
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                req,
                timeout=timeout,
                context=_get_ssl_context(),
            ) as resp:
                raw = resp.read()
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            # Try to parse Neon error body
            try:
                err_body = json.loads(e.read())
            except Exception:
                err_body = {"message": str(e), "code": str(e.code)}
            return err_body  # caller will see 'message' key and raise

    return await loop.run_in_executor(None, _sync_request)


# ── Result conversion ──────────────────────────────────────────────────


# PostgreSQL OID → Python type decoder
# BYTEA (oid=17) arrives as a hex string "\x…" via HTTP JSON.
# We decode it back to bytes so existing code (avatar serving, etc.)
# sees the same type it got from asyncpg.
_BYTEA_OID = 17

# Numeric / boolean OIDs — Neon HTTP may return these as JSON strings
# (e.g. COUNT(*) → "16") rather than native JSON numbers / booleans.
# We convert them so arithmetic and comparisons work as expected.
_INT_OIDS: set[int] = {20, 21, 23}     # int8, int2, int4
_FLOAT_OIDS: set[int] = {700, 701, 1700}  # float4, float8, numeric
_BOOL_OID = 16


def _coerce_field(value: Any, oid: int | None) -> Any:
    """Convert a Neon HTTP field value to the expected Python type.

    Neon SQL-over-HTTP may serialise some PostgreSQL types as JSON strings
    instead of native JSON types.  This function applies the reverse
    coercion so downstream code sees the same types asyncpg would return.
    """
    if value is None:
        return None
    if oid is not None and isinstance(value, str):
        if oid in _INT_OIDS:
            try:
                return int(value)
            except ValueError:
                return value
        if oid in _FLOAT_OIDS:
            try:
                return float(value)
            except ValueError:
                return value
        if oid == _BOOL_OID:
            return value.lower() in ("t", "true", "1", "yes", "on")
    return value


def _decode_bytea(value: str) -> bytes:
    """Decode a PostgreSQL hex-encoded BYTEA string to bytes.

    Format: ``\\x89504e47…`` (JSON-escaped) or ``\\x89504e47…``.
    """
    # Strip leading backslash-escaped prefix
    s = value
    if s.startswith("\\\\x"):
        s = s[3:]  # remove \\\\x (JSON double-escaped)
    elif s.startswith("\\x"):
        s = s[2:]  # remove \\x
    return bytes.fromhex(s)


def _rows_to_dicts(
    response: dict[str, Any],
) -> list[dict[str, Any]]:
    """Convert Neon HTTP response rows to list-of-dicts (like asyncpg.fetch).

    Automatically decodes BYTEA hex strings back to ``bytes`` and coerces
    numeric / boolean fields to their native Python types so the caller
    sees the same types as with native asyncpg connections.
    """
    fields = response.get("fields", [])
    rows = response.get("rows", [])

    # Build maps: field_index → oid, and collect field names
    field_oids: list[int | None] = []
    field_names: list[str] = []
    for f in fields:
        field_names.append(f["name"])
        field_oids.append(f.get("dataTypeID"))

    result: list[Record] = []
    for row in rows:
        if response.get("rowAsArray", False):
            # row is a list of values — decode / coerce by position
            decoded: dict[str, Any] = {}
            for i, val in enumerate(row):
                key = field_names[i]
                oid = field_oids[i]
                if oid == _BYTEA_OID and isinstance(val, str):
                    try:
                        decoded[key] = _decode_bytea(val)
                    except (ValueError, IndexError):
                        decoded[key] = val
                else:
                    decoded[key] = _coerce_field(val, oid)
            result.append(Record(decoded))
        else:
            # row is a dict keyed by column name — decode / coerce by index
            decoded_row: dict[str, Any] = {}
            for i, (key, val) in enumerate(row.items()):
                oid = field_oids[i] if i < len(field_oids) else None
                if oid == _BYTEA_OID and isinstance(val, str):
                    try:
                        decoded_row[key] = _decode_bytea(val)
                    except (ValueError, IndexError):
                        decoded_row[key] = val
                else:
                    decoded_row[key] = _coerce_field(val, oid)
            result.append(Record(decoded_row))
    return result


def _ensure_ok(response: dict[str, Any]) -> None:
    """Raise if the Neon HTTP response contains an error."""
    if "message" in response:
        code = response.get("code", "")
        severity = response.get("severity", "")
        detail = response.get("detail", "")
        hint = response.get("hint", "")
        msg = f"Neon HTTP error: {response['message']}"
        if code:
            msg += f" [code={code}]"
        if severity:
            msg += f" [severity={severity}]"
        if detail:
            msg += f"\n  Detail: {detail}"
        if hint:
            msg += f"\n  Hint: {hint}"
        raise RuntimeError(msg)


# ── Connection-like wrapper ────────────────────────────────────────────


class NeonHttpConnection:
    """A lightweight object that mimics ``asyncpg.Connection``.

    All operations are stateless HTTP requests — there is no real
    PostgreSQL connection behind this object.
    """

    def __init__(self) -> None:
        self._closed = False

    async def execute(self, sql: str, *args: Any) -> str:
        """Execute a statement and return the status string (e.g. 'INSERT 0 1')."""
        self._check_open()
        resp = await _http_query(sql, args)
        _ensure_ok(resp)
        return resp.get("command", "")

    async def executemany(self, sql: str, args_list: list[tuple[Any, ...]]) -> None:
        """Execute a statement with many parameter sets.

        Neon HTTP doesn't support true batch execution natively — we
        fire all requests concurrently and raise the first error.
        """
        self._check_open()

        tasks = [_http_query(sql, tuple(a)) for a in args_list]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        errors = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                errors.append((i, r))
            elif isinstance(r, dict):
                try:
                    _ensure_ok(r)
                except RuntimeError as e:
                    errors.append((i, e))

        if errors:
            first_idx, first_err = errors[0]
            raise RuntimeError(
                f"executemany: batch item {first_idx} failed: {first_err}"
            ) from first_err

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        """Execute a SELECT and return all rows as dicts."""
        self._check_open()
        resp = await _http_query(sql, args)
        _ensure_ok(resp)
        return _rows_to_dicts(resp)

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        """Execute a SELECT and return the first row as a dict, or None."""
        self._check_open()
        resp = await _http_query(sql, args)
        _ensure_ok(resp)
        rows = _rows_to_dicts(resp)
        return rows[0] if rows else None

    async def fetchval(self, sql: str, *args: Any) -> Any:
        """Execute a SELECT and return the first value of the first row."""
        self._check_open()
        resp = await _http_query(sql, args)
        _ensure_ok(resp)
        rows = _rows_to_dicts(resp)
        if rows:
            first_row = rows[0]
            # Return the first column value (dict is ordered in Python 3.7+)
            return next(iter(first_row.values())) if first_row else None
        return None

    def transaction(self):
        """Return an async context manager for a transaction block.

        Transactions over HTTP are emulated via ``BEGIN`` / ``COMMIT`` /
        ``ROLLBACK`` statements.  Because each HTTP request is stateless,
        you **must** perform all work inside the ``async with`` block —
        do not keep the yielded connection and use it later.
        """
        return _NeonHttpTransaction(self)

    async def close(self) -> None:
        """Mark this connection as closed."""
        self._closed = True

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("Connection is closed")

    # ── Compatibility shims for code that accesses raw asyncpg conn attrs ──

    def __aiter__(self):
        raise NotImplementedError("NeonHttpConnection does not support iteration")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


# ── Transaction emulation ──────────────────────────────────────────────


class _NeonHttpTransaction:
    """Emulates ``conn.transaction()`` over stateless HTTP.

    Sends ``BEGIN`` on enter and ``COMMIT`` on successful exit
    (or ``ROLLBACK`` on exception).
    """

    def __init__(self, conn: NeonHttpConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> NeonHttpConnection:
        await self._conn.execute("BEGIN")
        return self._conn

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            try:
                await self._conn.execute("ROLLBACK")
            except Exception:
                logger.warning("neon_http: ROLLBACK failed", exc_info=True)
        else:
            await self._conn.execute("COMMIT")


# ── Pool-like wrapper ──────────────────────────────────────────────────


class NeonHttpPool:
    """An ``asyncpg.Pool``-shaped object backed by Neon HTTP SQL.

    Usage::

        pool = NeonHttpPool()
        await pool.initialize(database_url)

        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT $1::text AS hello", "world")
            assert rows == [{"hello": "world"}]
    """

    def __init__(self) -> None:
        self._initialized = False
        self._database_url: str = ""

    async def initialize(self, database_url: str) -> None:
        """Configure the HTTP endpoint (call once on app startup)."""
        _configure(database_url)
        # Quick connectivity check
        conn = NeonHttpConnection()
        try:
            ver = await conn.fetchval("SELECT version()")
            logger.info("neon_http: connected — %s", ver)
        except Exception as exc:
            logger.error("neon_http: connectivity check failed: %s", exc)
            raise
        self._initialized = True
        self._database_url = database_url

    def acquire(self):
        """Return an async context manager that yields a NeonHttpConnection."""
        return _NeonHttpAcquireContext(self)

    async def close(self) -> None:
        """No-op — HTTP has no persistent connections to close."""
        self._initialized = False
        logger.info("neon_http: pool closed (no persistent connections to clean up)")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


class _NeonHttpAcquireContext:
    """Async context manager that yields a fresh NeonHttpConnection."""

    def __init__(self, pool: NeonHttpPool) -> None:
        self._pool = pool
        self._conn: NeonHttpConnection | None = None

    async def __aenter__(self) -> NeonHttpConnection:
        self._conn = NeonHttpConnection()
        return self._conn

    async def __aexit__(self, *args: Any) -> None:
        if self._conn:
            await self._conn.close()
        self._conn = None


# ── Singleton ──────────────────────────────────────────────────────────

_pool: NeonHttpPool | None = None
_pool_lock = asyncio.Lock()


async def get_neon_http_pool(database_url: str) -> NeonHttpPool:
    """Get or create the global NeonHttpPool singleton."""
    global _pool
    if _pool is not None:
        return _pool

    async with _pool_lock:
        if _pool is not None:
            return _pool
        _pool = NeonHttpPool()
        await _pool.initialize(database_url)
        return _pool


async def close_neon_http_pool() -> None:
    """Shut down the HTTP pool (currently a no-op)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None

