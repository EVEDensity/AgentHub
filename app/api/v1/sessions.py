"""v1 sessions.py — T1-3 session event stream API.

Two read surfaces over the :class:`SessionEventRepository`:

* ``GET /api/v1/sessions/{sessionId}/events`` — paginated query
  with optional ``eventType`` filter and ``afterId`` cursor.
* ``GET /api/v1/sessions/{sessionId}/events/stream`` — Server-Sent
  Events subscription, polling every ``pollSeconds`` and pushing each
  new event as one ``data: <json>`` frame.

Both endpoints are **read-only** — the session event log is append-only
(ADR-0108) and mutations live in the chat_mission adapter / future
``POST /sessions/{sessionId}/events`` ingestion endpoints.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Annotated, AsyncIterator, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.v1.access import authorize_workspace
from app.domain import SessionEventType
from app.repositories import SessionEventRepository
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/sessions", tags=["sessions"])

CurrentUser = Annotated[dict, Depends(get_current_user)]


# ── Dependency wiring ───────────────────────────────────────────


def get_session_event_repository() -> SessionEventRepository:
    return SessionEventRepository()


SessionEventRepoDep = Annotated[SessionEventRepository, Depends(get_session_event_repository)]


# ── Parameter helpers ────────────────────────────────────────────


EventLimit = Annotated[int, Query(ge=1, le=500)]
EventPollSeconds = Annotated[float, Query(gt=0.1, le=30.0)]
EventMaxSeconds = Annotated[int, Query(ge=0)]
EventAfterId = Annotated[str | None, Query()]
EventTypeFilter = Annotated[str | None, Query()]


# ── Public projection ────────────────────────────────────────────


def _project_event(event) -> dict:
    """Convert a :class:`SessionEvent` to its API wire shape."""
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


# ── Query ────────────────────────────────────────────────────────


@router.get("/{session_id}/events")
async def list_session_events(
    session_id: str,
    workspace_id: Annotated[str, Query(alias="workspaceId")] = "local-admin",
    event_type: EventTypeFilter = None,
    limit: EventLimit = 200,
    after_id: EventAfterId = None,
    user: CurrentUser = None,
    repo: SessionEventRepoDep = None,
) -> dict:
    """Paginated read of the session event log (oldest → newest)."""
    if user is not None:
        authorize_workspace(user, workspace_id)

    type_filter: SessionEventType | None = None
    if event_type is not None:
        try:
            type_filter = SessionEventType(event_type)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"unknown eventType {event_type!r}",
            ) from exc

    offset = 0
    if after_id:
        # Cursor = offset-of(after_id) + 1 so we start right after it.
        after = await repo.get_session_event(after_id)
        if after is None:
            raise HTTPException(
                status_code=404,
                detail=f"afterId {after_id!r} not found in session {session_id}",
            )
        # Count events up to and including ``after`` to compute offset.
        offset = await repo.count_session_events(session_id)
        # Better: walk events in order to find the exact position.
        all_events = await repo.list_session_events(
            session_id,
            event_type=type_filter,
            limit=500,
            offset=0,
        )
        offset = 0
        for idx, ev in enumerate(all_events):
            if ev.id == after_id:
                offset = idx + 1
                break
        else:
            offset = len(all_events)

    events = await repo.list_session_events(
        session_id,
        event_type=type_filter,
        limit=limit,
        offset=offset,
    )
    total = await repo.count_session_events(
        session_id,
        event_type=type_filter,
    )
    return {
        "events": [_project_event(e) for e in events],
        "total": total,
        "hasMore": (offset + len(events)) < total,
        "nextAfterId": events[-1].id if events else None,
    }


# ── SSE stream ───────────────────────────────────────────────────


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
) -> StreamingResponse:
    """Server-Sent Events subscription over the session event log.

    Polls ``GET /events`` semantics every ``pollSeconds`` and pushes
    each new event as one ``data: <json>`` frame.  ``maxSeconds`` caps
    the stream server-side (0 = unlimited).  Client disconnect ends
    the stream cleanly.
    """
    if user is not None:
        authorize_workspace(user, workspace_id)

    type_filter: SessionEventType | None = None
    if event_type is not None:
        try:
            type_filter = SessionEventType(event_type)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"unknown eventType {event_type!r}",
            ) from exc

    seen_ids: set[str] = set()
    if after_id:
        # Prime ``seen_ids`` with everything up to and including ``after_id``
        # so we don't re-deliver history on stream start.
        history = await repo.list_session_events(
            session_id,
            event_type=type_filter,
            limit=500,
            offset=0,
        )
        for ev in history:
            seen_ids.add(ev.id)
            if ev.id == after_id:
                break

    async def event_stream() -> AsyncIterator[str]:
        deadline = time.monotonic() + max_seconds if max_seconds > 0 else None
        while True:
            try:
                batch = await repo.list_session_events(
                    session_id,
                    event_type=type_filter,
                    limit=limit,
                    offset=0,
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - poll failures must not kill stream
                batch = []

            for event in batch:
                if event.id in seen_ids:
                    continue
                seen_ids.add(event.id)
                yield f"data: {json.dumps(_project_event(event), ensure_ascii=False)}\n\n"

            if deadline is not None and time.monotonic() >= deadline:
                return
            try:
                if await request.is_disconnected():
                    return
            except Exception:  # noqa: BLE001 - transport already gone
                return
            await asyncio.sleep(poll_seconds)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
