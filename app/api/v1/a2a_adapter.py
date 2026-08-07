from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.v1.access import authorize_workspace
from app.repositories import MissionRepository
from app.schemas.a2a_adapter import A2ATaskCancelRequest, A2ATaskCreateRequest
from app.services.a2a_adapter_service import (
    A2AAdapterService,
    A2ATaskConflictError,
    A2ATaskNotFoundError,
    build_a2a_actor,
)
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/a2a/tasks", tags=["a2a-adapter"])


def get_a2a_repository() -> MissionRepository:
    return MissionRepository()


CurrentUser = Annotated[dict, Depends(get_current_user)]
A2ARepositoryDep = Annotated[MissionRepository, Depends(get_a2a_repository)]
WorkspaceId = Annotated[str, Query(alias="workspaceId", min_length=1, max_length=255)]
TaskId = Annotated[str, Query(alias="taskId", min_length=1, max_length=255)]


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, A2ATaskNotFoundError):
        return HTTPException(status_code=404, detail="A2A task not found")
    return HTTPException(status_code=409, detail=str(exc))


@router.post("")
async def submit_a2a_task(
    request: A2ATaskCreateRequest,
    user: CurrentUser,
    repository: A2ARepositoryDep,
) -> dict:
    authorize_workspace(user, request.workspace_id)
    try:
        return await A2AAdapterService(repository).submit_task(
            request,
            actor=build_a2a_actor(user),
        )
    except (A2ATaskConflictError, ValueError) as exc:
        raise _translate_error(exc) from exc


@router.get("")
async def get_a2a_task(
    workspace_id: WorkspaceId,
    task_id: TaskId,
    user: CurrentUser,
    repository: A2ARepositoryDep,
) -> dict:
    authorize_workspace(user, workspace_id)
    try:
        return await A2AAdapterService(repository).get_task(workspace_id, task_id)
    except A2ATaskNotFoundError as exc:
        raise _translate_error(exc) from exc


@router.post("/cancel")
async def cancel_a2a_task(
    request: A2ATaskCancelRequest,
    user: CurrentUser,
    repository: A2ARepositoryDep,
) -> dict:
    authorize_workspace(user, request.workspace_id)
    try:
        return await A2AAdapterService(repository).cancel_task(
            request.workspace_id,
            request.task_id,
            actor=build_a2a_actor(user),
        )
    except (A2ATaskNotFoundError, A2ATaskConflictError) as exc:
        raise _translate_error(exc) from exc
