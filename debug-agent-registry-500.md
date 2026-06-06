# Debug Session: agent-registry-500

- **Status**: [FIX-APPLIED] — awaiting user post-fix verification
- **Endpoint**: `GET /api/agent/registry`
- **Symptom**: 500 Internal Server Error; asyncpg `ConnectionDoesNotExistError: connection was closed in the middle of operation`; underlying socket `ConnectionResetError: [WinError 10054]`
- **OS**: Windows; venv path shows `AgentHubV1.1/.venv` (mismatched, flagged for follow-up)

## Root Cause (confirmed by code reading)

- **H1 + H2 combined**: `app/db/session.py:afetch_all` and siblings had zero error handling. asyncpg pool keeps `min_size=2` warm conns, but the server side (PG `idle_in_transaction_session_timeout`, OS TCP keepalive, Windows firewall, server restart) can silently kill them. When a stale conn is handed to `conn.fetch()`, asyncpg surfaces `ConnectionDoesNotExistError` and the request dies with 500.

## Fix Applied — `app/db/session.py`

Three layers of defense:

1. **Pool init callback** — `_init_conn` runs `SELECT 1` on every new conn. If it fails, the conn is closed and asyncpg creates a fresh one.
2. **Stale-conn lifetime** — `max_inactive_connection_lifetime=120` (was default 300). Idle conns are recycled before the server can kill them.
3. **Per-query retry** — `_acquire_with_retry` helper funnels all 5 query functions. On any `ConnectionDoesNotExistError` / `CannotConnectNowError` / `PostgresConnectionError` / `InterfaceError` / `ConnectionResetError` / `OSError` / `PostgresError`, the bad conn is released and the operation is retried once on a fresh conn. `_MAX_QUERY_ATTEMPTS = 2`.

### Files touched
- `app/db/session.py` — added `import asyncpg`, `_init_conn` callback, `init=_init_conn` + `max_inactive_connection_lifetime=120` in pool config, `_RETRYABLE_DB_ERRORS` tuple, `_acquire_with_retry` helper, rewrote `afetch_all`/`afetch_one`/`aexecute`/`aexecute_insert`/`aexecute_many` to use it. `atransaction` left as-is (caller-driven retry).

### Files NOT touched (intentional)
- `app/api/agent.py` — endpoint code is correct, the failure was at the session layer.
- `app/api/agent.py` `registry` — no need to add error handling; with retry at the session layer, this should never raise 500 for stale-conn reasons anymore.

## Verification Plan

1. Restart backend (`uvicorn ... --reload` or just kill+relaunch).
2. Wait until pool is created (look for `db: PostgreSQL pool ready — PostgreSQL ...`).
3. Hit `GET /api/agent/registry` 20× with ~1s gap to ensure it works under normal load.
4. **Reproduce the original bug** by:
   - Restarting the PostgreSQL server (or pausing it for 30s) while the app is running
   - Or: setting `pg_terminate_backend(pid)` for the app's conns
   - Or: waiting 5+ minutes idle, then hitting the endpoint
5. Expected behavior: 200 OK, with a single warning line in logs: `db: afetch_all attempt 1/2 hit stale-conn (err=ConnectionDoesNotExistError: ...) — retrying on fresh conn`.

## Cleanup (after user confirms fix works)

- Mark this file's status `[CLOSED]`
- No instrumentation to remove (was already replaced by the cleaner helper logs)

## Follow-up (separate task)

- **venv mismatch**: stack trace path was `AgentHubV1.1\.venv\Lib\site-packages\asyncpg\...` but project code is in `AgenthubV1.2`. Likely a shared venv or accidental import path. Worth verifying with `pip show asyncpg` from the project's actual venv and ensuring `python` resolved by uvicorn is the v1.2 venv.

