"""Workspace-authorized, read-only projections of the session event log."""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from app.api.v1.access import authorize_workspace
from app.api.v1.session_access import require_session_workspace
from app.domain import SessionEvent, SessionEventType
from app.repositories import SessionEventRepository, SessionRepository
from app.services.auth_service import get_current_user
from app.repositories.session_mission_history import SessionMissionCursorNotFound, SessionMissionHistoryRepository
from app.schemas.session_history import SessionDetail, SessionDirectory, SessionMissionDirectory
from app.services.session_history_service import list_native_session_missions, list_native_sessions, session_directory_item

router = APIRouter(prefix="/sessions", tags=["sessions"])
CurrentUser = Annotated[dict, Depends(get_current_user)]


def get_session_event_repository() -> SessionEventRepository:
    return SessionEventRepository()


def get_session_repository() -> SessionRepository:
    return SessionRepository()


def get_session_mission_history_repository() -> SessionMissionHistoryRepository:
    return SessionMissionHistoryRepository()


SessionEventRepoDep = Annotated[SessionEventRepository, Depends(get_session_event_repository)]
SessionRepoDep = Annotated[SessionRepository, Depends(get_session_repository)]
EventLimit = Annotated[int, Query(ge=1, le=500)]
EventPollSeconds = Annotated[float, Query(alias="pollSeconds", gt=0.1, le=30.0)]
EventMaxSeconds = Annotated[float, Query(alias="maxSeconds", ge=0, le=3600)]
EventAfterId = Annotated[str | None, Query(alias="afterId")]
EventTypeFilter = Annotated[str | None, Query(alias="eventType")]
HistoryRepoDep = Annotated[SessionMissionHistoryRepository, Depends(get_session_mission_history_repository)]
HistoryWorkspace = Annotated[str, Query(alias="workspaceId", min_length=1, max_length=255)]
SessionLimit = Annotated[int, Query(ge=1, le=50)]
MissionHistoryLimit = Annotated[int, Query(ge=1, le=20)]
HistoryOffset = Annotated[int, Query(ge=0)]
HistoryBeforeId = Annotated[str | None, Query(alias="beforeId", min_length=1, max_length=255)]
_HISTORY_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _authorize_history(user: dict, workspace_id: str) -> None:
    if user.get("role") in {"runner", "verifier", "agent", "service"}:
        raise HTTPException(403, "Human Session history access required")
    authorize_workspace(user, workspace_id)


def _history_failure(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        exc.headers = {**(exc.headers or {}), **_HISTORY_HEADERS}
        return exc
    if isinstance(exc, SessionMissionCursorNotFound):
        return HTTPException(404, "Mission history cursor not found", headers=_HISTORY_HEADERS)
    return HTTPException(503, "Session history is unavailable", headers=_HISTORY_HEADERS)


@router.get("", response_model=SessionDirectory)
async def list_native_session_directory(
    workspace_id: HistoryWorkspace, user: CurrentUser, sessions: SessionRepoDep,
    response: Response, limit: SessionLimit = 50, offset: HistoryOffset = 0,
) -> SessionDirectory:
    try:
        _authorize_history(user, workspace_id)
        result = await list_native_sessions(sessions, workspace_id, limit=limit, offset=offset)
    except Exception as exc:
        raise _history_failure(exc) from exc
    response.headers.update(_HISTORY_HEADERS)
    return result


@router.get("/{session_id}", response_model=SessionDetail)
async def get_native_session_detail(
    session_id: str, workspace_id: HistoryWorkspace, user: CurrentUser,
    sessions: SessionRepoDep, response: Response,
) -> SessionDetail:
    try:
        _authorize_history(user, workspace_id)
        session = await require_session_workspace(session_id, workspace_id, sessions)
        result = SessionDetail(workspace_id=workspace_id, session=session_directory_item(session))
    except Exception as exc:
        raise _history_failure(exc) from exc
    response.headers.update(_HISTORY_HEADERS)
    return result


@router.get("/{session_id}/missions", response_model=SessionMissionDirectory)
async def list_native_session_mission_directory(
    session_id: str, workspace_id: HistoryWorkspace, user: CurrentUser,
    sessions: SessionRepoDep, history: HistoryRepoDep, response: Response,
    limit: MissionHistoryLimit = 20, before_id: HistoryBeforeId = None,
) -> SessionMissionDirectory:
    try:
        _authorize_history(user, workspace_id)
        session = await require_session_workspace(session_id, workspace_id, sessions)
        result = await list_native_session_missions(history, session, limit=limit, before_id=before_id)
    except Exception as exc:
        raise _history_failure(exc) from exc
    response.headers.update(_HISTORY_HEADERS)
    return result


def _event_type(value: str | None) -> SessionEventType | None:
    if value is None:
        return None
    try:
        return SessionEventType(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"unknown eventType {value!r}") from exc


async def _session_cursor(
    session_id: str,
    after_id: str | None,
    repo: SessionEventRepository,
) -> SessionEvent | None:
    if not after_id:
        return None
    after = await repo.get_session_event(after_id)
    if after is None or after.session_id != session_id:
        raise HTTPException(status_code=404, detail="Session event cursor not found")
    return after


def _project_event(event: SessionEvent) -> dict:
    return {
        "id": event.id,
        "sessionId": event.session_id,
        "eventType": event.event_type.value,
        "actor": {
            "type": event.actor.type,
            "id": event.actor.id,
            "displayName": event.actor.display_name,
        },
        "payload": event.payload,
        "createdAt": event.created_at.isoformat(),
    }


@router.get("/{session_id}/events")
async def list_session_events(
    session_id: str,
    workspace_id: Annotated[str, Query(alias="workspaceId")] = "local-admin",
    event_type: EventTypeFilter = None,
    limit: EventLimit = 200,
    after_id: EventAfterId = None,
    user: CurrentUser = None,
    repo: SessionEventRepoDep = None,
    sessions: SessionRepoDep = None,
) -> dict:
    """Read a page in (created_at, id) order after durable scope validation."""
    authorize_workspace(user, workspace_id)
    await require_session_workspace(session_id, workspace_id, sessions)
    type_filter = _event_type(event_type)
    after = await _session_cursor(session_id, after_id, repo)
    events = await repo.list_session_events(
        session_id, event_type=type_filter, limit=limit, after=after,
    )
    total = await repo.count_session_events(session_id, event_type=type_filter)
    remaining = total if after is None else await repo.count_session_events(
        session_id, event_type=type_filter, after=after,
    )
    return {
        "events": [_project_event(event) for event in events],
        "total": total,
        "hasMore": len(events) < remaining,
        "nextAfterId": events[-1].id if events else None,
    }


@router.get("/{session_id}/events/stream")
async def stream_session_events(
    session_id: str,
    request: Request,
    workspace_id: Annotated[str, Query(alias="workspaceId")] = "local-admin",
    event_type: EventTypeFilter = None,
    after_id: EventAfterId = None,
    limit: EventLimit = 200,
    poll_seconds: EventPollSeconds = 1.0,
    max_seconds: EventMaxSeconds = 0,
    user: CurrentUser = None,
    repo: SessionEventRepoDep = None,
    sessions: SessionRepoDep = None,
) -> StreamingResponse:
    """Poll with a bounded keyset cursor; zero maxSeconds keeps the stream open."""
    authorize_workspace(user, workspace_id)
    await require_session_workspace(session_id, workspace_id, sessions)
    type_filter = _event_type(event_type)
    after = await _session_cursor(session_id, after_id, repo)

    async def event_stream() -> AsyncIterator[str]:
        cursor = after
        deadline = time.monotonic() + max_seconds if max_seconds > 0 else None
        while True:
            try:
                batch = await repo.list_session_events(
                    session_id, event_type=type_filter, limit=limit, after=cursor,
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - transient polls preserve the cursor
                batch = []

            for event in batch:
                cursor = event
                yield f"data: {json.dumps(_project_event(event), ensure_ascii=False)}\n\n"

            if deadline is not None and time.monotonic() >= deadline:
                return
            try:
                if await request.is_disconnected():
                    return
            except Exception:  # noqa: BLE001 - transport already gone
                return
            if len(batch) == limit:
                # Drain persisted pages immediately without retaining the
                # entire stream or repeatedly polling its first page.
                continue
            await asyncio.sleep(poll_seconds)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
