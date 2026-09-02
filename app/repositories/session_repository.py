"""SessionRepository — persistence for T3 chat sessions."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from app.domain import ActorRef, Session, SessionStatus

Execute = Callable[..., Awaitable[None]]
FetchOne = Callable[..., Awaitable[dict[str, Any] | None]]
FetchAll = Callable[..., Awaitable[list[dict[str, Any]]]]


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
    created_at = row["created_at"]
    updated_at = row["updated_at"]
    for dt in (created_at, updated_at):
        if isinstance(dt, str):
            if dt.endswith("Z"):
                dt = dt[:-1] + "+00:00"
            dt = datetime.fromisoformat(dt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

    return Session(
        id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        title=str(row["title"]),
        status=SessionStatus(row["status"]),
        metadata=_decode_json(row.get("metadata")),
        created_by=_decode_actor(row),
        created_at=created_at,
        updated_at=updated_at,
    )


class SessionRepository:
    """Persistence adapter for chat sessions."""

    def __init__(
        self,
        *,
        execute: Execute | None = None,
        fetch_one: FetchOne | None = None,
        fetch_all: FetchAll | None = None,
    ) -> None:
        if execute is None or fetch_one is None or fetch_all is None:
            from app.db.session import aexecute, afetch_all, afetch_one

            execute = execute or aexecute
            fetch_one = fetch_one or afetch_one
            fetch_all = fetch_all or afetch_all
        self._execute = execute
        self._fetch_one = fetch_one
        self._fetch_all = fetch_all

    # ── mutations ──────────────────────────────────────────────────

    async def add_session(self, session: Session) -> None:
        """Persist one new chat session."""
        await self._execute(
            """INSERT INTO sessions(
                   id, workspace_id, title, status, metadata,
                   created_by_type, created_by_id, created_by_display_name,
                   created_at, updated_at
               ) VALUES($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
            session.id,
            session.workspace_id,
            session.title,
            session.status.value,
            _encode_json(session.metadata) if session.metadata else None,
            session.created_by.type,
            session.created_by.id,
            session.created_by.display_name or "",
            session.created_at,
            session.updated_at,
        )

    async def archive_session(self, session_id: str) -> Session | None:
        """Mark a session as ARCHIVED."""
        row = await self._fetch_one(
            "SELECT * FROM sessions WHERE id=$1",
            session_id,
        )
        if row is None:
            return None
        now = datetime.now(timezone.utc)
        await self._execute(
            """UPDATE sessions
               SET status='ARCHIVED', updated_at=$1
               WHERE id=$2""",
            now,
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
        return _session_from_row(row) if row is not None else None

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
            """SELECT * FROM sessions
               WHERE workspace_id=$1
               ORDER BY created_at DESC, id DESC
               LIMIT $2 OFFSET $3""",
            workspace_id,
            limit,
            offset,
        )
        return [_session_from_row(row) for row in rows]
