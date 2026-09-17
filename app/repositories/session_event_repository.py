"""SessionEventRepository — persistence for T1-3 session event stream."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from app.domain import ActorRef, SessionEvent, SessionEventType

Execute = Callable[..., Awaitable[None]]
FetchOne = Callable[..., Awaitable[dict[str, Any] | None]]
FetchAll = Callable[..., Awaitable[list[dict[str, Any]]]]


def _encode_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _decode_json(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        raise TypeError("database field payload must contain a JSON object")
    return dict(value)


def _decode_actor(row: Mapping[str, Any]) -> ActorRef:
    return ActorRef(
        type=str(row["actor_type"]),
        id=str(row["actor_id"]),
        display_name=str(row.get("actor_display_name") or ""),
    )


def _event_from_row(row: Mapping[str, Any]) -> SessionEvent:
    created_at = row["created_at"]
    if isinstance(created_at, str):
        # SQLite returns ISO strings; normalise to UTC-aware datetime.
        if created_at.endswith("Z"):
            created_at = created_at[:-1] + "+00:00"
        created_at = datetime.fromisoformat(created_at)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    return SessionEvent(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        event_type=SessionEventType(row["event_type"]),
        actor=_decode_actor(row),
        payload=_decode_json(row["payload"]),
        created_at=created_at,
    )


class SessionEventRepository:
    """Persistence adapter for the immutable session event stream.

    Follows the same constructor-injection pattern as
    :class:`app.repositories.mission_repository.MissionRepository`, so the
    default ``aexecute`` / ``afetch_one`` / ``afetch_all`` wiring picks up
    whichever backend (PostgreSQL or SQLite) the app is running against.
    """

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

    async def add_session_event(self, event: SessionEvent) -> None:
        """Append one immutable event to the session stream."""
        await self._execute(
            """INSERT INTO session_events(
                   id, session_id, event_type,
                   actor_type, actor_id, actor_display_name,
                   payload, created_at
               ) VALUES($1, $2, $3, $4, $5, $6, $7, $8)""",
            event.id,
            event.session_id,
            event.event_type.value,
            event.actor.type,
            event.actor.id,
            event.actor.display_name or "",
            _encode_json(event.payload),
            event.created_at,
        )

    # ── queries ────────────────────────────────────────────────────

    async def get_session_event(self, event_id: str) -> SessionEvent | None:
        row = await self._fetch_one(
            """SELECT id, session_id, event_type,
                      actor_type, actor_id, actor_display_name,
                      payload, created_at
               FROM session_events WHERE id=$1""",
            event_id,
        )
        return _event_from_row(row) if row is not None else None

    async def list_session_events(
        self,
        session_id: str,
        *,
        event_type: SessionEventType | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[SessionEvent]:
        """Return the event stream for ``session_id`` in chronological order."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if offset < 0:
            raise ValueError("offset cannot be negative")

        if event_type is not None:
            rows = await self._fetch_all(
                """SELECT id, session_id, event_type,
                          actor_type, actor_id, actor_display_name,
                          payload, created_at
                   FROM session_events
                   WHERE session_id=$1 AND event_type=$2
                   ORDER BY created_at ASC, id ASC
                   LIMIT $3 OFFSET $4""",
                session_id,
                event_type.value,
                limit,
                offset,
            )
        else:
            rows = await self._fetch_all(
                """SELECT id, session_id, event_type,
                          actor_type, actor_id, actor_display_name,
                          payload, created_at
                   FROM session_events
                   WHERE session_id=$1
                   ORDER BY created_at ASC, id ASC
                   LIMIT $2 OFFSET $3""",
                session_id,
                limit,
                offset,
            )
        return [_event_from_row(row) for row in rows]

    async def count_session_events(
        self,
        session_id: str,
        *,
        event_type: SessionEventType | None = None,
    ) -> int:
        if event_type is not None:
            row = await self._fetch_one(
                "SELECT COUNT(*) AS n FROM session_events WHERE session_id=$1 AND event_type=$2",
                session_id,
                event_type.value,
            )
        else:
            row = await self._fetch_one(
                "SELECT COUNT(*) AS n FROM session_events WHERE session_id=$1",
                session_id,
            )
        return int(row["n"]) if row else 0

    # ── cross-session text search (T6) ─────────────────────────────

    async def search_session_events(
        self,
        query: str,
        *,
        session_id: str | None = None,
        event_type: SessionEventType | None = None,
        limit: int = 50,
    ) -> list[SessionEvent]:
        """Search session events whose payload or event_type matches ``query``.

        Case-insensitive substring match on the JSON-serialised payload
        plus the event_type column.  Works on both SQLite (LIKE) and
        PostgreSQL (LIKE).  FTS5 / tsvector indexes can be layered on top
        for performance; this is the correctness baseline.
        """
        terms = [term.lower() for term in query.split() if term]
        if not terms:
            return []
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")

        conditions: list[str] = []
        params: list[Any] = []
        placeholder = 1

        # Each term must match somewhere in payload (JSON text) or event_type.
        # Terms are ANDed together; within a term, payload OR event_type match.
        term_clauses = []
        for term in terms:
            pattern = f"%{term}%"
            term_clauses.append(
                f"(LOWER(payload) LIKE ${placeholder} OR LOWER(event_type) LIKE ${placeholder})"
            )
            params.append(pattern)
            placeholder += 1
        conditions.append("(" + " AND ".join(term_clauses) + ")")

        if session_id is not None:
            conditions.append(f"session_id=${placeholder}")
            params.append(session_id)
            placeholder += 1
        if event_type is not None:
            conditions.append(f"event_type=${placeholder}")
            params.append(event_type.value)
            placeholder += 1

        where = " AND ".join(conditions)
        sql = (
            "SELECT id, session_id, event_type, actor_type, actor_id, "
            f"actor_display_name, payload, created_at "
            f"FROM session_events WHERE {where} "
            "ORDER BY created_at DESC, id DESC "
            f"LIMIT ${placeholder}"
        )
        params.append(limit)

        rows = await self._fetch_all(sql, *params)
        return [_event_from_row(row) for row in rows]
