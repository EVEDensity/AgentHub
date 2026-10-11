"""SessionRepository — persistence for T3 chat sessions."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from app.domain import ActorRef, Session, SessionStatus

Execute = Callable[..., Awaitable[None]]
FetchOne = Callable[..., Awaitable[dict[str, Any] | None]]
FetchAll = Callable[..., Awaitable[list[dict[str, Any]]]]


def _sqlite_created_order() -> str:
    # SQLite's datetime/julianday parser rounds fractional seconds to millis.
    # Parse only integral seconds there; keep the original six decimal places
    # as a second INTEGER key. Postgres native-to-TEXT values can use +HH.
    tail = "substr(created_at,21)"
    zone = f"CASE WHEN substr(created_at,20,1)='.' THEN ltrim({tail},'0123456789') ELSE substr(created_at,20) END"
    normalized_zone = f"CASE WHEN length({zone})=3 THEN ({zone}) || ':00' WHEN length({zone})=5 THEN substr(({zone}),1,3) || ':' || substr(({zone}),4,2) ELSE ({zone}) END"
    seconds = f"CAST(strftime('%s', substr(created_at,1,19) || ({normalized_zone})) AS INTEGER)"
    fraction = f"substr({tail},1,length({tail})-length(ltrim({tail},'0123456789')))"
    micros = f"CASE WHEN substr(created_at,20,1)='.' THEN CAST(substr(({fraction}) || '000000',1,6) AS INTEGER) ELSE 0 END"
    return f"{seconds} DESC, {micros} DESC, id DESC"


def _created_order(fetch_all: FetchAll, *, backend: str | None, configured: bool) -> str:
    if backend is None:
        if configured:
            from app.db.session import is_sqlite_backend
            backend = "sqlite" if is_sqlite_backend() else "postgresql"
        else:
            from app.db.sqlite_pool import SQLiteConnection
            backend = "sqlite" if isinstance(getattr(fetch_all, "__self__", None), SQLiteConnection) else "postgresql"
    if backend == "sqlite":
        return _sqlite_created_order()
    if backend == "postgresql":
        # Naive legacy strings use the same UTC interpretation as the decoder,
        # regardless of the PostgreSQL connection's configured TimeZone.
        zoned = "created_at ~ '[T ][0-9]{2}:' AND created_at ~ '(Z|[+-][0-9]{2}(:?[0-9]{2})?)$'"
        instant = f"CASE WHEN {zoned} THEN created_at::timestamptz ELSE created_at::timestamp AT TIME ZONE 'UTC' END"
        return f"({instant}) DESC, id DESC"
    raise ValueError("session repository backend must be sqlite or postgresql")


def _decode_datetime(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.removesuffix("Z") + ("+00:00" if value.endswith("Z") else ""))
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _encode_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _encode_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _decode_json(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        return None
    return dict(value)


def _decode_actor(row: Mapping[str, Any]) -> ActorRef:
    return ActorRef(
        type=str(row["created_by_type"]),
        id=str(row["created_by_id"]),
        display_name=str(row.get("created_by_display_name") or ""),
    )


def _session_from_row(row: Mapping[str, Any]) -> Session:
    return Session(
        id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        title=str(row["title"]),
        status=SessionStatus(row["status"]),
        metadata=_decode_json(row.get("metadata")),
        created_by=_decode_actor(row),
        created_at=_decode_datetime(row["created_at"]),
        updated_at=_decode_datetime(row["updated_at"]),
    )


class SessionRepository:
    """Persistence adapter for chat sessions."""

    def __init__(
        self,
        *,
        execute: Execute | None = None,
        fetch_one: FetchOne | None = None,
        fetch_all: FetchAll | None = None,
        backend: str | None = None,
    ) -> None:
        configured = fetch_all is None
        if execute is None or fetch_one is None or fetch_all is None:
            from app.db.session import aexecute, afetch_all, afetch_one

            execute = execute or aexecute
            fetch_one = fetch_one or afetch_one
            fetch_all = fetch_all or afetch_all
        self._execute = execute
        self._fetch_one = fetch_one
        self._fetch_all = fetch_all
        self._created_order = _created_order(fetch_all, backend=backend, configured=configured)

    # ── mutations ──────────────────────────────────────────────────

    async def add_session(self, session: Session) -> None:
        """Persist one new chat session."""
        await self._execute(
            """INSERT INTO sessions(
                   id, workspace_id, title, status, metadata,
                   created_by_type, created_by_id, created_by_display_name,
                   created_at, updated_at, name, owner_id
               ) VALUES($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)""",
            session.id,
            session.workspace_id,
            session.title,
            session.status.value,
            _encode_json(session.metadata) if session.metadata else None,
            session.created_by.type,
            session.created_by.id,
            session.created_by.display_name or "",
            _encode_datetime(session.created_at),
            _encode_datetime(session.updated_at),
            session.title,
            session.created_by.id if session.created_by.type == "human" else "",
        )

    async def archive_session(self, session_id: str) -> Session | None:
        """Mark a session as ARCHIVED."""
        row = await self._fetch_one(
            "SELECT * FROM sessions WHERE id=$1",
            session_id,
        )
        if row is None or not row.get("workspace_id"):
            return None
        now = datetime.now(UTC)
        await self._execute(
            """UPDATE sessions
               SET status='ARCHIVED', active=0, updated_at=$1
               WHERE id=$2""",
            _encode_datetime(now),
            session_id,
        )
        row = dict(row)
        row["status"] = "ARCHIVED"
        row["updated_at"] = now
        return _session_from_row(row)

    # ── queries ────────────────────────────────────────────────────

    async def get_session(self, session_id: str) -> Session | None:
        row = await self._fetch_one(
            "SELECT * FROM sessions WHERE id=$1",
            session_id,
        )
        # Legacy conversation rows have no durable workspace assignment.
        # Do not infer a v1 scope from owner_id, participants, or the caller.
        if row is None or not row.get("workspace_id"):
            return None
        return _session_from_row(row)

    async def list_sessions(
        self,
        workspace_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Session]:
        """Return sessions for a workspace ordered by creation desc."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if offset < 0:
            raise ValueError("offset cannot be negative")
        rows = await self._fetch_all(
            f"""SELECT * FROM sessions
               WHERE workspace_id=$1
               ORDER BY {self._created_order}
               LIMIT $2 OFFSET $3""",
            workspace_id,
            limit,
            offset,
        )
        return [_session_from_row(row) for row in rows]
