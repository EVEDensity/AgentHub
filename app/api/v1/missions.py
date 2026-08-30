from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Annotated, AsyncIterator, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.v1.access import authorize_verifier, authorize_workspace
from app.domain import (
    ActorRef,
    DecisionResolution,
    DecisionStatus,
    EvaluationPolicyReason,
    EvidenceVerdict,
    InvalidStateTransition,
    Mission,
)
from app.repositories import MissionRepository
from app.schemas.mission import (
    ArtifactCreateRequest,
    ContractRevisionRequest,
    DecisionResolutionRequest,
    ExecutionCheckpointCreateRequest,
    MissionCreateRequest,
    MissionForkRequest,
    MissionGuidanceRequest,
    WorkspaceVerificationDiscoveryRequest,
    WorkspaceWorkUnitClaimRequest,
    WorkUnitClaimRequest,
    WorkUnitCompletionRequest,
    WorkUnitCreateRequest,
    WorkUnitDelegationRequest,
    WorkUnitExecutionContextRequest,
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
from app.services.desktop_changed_files_service import (
    collect_desktop_changed_files,
    resolve_desktop_execution_workspace_root,
)
from app.services.mission_service import (
    DESKTOP_TASK_WORK_UNIT_KIND,
    AgentBindingNotFoundError,
    ContractRevisionConflictError,
    DecisionConflictError,
    DecisionNotFoundError,
    MissionNotFoundError,
    MissionService,
    WorkUnitNotFoundError,
    build_human_actor,
    build_runner_actor,
    build_verifier_actor,
)
from app.services.workspace_access_service import (
    DatabaseRunnerWorkspaceGrantAuthorizer,
    DatabaseVerifierWorkspaceGrantAuthorizer,
    RunnerWorkspaceGrantAuthorizer,
    RunnerWorkspaceGrantUnavailableError,
    VerifierWorkspaceGrantAuthorizer,
    VerifierWorkspaceGrantUnavailableError,
)
from app.services.workspace_admission_service import (
    DatabaseWorkspaceClaimAdmissionPolicyResolver,
    WorkspaceClaimAdmissionDeniedError,
    WorkspaceClaimAdmissionPolicy,
    WorkspaceClaimAdmissionPolicyResolver,
    WorkspaceClaimAdmissionUnavailableError,
)

router = APIRouter(prefix="/missions", tags=["missions"])


def get_mission_repository() -> MissionRepository:
    return MissionRepository()


def get_artifact_byte_verifier() -> ArtifactByteVerifier:
    return build_artifact_byte_verifier()


def get_agent_binding_resolver() -> AgentBindingResolver:
    return DatabaseAgentBindingResolver()


def get_runner_workspace_grant_authorizer() -> RunnerWorkspaceGrantAuthorizer:
    return DatabaseRunnerWorkspaceGrantAuthorizer()


def get_verifier_workspace_grant_authorizer() -> VerifierWorkspaceGrantAuthorizer:
    return DatabaseVerifierWorkspaceGrantAuthorizer()


def get_workspace_claim_admission_policy_resolver(
) -> WorkspaceClaimAdmissionPolicyResolver:
    return DatabaseWorkspaceClaimAdmissionPolicyResolver()


def get_desktop_execution_workspace_root() -> Path:
    return resolve_desktop_execution_workspace_root()


CurrentUser = Annotated[dict, Depends(get_current_user)]
MissionRepositoryDep = Annotated[MissionRepository, Depends(get_mission_repository)]
DesktopExecutionWorkspaceRootDep = Annotated[
    Path,
    Depends(get_desktop_execution_workspace_root),
]
ArtifactByteVerifierDep = Annotated[
    ArtifactByteVerifier,
    Depends(get_artifact_byte_verifier),
]
AgentBindingResolverDep = Annotated[
    AgentBindingResolver,
    Depends(get_agent_binding_resolver),
]
RunnerWorkspaceGrantAuthorizerDep = Annotated[
    RunnerWorkspaceGrantAuthorizer,
    Depends(get_runner_workspace_grant_authorizer),
]
VerifierWorkspaceGrantAuthorizerDep = Annotated[
    VerifierWorkspaceGrantAuthorizer,
    Depends(get_verifier_workspace_grant_authorizer),
]
WorkspaceClaimAdmissionPolicyResolverDep = Annotated[
    WorkspaceClaimAdmissionPolicyResolver,
    Depends(get_workspace_claim_admission_policy_resolver),
]
WorkspaceId = Annotated[str, Query(alias="workspaceId")]
MissionLimit = Annotated[int, Query(ge=1, le=200)]
MissionOffset = Annotated[int, Query(ge=0)]
EventAfterSequence = Annotated[int, Query(alias="afterSequence", ge=0)]
EventLimit = Annotated[int, Query(ge=1, le=200)]
EventPollSeconds = Annotated[
    float, Query(alias="pollSeconds", ge=0.05, le=30.0)
]
EventMaxSeconds = Annotated[float, Query(alias="maxSeconds", ge=0, le=3600)]
WorkUnitLimit = Annotated[int, Query(ge=1, le=200)]
WorkUnitOffset = Annotated[int, Query(ge=0)]
ArtifactLimit = Annotated[int, Query(ge=1, le=200)]
ArtifactOffset = Annotated[int, Query(ge=0)]
EvidenceLimit = Annotated[int, Query(ge=1, le=200)]
EvidenceOffset = Annotated[int, Query(ge=0)]
DecisionLimit = Annotated[int, Query(ge=1, le=200)]
DecisionOffset = Annotated[int, Query(ge=0)]
DecisionStatusFilter = Annotated[
    Literal["PENDING", "RESOLVED", "CANCELLED", "EXPIRED", "ALL"],
    Query(alias="status"),
]
DecisionMissionIdFilter = Annotated[
    str | None,
    Query(alias="missionId", min_length=1, max_length=255),
]
DecisionReasonFilter = Annotated[
    EvaluationPolicyReason | None,
    Query(alias="reasonCode"),
]


def _authorize_human_decision_access(user: dict) -> None:
    if user.get("role") in {"runner", "verifier", "agent", "service"}:
        raise HTTPException(status_code=403, detail="Human Decision access required")


def _authorize_human_contract_access(user: dict) -> None:
    if user.get("role") in {"runner", "verifier", "agent", "service"}:
        raise HTTPException(status_code=403, detail="Human Contract access required")


def _authorize_human_fork_access(user: dict) -> None:
    if user.get("role") in {"runner", "verifier", "agent", "service"}:
        raise HTTPException(status_code=403, detail="Human Mission fork access required")


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


@router.post(
    "/{source_mission_id}/forks",
    status_code=status.HTTP_201_CREATED,
)
async def fork_mission(
    source_mission_id: str,
    request: MissionForkRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    artifact_byte_verifier: ArtifactByteVerifierDep,
    agent_binding_resolver: AgentBindingResolverDep,
) -> dict:
    _authorize_human_fork_access(user)
    await _authorized_mission(
        source_mission_id,
        user=user,
        repository=repository,
    )
    service = MissionService(
        repository,
        artifact_byte_verifier=artifact_byte_verifier,
        agent_binding_resolver=agent_binding_resolver,
    )
    try:
        outcome = await service.fork_mission(
            source_mission_id,
            mission_id=request.id,
            work_unit_id=request.work_unit_id,
            title=request.title,
            objective=request.objective,
            checkpoint_id=request.checkpoint_id,
            artifact_refs=request.artifact_refs,
            expected_outputs=request.expected_outputs,
            required_capabilities=request.required_capabilities,
            agent_id=request.agent_id,
            actor=build_human_actor(user),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except AgentBindingUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ArtifactBytesUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_424_FAILED_DEPENDENCY,
            detail=str(exc),
        ) from exc
    except (AgentBindingNotFoundError, ArtifactIntegrityError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return outcome.to_public_dict()


@router.get("/decisions")
async def list_workspace_decisions(
    workspace_id: WorkspaceId,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    decision_status: DecisionStatusFilter = "PENDING",
    mission_id: DecisionMissionIdFilter = None,
    reason_code: DecisionReasonFilter = None,
    limit: DecisionLimit = 100,
    offset: DecisionOffset = 0,
) -> dict:
    authorize_workspace(user, workspace_id)
    _authorize_human_decision_access(user)
    decisions = await repository.list_workspace_decisions(
        workspace_id,
        status=(None if decision_status == "ALL" else DecisionStatus(decision_status)),
        mission_id=mission_id,
        reason_code=reason_code,
        limit=limit,
        offset=offset,
    )
    return {"decisions": [decision.to_public_dict() for decision in decisions]}


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


@router.post(
    "/{mission_id}/contract/revisions",
    status_code=status.HTTP_201_CREATED,
)
async def revise_contract(
    mission_id: str,
    request: ContractRevisionRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    _authorize_human_contract_access(user)
    await _authorized_mission(mission_id, user=user, repository=repository)
    service = MissionService(repository)
    try:
        contract = await service.revise_contract(
            mission_id,
            expected_version=request.expected_version,
            contract=request.contract,
            reason=request.reason,
            actor=build_human_actor(user),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except ContractRevisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return contract.to_public_dict()


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


async def _authorize_verifier_mission(
    mission_id: str,
    *,
    user: dict,
    repository: MissionRepository,
    grant_authorizer: VerifierWorkspaceGrantAuthorizer,
) -> Mission:
    mission = await repository.get_mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    await _authorize_workspace_verification(
        user,
        mission.workspace_id,
        grant_authorizer=grant_authorizer,
    )
    return mission


async def _authorize_workspace_verification(
    user: dict,
    workspace_id: str,
    *,
    grant_authorizer: VerifierWorkspaceGrantAuthorizer,
) -> None:
    authorize_verifier(user)
    principal_id = str(user.get("id") or "").strip()
    normalized_workspace_id = workspace_id.strip()
    if not normalized_workspace_id:
        raise HTTPException(status_code=403, detail="Workspace identity required")
    if user.get("role") == "admin" or principal_id == normalized_workspace_id:
        return
    if not principal_id:
        raise HTTPException(status_code=403, detail="Verifier identity required")
    try:
        granted = await grant_authorizer.has_verify_grant(
            workspace_id=normalized_workspace_id,
            principal_id=principal_id,
        )
    except VerifierWorkspaceGrantUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Verifier workspace authorization is unavailable",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not granted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verifier workspace grant required",
        )


async def _authorize_execution_work_unit(
    mission_id: str,
    work_unit_id: str,
    *,
    lease_id: str,
    user: dict,
    repository: MissionRepository,
) -> None:
    if user.get("role") != "runner":
        await _authorized_mission(mission_id, user=user, repository=repository)
    work_unit = await repository.get_work_unit(work_unit_id)
    if work_unit is None or work_unit.mission_id != mission_id:
        raise HTTPException(status_code=404, detail="WorkUnit not found")
    if user.get("role") == "runner" and (
        work_unit.lease is None
        or work_unit.lease.id != lease_id
        or work_unit.lease.runner_id != str(user["id"])
    ):
        raise HTTPException(
            status_code=403,
            detail="Active Runner lease ownership required",
        )


def _build_execution_actor(user: dict) -> ActorRef:
    if user.get("role") == "runner":
        return build_runner_actor(user)
    return build_human_actor(user)


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


async def _collect_mission_events(
    repository: MissionRepository,
    mission_id: str,
    *,
    after_sequence: int,
    limit: int,
    seen_event_ids: set[str] | None = None,
) -> tuple[list[dict], int]:
    """Shared events query: mission ledger window + work-unit event window.

    Returns the ordered public dicts and the highest mission-aggregate
    sequence observed (the SSE cursor). When ``seen_event_ids`` is given,
    already-delivered events are dropped (work-unit events are re-listed as
    a bounded window on every poll; clients/streams deduplicate by event_id).
    """
    events = await repository.list_events(
        mission_id,
        after_sequence=after_sequence,
        limit=limit,
    )
    # Harness checkpoint and work-unit lifecycle events live on the
    # work_unit aggregate; merge the latest window so the desktop execution
    # feed sees them. Clients deduplicate by event_id.
    work_unit_events = await repository.list_work_unit_events(
        mission_id,
        limit=limit,
    )
    merged = {
        event.event_id: event
        for event in [*events, *work_unit_events]
    }
    ordered = sorted(
        merged.values(),
        key=lambda event: (event.occurred_at, event.event_id),
    )
    cursor = after_sequence
    public: list[dict] = []
    for event in ordered:
        # Only mission-aggregate sequences advance the SSE cursor; work-unit
        # events carry their own aggregate's sequence numbers.
        if event.aggregate_type.value == "mission":
            cursor = max(cursor, event.sequence)
        if seen_event_ids is not None:
            if event.event_id in seen_event_ids:
                continue
            seen_event_ids.add(event.event_id)
        public.append(event.to_public_dict())
    return public, cursor


@router.get("/{mission_id}/events")
async def list_mission_events(
    mission_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    after_sequence: EventAfterSequence = 0,
    limit: EventLimit = 100,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    public, _cursor = await _collect_mission_events(
        repository,
        mission_id,
        after_sequence=after_sequence,
        limit=limit,
    )
    return {"events": public}


@router.get("/{mission_id}/events/stream")
async def stream_mission_events(
    mission_id: str,
    request: Request,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    after_sequence: EventAfterSequence = 0,
    limit: EventLimit = 100,
    poll_seconds: EventPollSeconds = 1.0,
    max_seconds: EventMaxSeconds = 0,
) -> StreamingResponse:
    """Server-Sent Events stream over the mission event ledger (P3-4a).

    Reuses the ``GET /events`` query logic and polls it every
    ``pollSeconds`` (default 1 s), pushing each new event as one
    ``data: <json>`` frame. The mission-aggregate sequence acts as the
    cursor; a client disconnect ends the stream. ``maxSeconds`` bounds the
    stream server-side (0 = unlimited).
    """
    mission = await _authorized_mission(mission_id, user=user, repository=repository)
    del mission

    async def event_stream() -> AsyncIterator[str]:
        cursor = after_sequence
        seen_event_ids: set[str] = set()
        deadline = time.monotonic() + max_seconds if max_seconds > 0 else None
        while True:
            try:
                batch, cursor = await _collect_mission_events(
                    repository,
                    mission_id,
                    after_sequence=cursor,
                    limit=limit,
                    seen_event_ids=seen_event_ids,
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a failed poll must not kill the stream
                batch = []
            for event in batch:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
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


@router.post("/{mission_id}/guidance")
async def add_mission_guidance(
    mission_id: str,
    request: MissionGuidanceRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    """Push one run-time guidance entry into a Mission (P1-1).

    The entry is stored as a ``mission.guidance.added`` event; the desktop
    runner injects it into the model prompt before the next model call.
    """
    await _authorized_mission(mission_id, user=user, repository=repository)
    service = MissionService(repository)
    try:
        event = await service.add_mission_guidance(
            mission_id,
            content=request.content,
            actor=build_human_actor(user),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return event.to_public_dict()


@router.get("/{mission_id}/changed-files")
async def list_mission_changed_files(
    mission_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    workspace_root: DesktopExecutionWorkspaceRootDep,
) -> dict:
    """Disclose the change set a desktop task produced in HEAD (G7).

    Only ``desktop.task`` Missions have a desktop execution workspace; other
    Missions report an empty change set.
    """
    await _authorized_mission(mission_id, user=user, repository=repository)
    work_units = await repository.list_work_units(mission_id)
    if not any(
        unit.kind == DESKTOP_TASK_WORK_UNIT_KIND for unit in work_units
    ):
        return {"files": []}
    return {"files": collect_desktop_changed_files(workspace_root)}


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


@router.get("/{mission_id}/decisions")
async def list_decisions(
    mission_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    limit: DecisionLimit = 100,
    offset: DecisionOffset = 0,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    decisions = await repository.list_decisions(
        mission_id,
        limit=limit,
        offset=offset,
    )
    return {"decisions": [decision.to_public_dict() for decision in decisions]}


@router.post("/{mission_id}/decisions/{decision_id}/resolve")
async def resolve_decision(
    mission_id: str,
    decision_id: str,
    request: DecisionResolutionRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    _authorize_human_decision_access(user)
    await _authorized_mission(mission_id, user=user, repository=repository)
    service = MissionService(repository)
    try:
        decision, work_unit, mission = await service.resolve_decision(
            mission_id,
            decision_id,
            expected_version=request.expected_version,
            resolution=DecisionResolution(request.resolution),
            rationale=request.rationale,
            actor=build_human_actor(user),
        )
    except DecisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Decision not found") from exc
    except (DecisionConflictError, WorkUnitNotFoundError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "decision": decision.to_public_dict(),
        "workUnit": work_unit.to_public_dict(),
        "mission": mission.to_public_dict(),
    }


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


@router.post("/work-unit-claims")
async def claim_workspace_work_unit(
    request: WorkspaceWorkUnitClaimRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    grant_authorizer: RunnerWorkspaceGrantAuthorizerDep,
    admission_policy_resolver: WorkspaceClaimAdmissionPolicyResolverDep,
) -> dict:
    """Discover and claim one ready WorkUnit in an authorized workspace."""

    await _authorize_workspace_claim(
        user,
        request.workspace_id,
        grant_authorizer=grant_authorizer,
    )
    admission_policy = await _resolve_workspace_claim_admission(
        request.workspace_id,
        resolver=admission_policy_resolver,
    )
    service = MissionService(repository)
    try:
        claimed = await service.claim_workspace_bound_work_unit(
            request.workspace_id,
            agent_id=request.agent_id,
            adapter_type=request.adapter_type,
            supported_work_unit_kinds=request.supported_work_unit_kinds,
            runner_id=str(user["id"]),
            actor=build_runner_actor(user),
            lease_seconds=request.lease_seconds,
            admission_policy=admission_policy,
        )
    except WorkspaceClaimAdmissionUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return claimed.to_public_dict()


@router.post("/verification-work-items/discover")
async def discover_verification_work(
    request: WorkspaceVerificationDiscoveryRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    grant_authorizer: VerifierWorkspaceGrantAuthorizerDep,
) -> dict:
    """Return verifier context; inconclusive policy opens a Mission Decision."""

    await _authorize_workspace_verification(
        user,
        request.workspace_id,
        grant_authorizer=grant_authorizer,
    )
    service = MissionService(repository)
    try:
        discovered = await service.discover_workspace_verification_work(
            request.workspace_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return discovered.to_public_dict()


async def _resolve_workspace_claim_admission(
    workspace_id: str,
    *,
    resolver: WorkspaceClaimAdmissionPolicyResolver,
) -> WorkspaceClaimAdmissionPolicy:
    try:
        return await resolver.resolve(workspace_id=workspace_id)
    except WorkspaceClaimAdmissionDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except WorkspaceClaimAdmissionUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


async def _authorize_workspace_claim(
    user: dict,
    workspace_id: str,
    *,
    grant_authorizer: RunnerWorkspaceGrantAuthorizer,
) -> None:
    role = user.get("role")
    principal_id = str(user.get("id") or "").strip()
    if role == "admin":
        return
    if role != "runner":
        raise HTTPException(status_code=403, detail="Runner access required")
    if principal_id == workspace_id:
        return
    if not principal_id:
        raise HTTPException(status_code=403, detail="Runner identity required")
    try:
        granted = await grant_authorizer.has_claim_grant(
            workspace_id=workspace_id,
            principal_id=principal_id,
        )
    except RunnerWorkspaceGrantUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not granted:
        raise HTTPException(
            status_code=403,
            detail="Runner workspace claim grant required",
        )


@router.post("/{mission_id}/work-unit-claims")
async def claim_delegated_work_unit(
    mission_id: str,
    request: WorkUnitClaimRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    admission_policy_resolver: WorkspaceClaimAdmissionPolicyResolverDep,
) -> dict:
    """Claim one ready WorkUnit for an explicit Runner binding."""
    mission = await _authorized_mission(
        mission_id,
        user=user,
        repository=repository,
    )
    admission_policy = await _resolve_workspace_claim_admission(
        mission.workspace_id,
        resolver=admission_policy_resolver,
    )
    service = MissionService(repository)
    try:
        claimed = await service.claim_bound_work_unit(
            mission_id,
            agent_id=request.agent_id,
            adapter_type=request.adapter_type,
            runner_id=str(user["id"]),
            actor=build_runner_actor(user),
            lease_seconds=request.lease_seconds,
            admission_policy=admission_policy,
        )
    except WorkspaceClaimAdmissionUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return claimed.to_public_dict()


@router.post(
    "/{mission_id}/work-units/{work_unit_id}/execution-context",
)
async def get_claimed_execution_context(
    mission_id: str,
    work_unit_id: str,
    request: WorkUnitExecutionContextRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    """Return a lease-fenced context snapshot for a controlled root."""
    await _authorize_execution_work_unit(
        mission_id,
        work_unit_id,
        lease_id=request.lease_id,
        user=user,
        repository=repository,
    )
    service = MissionService(repository)
    try:
        context = await service.get_claimed_execution_context(
            mission_id,
            work_unit_id,
            lease_id=request.lease_id,
            runner_id=str(user["id"]),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"executionContext": context.to_public_dict()}


@router.post("/{mission_id}/work-units/{work_unit_id}/start")
async def start_work_unit(
    mission_id: str,
    work_unit_id: str,
    request: WorkUnitStartRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    await _authorize_execution_work_unit(
        mission_id,
        work_unit_id,
        lease_id=request.lease_id,
        user=user,
        repository=repository,
    )
    service = MissionService(repository)
    try:
        started = await service.start_work_unit(
            mission_id,
            work_unit_id,
            lease_id=request.lease_id,
            runner_id=str(user["id"]),
            actor=_build_execution_actor(user),
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
    await _authorize_execution_work_unit(
        mission_id,
        work_unit_id,
        lease_id=request.lease_id,
        user=user,
        repository=repository,
    )
    service = MissionService(repository)
    try:
        renewed = await service.heartbeat_work_unit(
            mission_id,
            work_unit_id,
            lease_id=request.lease_id,
            runner_id=str(user["id"]),
            actor=_build_execution_actor(user),
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
    "/{mission_id}/work-units/{work_unit_id}/checkpoints",
    status_code=status.HTTP_201_CREATED,
)
async def record_execution_checkpoint(
    mission_id: str,
    work_unit_id: str,
    request: ExecutionCheckpointCreateRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    await _authorize_execution_work_unit(
        mission_id,
        work_unit_id,
        lease_id=request.lease_id,
        user=user,
        repository=repository,
    )
    service = MissionService(repository)
    try:
        checkpoint = await service.record_execution_checkpoint(
            mission_id,
            work_unit_id,
            checkpoint_id=request.id,
            lease_id=request.lease_id,
            runner_id=str(user["id"]),
            sequence=request.sequence,
            phase=request.phase,
            iteration=request.iteration,
            tool_calls=request.tool_calls,
            prompt_tokens=request.prompt_tokens,
            completion_tokens=request.completion_tokens,
            model_cost=request.model_cost,
            terminal=request.terminal,
            failure_reason=request.failure_reason,
            tool_name=request.tool_name,
            tool_success=request.tool_success,
            actor=_build_execution_actor(user),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except WorkUnitNotFoundError as exc:
        raise HTTPException(status_code=404, detail="WorkUnit not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return checkpoint.to_public_dict()


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
    await _authorize_execution_work_unit(
        mission_id,
        work_unit_id,
        lease_id=request.lease_id,
        user=user,
        repository=repository,
    )
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
            actor=_build_execution_actor(user),
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
    await _authorize_execution_work_unit(
        mission_id,
        work_unit_id,
        lease_id=request.lease_id,
        user=user,
        repository=repository,
    )
    service = MissionService(repository)
    runner_id = str(user["id"])
    actor = _build_execution_actor(user)
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
    grant_authorizer: VerifierWorkspaceGrantAuthorizerDep,
) -> dict:
    authorize_verifier(user)
    if user.get("role") != "admin" and request.verifier_id != str(user["id"]):
        raise HTTPException(status_code=403, detail="Verifier identity mismatch")
    await _authorize_verifier_mission(
        mission_id,
        user=user,
        repository=repository,
        grant_authorizer=grant_authorizer,
    )
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
