from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.v1.access import authorize_workspace
from app.repositories import MissionRepository
from app.schemas.a2a_adapter import (
    A2AInboundTaskCancelRequest,
    A2AInboundTaskCreateRequest,
    A2ATaskCancelRequest,
    A2ATaskCreateRequest,
    A2ATaskFailRequest,
    normalize_source_agent_url,
)
from app.services.a2a_adapter_service import (
    A2AAdapterService,
    A2ATaskConflictError,
    A2ATaskNotFoundError,
    build_a2a_actor,
)
from app.services.a2a_result_bundle_service import (
    A2AResultBundlePolicyError,
    A2AResultBundleTooLargeError,
)
from app.services.agent_binding_service import (
    AgentBindingSelector,
    AgentBindingUnavailableError,
    DatabaseAgentBindingSelector,
)
from app.services.artifact_integrity_service import (
    ArtifactByteExporter,
    ArtifactBytesUnavailableError,
    ArtifactIntegrityError,
    build_artifact_byte_verifier,
)
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/a2a/tasks", tags=["a2a-adapter"])


def get_a2a_repository() -> MissionRepository:
    return MissionRepository()


def get_a2a_binding_selector() -> AgentBindingSelector:
    return DatabaseAgentBindingSelector()


def get_a2a_artifact_byte_exporter() -> ArtifactByteExporter:
    return build_artifact_byte_verifier()


CurrentUser = Annotated[dict, Depends(get_current_user)]
A2ARepositoryDep = Annotated[MissionRepository, Depends(get_a2a_repository)]
A2ABindingSelectorDep = Annotated[
    AgentBindingSelector,
    Depends(get_a2a_binding_selector),
]
A2AArtifactByteExporterDep = Annotated[
    ArtifactByteExporter,
    Depends(get_a2a_artifact_byte_exporter),
]
WorkspaceId = Annotated[str, Query(alias="workspaceId", min_length=1, max_length=255)]
TaskId = Annotated[str, Query(alias="taskId", min_length=1, max_length=255)]
SourceAgentUrl = Annotated[
    str,
    Query(alias="sourceAgentUrl", min_length=1, max_length=2048),
]


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AgentBindingUnavailableError):
        return HTTPException(
            status_code=503,
            detail="A2A execution capacity is unavailable",
        )
    if isinstance(exc, A2ATaskNotFoundError):
        return HTTPException(status_code=404, detail="A2A task not found")
    if isinstance(exc, A2AResultBundleTooLargeError):
        return HTTPException(status_code=413, detail="A2A result bundle is too large")
    if isinstance(exc, ArtifactBytesUnavailableError):
        return HTTPException(
            status_code=424,
            detail="A2A result Artifact bytes are unavailable",
        )
    if isinstance(exc, ArtifactIntegrityError):
        return HTTPException(
            status_code=409,
            detail="A2A result Artifact integrity check failed",
        )
    if isinstance(exc, A2AResultBundlePolicyError):
        return HTTPException(
            status_code=409,
            detail="A2A result bundle is not exportable",
        )
    return HTTPException(status_code=409, detail=str(exc))


@router.post("")
async def submit_a2a_task(
    request: A2ATaskCreateRequest,
    user: CurrentUser,
    repository: A2ARepositoryDep,
    binding_selector: A2ABindingSelectorDep,
) -> dict:
    authorize_workspace(user, request.workspace_id)
    try:
        return await A2AAdapterService(
            repository,
            binding_selector=binding_selector,
        ).submit_task(
            request,
            actor=build_a2a_actor(user),
        )
    except (AgentBindingUnavailableError, A2ATaskConflictError, ValueError) as exc:
        raise _translate_error(exc) from exc


@router.post("/inbound")
async def accept_inbound_a2a_task(
    request: A2AInboundTaskCreateRequest,
    user: CurrentUser,
    repository: A2ARepositoryDep,
    binding_selector: A2ABindingSelectorDep,
) -> dict:
    authorize_workspace(user, request.workspace_id)
    try:
        return await A2AAdapterService(
            repository,
            binding_selector=binding_selector,
        ).accept_inbound_task(
            request,
            actor=build_a2a_actor(user),
        )
    except (AgentBindingUnavailableError, A2ATaskConflictError, ValueError) as exc:
        raise _translate_error(exc) from exc


@router.post("/inbound/cancel")
async def cancel_inbound_a2a_task(
    request: A2AInboundTaskCancelRequest,
    user: CurrentUser,
    repository: A2ARepositoryDep,
) -> dict:
    authorize_workspace(user, request.workspace_id)
    try:
        return await A2AAdapterService(repository).cancel_inbound_task(
            request.workspace_id,
            request.source_agent_url,
            request.task_id,
            actor=build_a2a_actor(user),
        )
    except (A2ATaskNotFoundError, A2ATaskConflictError) as exc:
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


@router.get("/inbound")
async def get_inbound_a2a_task(
    workspace_id: WorkspaceId,
    source_agent_url: SourceAgentUrl,
    task_id: TaskId,
    user: CurrentUser,
    repository: A2ARepositoryDep,
    artifact_byte_exporter: A2AArtifactByteExporterDep,
) -> dict:
    authorize_workspace(user, workspace_id)
    try:
        normalized_source = normalize_source_agent_url(source_agent_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        return await A2AAdapterService(
            repository,
            artifact_byte_exporter=artifact_byte_exporter,
        ).get_inbound_task(
            workspace_id,
            normalized_source,
            task_id,
        )
    except (
        A2ATaskNotFoundError,
        A2AResultBundlePolicyError,
        ArtifactBytesUnavailableError,
        ArtifactIntegrityError,
    ) as exc:
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


@router.post("/fail")
async def fail_a2a_task(
    request: A2ATaskFailRequest,
    user: CurrentUser,
    repository: A2ARepositoryDep,
) -> dict:
    authorize_workspace(user, request.workspace_id)
    try:
        return await A2AAdapterService(repository).fail_task(
            request.workspace_id,
            request.task_id,
            actor=build_a2a_actor(user),
            reason=request.reason,
        )
    except (A2ATaskNotFoundError, A2ATaskConflictError) as exc:
        raise _translate_error(exc) from exc
