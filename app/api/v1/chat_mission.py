"""Chat-to-Mission adapter (P0 web chat migration slice, ADR-0108).

Thin endpoint that turns one chat message into a running Mission:
create + start in one HTTP round-trip, return the mission_id and the
SSE stream URL. The caller then opens the event stream and renders
events as they arrive — no legacy orchestrator, no WebSocket session.

This is the bridge between the new Mission/v1 surface and the web
chat surface. The CLI already uses this path (``agenthub run`` →
``execute_objective``); the web chat page is being migrated onto it.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.access import authorize_workspace
from app.db.init_db import now
from app.repositories import MissionRepository
from app.services.auth_service import get_current_user
from app.services.mission_service import (
    MissionService,
    build_human_actor,
)

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatMissionRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda s: s.replace("_", ""),
        extra="forbid",
        populate_by_name=True,
    )

    message: str = Field(min_length=1, max_length=8000)
    workspace_id: str = Field(default="local-admin", alias="workspaceId")
    session_id: str | None = Field(default=None, alias="sessionId")
    stream: bool = True


def get_mission_repository() -> MissionRepository:
    return MissionRepository()


CurrentUser = Annotated[dict, Depends(get_current_user)]
MissionRepositoryDep = Annotated[MissionRepository, Depends(get_mission_repository)]


@router.post("/mission", status_code=202)
async def create_chat_mission(
    request: ChatMissionRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    """Create and start a Mission from one chat message.

    Returns ``missionId`` and ``streamUrl``. The caller opens the SSE
    stream at ``GET /api/v1/missions/{missionId}/events/stream`` to
    consume the event ledger as it arrives.
    """
    authorize_workspace(user, request.workspace_id)

    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="message is required")

    mission_id = f"mis-chat-{uuid.uuid4().hex[:12]}"
    title = message.splitlines()[0][:80] or "Chat mission"

    service = MissionService(repository)
    try:
        mission = await service.create_mission(
            mission_id=mission_id,
            workspace_id=request.workspace_id,
            title=title,
            objective=message,
            source={
                "type": "chat.mission",
                "session_id": request.session_id,
                "created_at": now().isoformat(),
            },
            actor=build_human_actor(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Start immediately — the web chat surface expects a running mission.
    try:
        await service.start_mission(
            mission_id=mission_id,
            actor=build_human_actor(user),
        )
    except Exception as exc:  # noqa: BLE001 - start failures surface cleanly
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    stream_url = (
        f"/api/v1/missions/{mission_id}/events/stream?maxSeconds=0"
    )

    return {
        "missionId": mission.id,
        "status": mission.status.value,
        "streamUrl": stream_url,
        "updatedAt": mission.updated_at.isoformat(),
    }
