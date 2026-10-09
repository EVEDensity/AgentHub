"""Workspace-authorized, read-only projections of the session event log."""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.api.v1.access import authorize_workspace
from app.api.v1.session_access import require_session_workspace
from app.domain import SessionEvent, SessionEventType
from app.repositories import SessionEventRepository, SessionRepository
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/sessions", tags=["sessions"])
CurrentUser = Annotated[dict, Depends(get_current_user)]


def get_session_event_repository() -> SessionEventRepository:
    return SessionEventRepository()


def get_session_repository() -> SessionRepository:
    return SessionRepository()


SessionEventRepoDep = Annotated[SessionEventRepository, Depends(get_session_event_repository)]
SessionRepoDep = Annotated[SessionRepository, Depends(get_session_repository)]
EventLimit = Annotated[int, Query(ge=1, le=500)]
EventPollSeconds = Annotated[float, Query(alias="pollSeconds", gt=0.1, le=30.0)]
EventMaxSeconds = Annotated[float, Query(alias="maxSeconds", ge=0, le=3600)]
EventAfterId = Annotated[str | None, Query(alias="afterId")]
EventTypeFilter = Annotated[str | None, Query(alias="eventType")]


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