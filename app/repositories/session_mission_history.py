"""Bounded read projection of actual chat Mission ownership and creation order."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

FetchOne = Callable[..., Awaitable[dict[str, Any] | None]]
FetchAll = Callable[..., Awaitable[list[dict[str, Any]]]]
_SCOPE = "workspace_id=$1 AND source->>'type'='chat' AND source->>'sessionId'=$2"


class SessionMissionCursorNotFound(ValueError):
    pass


def _utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.removesuffix("Z") + ("+00:00" if value.endswith("Z") else ""))
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _sqlite_time_parts() -> tuple[str, str]:
    # Match SessionRepository's UTC and microsecond chronology. SQLite's
    # julianday/strftime on fractional input would round .999999 across seconds.
    tail = "substr(created_at,21)"
    zone = f"CASE WHEN substr(created_at,20,1)='.' THEN ltrim({tail},'0123456789') ELSE substr(created_at,20) END"
    normalized = f"CASE WHEN length({zone})=3 THEN ({zone}) || ':00' WHEN length({zone})=5 THEN substr(({zone}),1,3) || ':' || substr(({zone}),4,2) ELSE ({zone}) END"
    seconds = f"CAST(strftime('%s', substr(created_at,1,19) || ({normalized})) AS INTEGER)"
    fraction = f"substr({tail},1,length({tail})-length(ltrim({tail},'0123456789')))"
    micros = f"CASE WHEN substr(created_at,20,1)='.' THEN CAST(substr(({fraction}) || '000000',1,6) AS INTEGER) ELSE 0 END"
    return seconds, micros


@dataclass(frozen=True)
class SessionMissionRecord:
    id: str
    workspace_id: str
    session_id: str
    title: str
    objective: str
    status: str
    created_at: datetime


class SessionMissionHistoryRepository:
    def __init__(self, *, fetch_one: FetchOne | None = None, fetch_all: FetchAll | None = None,
                 backend: str | None = None):
        configured = fetch_all is None
        if fetch_one is None or fetch_all is None:
            from app.db.session import afetch_all, afetch_one
            fetch_one, fetch_all = fetch_one or afetch_one, fetch_all or afetch_all
        self._fetch_one, self._fetch_all = fetch_one, fetch_all
        if backend is None and not configured:
            from app.db.sqlite_pool import SQLiteConnection
            backend = "sqlite" if isinstance(getattr(fetch_all, "__self__", None), SQLiteConnection) else "postgresql"
        if backend is None:
            from app.db.session import is_sqlite_backend
            backend = "sqlite" if is_sqlite_backend() else "postgresql"
        if backend not in {"sqlite", "postgresql"}:
            raise ValueError("Mission history requires SQLite or PostgreSQL")
        self._backend = backend

    async def list_for_session(self, workspace_id: str, session_id: str, *, limit: int,
                               before_id: str | None = None) -> list[SessionMissionRecord]:
        if not 1 <= limit <= 21:
            raise ValueError("Mission history query limit must be between 1 and 21")
        parameters: list[Any] = [workspace_id, session_id]
        where = _SCOPE
        if before_id is not None:
            cursor = await self._fetch_one(
                f"SELECT id,created_at FROM missions WHERE {_SCOPE} AND id=$3",
                workspace_id, session_id, before_id,
            )
            if cursor is None:
                raise SessionMissionCursorNotFound("Mission history cursor not found")
            predicate, values = self._before(_utc(cursor["created_at"]), cursor["id"])
            where += " AND " + predicate
            parameters.extend(values)
        order = "created_at DESC,id DESC"
        if self._backend == "sqlite":
            seconds, micros = _sqlite_time_parts()
            order = f"{seconds} DESC,{micros} DESC,id DESC"
        parameters.append(limit)
        rows = await self._fetch_all(
            "SELECT id,workspace_id,source->>'sessionId' AS session_id,title,objective,status,created_at "
            f"FROM missions WHERE {where} ORDER BY {order} LIMIT ${len(parameters)}", *parameters,
        )
        return [SessionMissionRecord(**{**dict(row), "created_at": _utc(row["created_at"])}) for row in rows]

    def _before(self, created_at: datetime, identifier: str) -> tuple[str, list[Any]]:
        if self._backend == "postgresql":
            return "(created_at<$3 OR (created_at=$3 AND id<$4))", [created_at, identifier]
        seconds, micros = _sqlite_time_parts()
        elapsed = created_at - datetime(1970, 1, 1, tzinfo=UTC)
        epoch_seconds = elapsed.days * 86400 + elapsed.seconds
        return (
            f"(({seconds})<$3 OR (({seconds})=$3 AND ({micros})<$4) "
            f"OR (({seconds})=$3 AND ({micros})=$4 AND id<$5))",
            [epoch_seconds, created_at.microsecond, identifier],
        )
