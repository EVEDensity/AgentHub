from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.repositories import MissionRepository
from app.schemas.mission import MissionCreateRequest
from app.services.auth_service import get_current_user
from app.services.mission_service import MissionService, build_human_actor

router = APIRouter(prefix="/missions", tags=["missions"])


def get_mission_repository() -> MissionRepository:
    return MissionRepository()


CurrentUser = Annotated[dict, Depends(get_current_user)]
MissionRepositoryDep = Annotated[MissionRepository, Depends(get_mission_repository)]
WorkspaceId = Annotated[str, Query(alias="workspaceId")]
MissionLimit = Annotated[int, Query(ge=1, le=200)]
MissionOffset = Annotated[int, Query(ge=0)]


def _authorize_workspace(user: dict, workspace_id: str) -> None:
    if user.get("role") == "admin":
        return
    if str(user["id"]) == workspace_id:
        return
    raise HTTPException(status_code=403, detail="Workspace access denied")


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_mission(
    request: MissionCreateRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    _authorize_workspace(user, request.workspace_id)
    service = MissionService(repository)
    try:
        mission = await service.create_mission(
            mission_id=request.id,
            workspace_id=request.workspace_id,
            title=request.title,
            objective=request.objective,
            source=request.source,
            contract=request.contract,
            actor=build_human_actor(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return mission.to_public_dict()


@router.get("/{mission_id}")
async def get_mission(
    mission_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    mission = await repository.get_mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    _authorize_workspace(user, mission.workspace_id)
    return mission.to_public_dict()


@router.get("")
async def list_missions(
    workspace_id: WorkspaceId,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    limit: MissionLimit = 100,
    offset: MissionOffset = 0,
) -> dict:
    _authorize_workspace(user, workspace_id)
    missions = await repository.list_missions(
        workspace_id,
        limit=limit,
        offset=offset,
    )
    return {"missions": [mission.to_public_dict() for mission in missions]}
