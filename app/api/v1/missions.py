from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.domain import InvalidStateTransition, Mission
from app.repositories import MissionRepository
from app.schemas.mission import MissionCreateRequest, WorkUnitCreateRequest
from app.services.auth_service import get_current_user
from app.services.mission_service import (
    MissionNotFoundError,
    MissionService,
    build_human_actor,
)

router = APIRouter(prefix="/missions", tags=["missions"])


def get_mission_repository() -> MissionRepository:
    return MissionRepository()


CurrentUser = Annotated[dict, Depends(get_current_user)]
MissionRepositoryDep = Annotated[MissionRepository, Depends(get_mission_repository)]
WorkspaceId = Annotated[str, Query(alias="workspaceId")]
MissionLimit = Annotated[int, Query(ge=1, le=200)]
MissionOffset = Annotated[int, Query(ge=0)]
EventAfterSequence = Annotated[int, Query(alias="afterSequence", ge=0)]
EventLimit = Annotated[int, Query(ge=1, le=200)]
WorkUnitLimit = Annotated[int, Query(ge=1, le=200)]
WorkUnitOffset = Annotated[int, Query(ge=0)]


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


async def _authorized_mission(
    mission_id: str,
    *,
    user: dict,
    repository: MissionRepository,
) -> Mission:
    mission = await repository.get_mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    _authorize_workspace(user, mission.workspace_id)
    return mission


async def _run_lifecycle_command(
    mission_id: str,
    *,
    command: Literal["start", "cancel"],
    user: dict,
    repository: MissionRepository,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    service = MissionService(repository)
    actor = build_human_actor(user)
    try:
        if command == "start":
            mission = await service.start_mission(mission_id, actor=actor)
        else:
            mission = await service.cancel_mission(mission_id, actor=actor)
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except InvalidStateTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return mission.to_public_dict()


@router.post("/{mission_id}/start")
async def start_mission(
    mission_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    return await _run_lifecycle_command(
        mission_id,
        command="start",
        user=user,
        repository=repository,
    )


@router.post("/{mission_id}/cancel")
async def cancel_mission(
    mission_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    return await _run_lifecycle_command(
        mission_id,
        command="cancel",
        user=user,
        repository=repository,
    )


@router.get("/{mission_id}/events")
async def list_mission_events(
    mission_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    after_sequence: EventAfterSequence = 0,
    limit: EventLimit = 100,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    events = await repository.list_events(
        mission_id,
        after_sequence=after_sequence,
        limit=limit,
    )
    return {"events": [event.to_public_dict() for event in events]}


@router.post("/{mission_id}/work-units", status_code=status.HTTP_201_CREATED)
async def create_work_unit(
    mission_id: str,
    request: WorkUnitCreateRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    service = MissionService(repository)
    try:
        work_unit = await service.create_work_unit(
            mission_id,
            work_unit_id=request.id,
            kind=request.kind,
            dependencies=request.dependencies,
            input_refs=request.input_refs,
            expected_outputs=request.expected_outputs,
            required_capabilities=request.required_capabilities,
            assigned_adapter=request.assigned_adapter,
            actor=build_human_actor(user),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return work_unit.to_public_dict()


@router.get("/{mission_id}/work-units")
async def list_work_units(
    mission_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    limit: WorkUnitLimit = 100,
    offset: WorkUnitOffset = 0,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    work_units = await repository.list_work_units(
        mission_id,
        limit=limit,
        offset=offset,
    )
    return {"workUnits": [work_unit.to_public_dict() for work_unit in work_units]}


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
