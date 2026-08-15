from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.v1.access import authorize_verifier, authorize_workspace
from app.domain import EvidenceVerdict, InvalidStateTransition, Mission
from app.repositories import MissionRepository
from app.schemas.mission import (
    ArtifactCreateRequest,
    MissionCreateRequest,
    WorkUnitClaimRequest,
    WorkUnitCompletionRequest,
    WorkUnitCreateRequest,
    WorkUnitDelegationRequest,
    WorkUnitExecutionRequest,
    WorkUnitHeartbeatRequest,
    WorkUnitLeaseRequest,
    WorkUnitStartRequest,
    WorkUnitVerificationRequest,
)
from app.services.agent_binding_service import (
    AgentBindingResolver,
    AgentBindingUnavailableError,
    DatabaseAgentBindingResolver,
)
from app.services.artifact_integrity_service import (
    ArtifactBytesUnavailableError,
    ArtifactByteVerifier,
    ArtifactIntegrityError,
    build_artifact_byte_verifier,
)
from app.services.auth_service import get_current_user
from app.services.mission_service import (
    AgentBindingNotFoundError,
    MissionNotFoundError,
    MissionService,
    WorkUnitNotFoundError,
    build_human_actor,
    build_runner_actor,
    build_verifier_actor,
)

router = APIRouter(prefix="/missions", tags=["missions"])


def get_mission_repository() -> MissionRepository:
    return MissionRepository()


def get_artifact_byte_verifier() -> ArtifactByteVerifier:
    return build_artifact_byte_verifier()


def get_agent_binding_resolver() -> AgentBindingResolver:
    return DatabaseAgentBindingResolver()


CurrentUser = Annotated[dict, Depends(get_current_user)]
MissionRepositoryDep = Annotated[MissionRepository, Depends(get_mission_repository)]
ArtifactByteVerifierDep = Annotated[
    ArtifactByteVerifier,
    Depends(get_artifact_byte_verifier),
]
AgentBindingResolverDep = Annotated[
    AgentBindingResolver,
    Depends(get_agent_binding_resolver),
]
WorkspaceId = Annotated[str, Query(alias="workspaceId")]
MissionLimit = Annotated[int, Query(ge=1, le=200)]
MissionOffset = Annotated[int, Query(ge=0)]
EventAfterSequence = Annotated[int, Query(alias="afterSequence", ge=0)]
EventLimit = Annotated[int, Query(ge=1, le=200)]
WorkUnitLimit = Annotated[int, Query(ge=1, le=200)]
WorkUnitOffset = Annotated[int, Query(ge=0)]
ArtifactLimit = Annotated[int, Query(ge=1, le=200)]
ArtifactOffset = Annotated[int, Query(ge=0)]
EvidenceLimit = Annotated[int, Query(ge=1, le=200)]
EvidenceOffset = Annotated[int, Query(ge=0)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_mission(
    request: MissionCreateRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    authorize_workspace(user, request.workspace_id)
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
    authorize_workspace(user, mission.workspace_id)
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
    authorize_workspace(user, mission.workspace_id)
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


@router.get("/{mission_id}/evidence")
async def list_evidence(
    mission_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    limit: EvidenceLimit = 100,
    offset: EvidenceOffset = 0,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    evidence = await repository.list_evidence(
        mission_id,
        limit=limit,
        offset=offset,
    )
    return {"evidence": [item.to_public_dict() for item in evidence]}


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


@router.post(
    "/{mission_id}/work-units/{parent_work_unit_id}/delegations",
    status_code=status.HTTP_202_ACCEPTED,
)
async def delegate_work_unit(
    mission_id: str,
    parent_work_unit_id: str,
    request: WorkUnitDelegationRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    agent_binding_resolver: AgentBindingResolverDep,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    service = MissionService(repository, agent_binding_resolver=agent_binding_resolver)
    try:
        work_unit = await service.delegate_work_unit(
            mission_id,
            parent_work_unit_id,
            work_unit_id=request.id,
            kind=request.kind,
            input_refs=request.input_refs,
            expected_outputs=request.expected_outputs,
            required_capabilities=request.required_capabilities,
            agent_id=request.agent_id,
            lease_id=request.lease_id,
            runner_id=str(user["id"]),
            actor=build_human_actor(user),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except AgentBindingUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AgentBindingNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return work_unit.to_public_dict()


@router.get("/{mission_id}/artifacts")
async def list_artifacts(
    mission_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    limit: ArtifactLimit = 100,
    offset: ArtifactOffset = 0,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    artifacts = await repository.list_artifacts(
        mission_id,
        limit=limit,
        offset=offset,
    )
    return {"artifacts": [artifact.to_public_dict() for artifact in artifacts]}


@router.post("/{mission_id}/work-units/{work_unit_id}/lease")
async def lease_work_unit(
    mission_id: str,
    work_unit_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    request: WorkUnitLeaseRequest | None = None,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    work_unit = await repository.get_work_unit(work_unit_id)
    if work_unit is None or work_unit.mission_id != mission_id:
        raise HTTPException(status_code=404, detail="WorkUnit not found")
    service = MissionService(repository)
    try:
        leased = await service.lease_work_unit(
            mission_id,
            work_unit_id,
            runner_id=str(user["id"]),
            actor=build_human_actor(user),
            lease_seconds=request.lease_seconds if request is not None else 300,
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return leased.to_public_dict()


@router.post("/{mission_id}/work-unit-claims")
async def claim_delegated_work_unit(
    mission_id: str,
    request: WorkUnitClaimRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    """Claim one ready WorkUnit for an explicit Runner binding."""
    await _authorized_mission(mission_id, user=user, repository=repository)
    service = MissionService(repository)
    try:
        claimed = await service.claim_bound_work_unit(
            mission_id,
            agent_id=request.agent_id,
            adapter_type=request.adapter_type,
            runner_id=str(user["id"]),
            actor=build_runner_actor(user),
            lease_seconds=request.lease_seconds,
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"workUnit": claimed.to_public_dict() if claimed is not None else None}


@router.post("/{mission_id}/work-units/{work_unit_id}/start")
async def start_work_unit(
    mission_id: str,
    work_unit_id: str,
    request: WorkUnitStartRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    work_unit = await repository.get_work_unit(work_unit_id)
    if work_unit is None or work_unit.mission_id != mission_id:
        raise HTTPException(status_code=404, detail="WorkUnit not found")
    service = MissionService(repository)
    try:
        started = await service.start_work_unit(
            mission_id,
            work_unit_id,
            lease_id=request.lease_id,
            runner_id=str(user["id"]),
            actor=build_human_actor(user),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return started.to_public_dict()


@router.post("/{mission_id}/work-units/{work_unit_id}/heartbeat")
async def heartbeat_work_unit(
    mission_id: str,
    work_unit_id: str,
    request: WorkUnitHeartbeatRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    work_unit = await repository.get_work_unit(work_unit_id)
    if work_unit is None or work_unit.mission_id != mission_id:
        raise HTTPException(status_code=404, detail="WorkUnit not found")
    service = MissionService(repository)
    try:
        renewed = await service.heartbeat_work_unit(
            mission_id,
            work_unit_id,
            lease_id=request.lease_id,
            runner_id=str(user["id"]),
            actor=build_human_actor(user),
            lease_seconds=request.lease_seconds,
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return renewed.to_public_dict()


@router.post(
    "/{mission_id}/work-units/{work_unit_id}/artifacts",
    status_code=status.HTTP_201_CREATED,
)
async def register_artifact(
    mission_id: str,
    work_unit_id: str,
    request: ArtifactCreateRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    work_unit = await repository.get_work_unit(work_unit_id)
    if work_unit is None or work_unit.mission_id != mission_id:
        raise HTTPException(status_code=404, detail="WorkUnit not found")
    service = MissionService(repository)
    try:
        artifact = await service.register_artifact(
            mission_id,
            work_unit_id,
            artifact_id=request.id,
            lease_id=request.lease_id,
            runner_id=str(user["id"]),
            kind=request.kind,
            digest=request.digest,
            content_address=request.content_address,
            media_type=request.media_type,
            size_bytes=request.size_bytes,
            source_repository=request.source_repository,
            base_commit=request.base_commit,
            retention=request.retention,
            sensitivity=request.sensitivity,
            actor=build_human_actor(user),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return artifact.to_public_dict()


@router.post("/{mission_id}/work-units/{work_unit_id}/recover")
async def recover_work_unit_lease(
    mission_id: str,
    work_unit_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    work_unit = await repository.get_work_unit(work_unit_id)
    if work_unit is None or work_unit.mission_id != mission_id:
        raise HTTPException(status_code=404, detail="WorkUnit not found")
    service = MissionService(repository)
    try:
        recovered = await service.recover_expired_lease(
            mission_id,
            work_unit_id,
            actor=build_human_actor(user),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return recovered.to_public_dict()


async def _run_work_unit_execution_command(
    mission_id: str,
    work_unit_id: str,
    *,
    command: Literal["complete", "fail", "retry"],
    request: WorkUnitExecutionRequest | WorkUnitCompletionRequest,
    user: dict,
    repository: MissionRepository,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    work_unit = await repository.get_work_unit(work_unit_id)
    if work_unit is None or work_unit.mission_id != mission_id:
        raise HTTPException(status_code=404, detail="WorkUnit not found")
    service = MissionService(repository)
    runner_id = str(user["id"])
    actor = build_human_actor(user)
    try:
        if command == "complete":
            updated = await service.complete_work_unit(
                mission_id,
                work_unit_id,
                lease_id=request.lease_id,
                runner_id=runner_id,
                actor=actor,
                artifact_refs=request.artifact_refs,
            )
        elif command == "fail":
            updated = await service.fail_work_unit(
                mission_id,
                work_unit_id,
                lease_id=request.lease_id,
                runner_id=runner_id,
                actor=actor,
                reason=request.reason,
            )
        else:
            updated = await service.retry_work_unit(
                mission_id,
                work_unit_id,
                lease_id=request.lease_id,
                runner_id=runner_id,
                actor=actor,
                reason=request.reason,
            )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return updated.to_public_dict()


@router.post("/{mission_id}/work-units/{work_unit_id}/complete")
async def complete_work_unit(
    mission_id: str,
    work_unit_id: str,
    request: WorkUnitCompletionRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    return await _run_work_unit_execution_command(
        mission_id,
        work_unit_id,
        command="complete",
        request=request,
        user=user,
        repository=repository,
    )


@router.post("/{mission_id}/work-units/{work_unit_id}/verify")
async def verify_work_unit(
    mission_id: str,
    work_unit_id: str,
    request: WorkUnitVerificationRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    artifact_byte_verifier: ArtifactByteVerifierDep,
) -> dict:
    authorize_verifier(user)
    if user.get("role") != "admin" and request.verifier_id != str(user["id"]):
        raise HTTPException(status_code=403, detail="Verifier identity mismatch")
    await _authorized_mission(mission_id, user=user, repository=repository)
    work_unit = await repository.get_work_unit(work_unit_id)
    if work_unit is None or work_unit.mission_id != mission_id:
        raise HTTPException(status_code=404, detail="WorkUnit not found")
    service = MissionService(
        repository,
        artifact_byte_verifier=artifact_byte_verifier,
    )
    try:
        evidence, updated_work_unit, updated_mission = (
            await service.verify_work_unit(
                mission_id,
                work_unit_id,
                criterion_id=request.criterion_id,
                verifier_id=request.verifier_id,
                verifier_version=request.verifier_version,
                configuration_digest=request.configuration_digest,
                verdict=EvidenceVerdict(request.verdict),
                artifact_refs=request.artifact_refs,
                summary=request.summary,
                integrity_hash=request.integrity_hash,
                actor=build_verifier_actor(user),
            )
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except ArtifactBytesUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_424_FAILED_DEPENDENCY,
            detail=str(exc),
        ) from exc
    except ArtifactIntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "evidence": evidence.to_public_dict(),
        "workUnit": updated_work_unit.to_public_dict(),
        "mission": updated_mission.to_public_dict(),
    }


@router.post("/{mission_id}/work-units/{work_unit_id}/fail")
async def fail_work_unit(
    mission_id: str,
    work_unit_id: str,
    request: WorkUnitExecutionRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    return await _run_work_unit_execution_command(
        mission_id,
        work_unit_id,
        command="fail",
        request=request,
        user=user,
        repository=repository,
    )


@router.post("/{mission_id}/work-units/{work_unit_id}/retry")
async def retry_work_unit(
    mission_id: str,
    work_unit_id: str,
    request: WorkUnitExecutionRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    return await _run_work_unit_execution_command(
        mission_id,
        work_unit_id,
        command="retry",
        request=request,
        user=user,
        repository=repository,
    )


@router.get("")
async def list_missions(
    workspace_id: WorkspaceId,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    limit: MissionLimit = 100,
    offset: MissionOffset = 0,
) -> dict:
    authorize_workspace(user, workspace_id)
    missions = await repository.list_missions(
        workspace_id,
        limit=limit,
        offset=offset,
    )
    return {"missions": [mission.to_public_dict() for mission in missions]}
