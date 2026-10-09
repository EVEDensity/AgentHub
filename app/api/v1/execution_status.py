"""Authenticated durable execution status and expiry-bounded Runner contact."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.access import authorize_workspace
from app.repositories import MissionRepository
from app.repositories.runner_presence_repository import RunnerPresenceRepository
from app.services.auth_service import get_current_user
from app.services.mission_execution_status import project_execution_status

router = APIRouter(prefix="/missions", tags=["missions"])


def get_repository() -> MissionRepository:
    return MissionRepository()


def get_runner_presence_repository() -> RunnerPresenceRepository:
    return RunnerPresenceRepository()


@router.get("/{mission_id}/execution-status")
async def execution_status(
    mission_id: str, user: Annotated[dict, Depends(get_current_user)],
    repository: Annotated[MissionRepository, Depends(get_repository)],
    presence: Annotated[RunnerPresenceRepository, Depends(get_runner_presence_repository)],
) -> dict:
    try:
        mission = await repository.get_mission(mission_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Execution status is unavailable") from exc
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    authorize_workspace(user, mission.workspace_id)
    try:
        units = []
        offset = 0
        while True:
            page = await repository.list_work_units(mission_id, limit=200, offset=offset)
            units.extend(page)
            if len(page) < 200:
                break
            offset += len(page)
        return await project_execution_status(mission, units, presence)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Execution status is unavailable") from exc
