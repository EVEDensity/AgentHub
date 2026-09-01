"""v1 missions/_deps.py — shared deps, aliases, and helpers."""

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


__all__ = [
    "get_mission_repository",
    "get_artifact_byte_verifier",
    "get_agent_binding_resolver",
    "get_runner_workspace_grant_authorizer",
    "get_verifier_workspace_grant_authorizer",
    "get_workspace_claim_admission_policy_resolver",
    "get_desktop_execution_workspace_root",
    "router",
    "CurrentUser",
    "MissionRepositoryDep",
    "DesktopExecutionWorkspaceRootDep",
    "ArtifactByteVerifierDep",
    "AgentBindingResolverDep",
    "RunnerWorkspaceGrantAuthorizerDep",
    "VerifierWorkspaceGrantAuthorizerDep",
    "WorkspaceClaimAdmissionPolicyResolverDep",
    "WorkspaceId",
    "MissionLimit",
    "MissionOffset",
    "EventAfterSequence",
    "EventLimit",
    "EventPollSeconds",
    "EventMaxSeconds",
    "WorkUnitLimit",
    "WorkUnitOffset",
    "ArtifactLimit",
    "ArtifactOffset",
    "EvidenceLimit",
    "EvidenceOffset",
    "DecisionLimit",
    "DecisionOffset",
    "DecisionStatusFilter",
    "DecisionMissionIdFilter",
    "DecisionReasonFilter",
    "_authorize_human_decision_access",
    "_authorize_human_contract_access",
    "_authorize_human_fork_access",
    "_authorized_mission",
    "_authorize_verifier_mission",
    "_authorize_workspace_verification",
    "_authorize_execution_work_unit",
    "_build_execution_actor",
    "_run_lifecycle_command",
    "_collect_mission_events",
    "_resolve_workspace_claim_admission",
    "_authorize_workspace_claim",
    "_run_work_unit_execution_command",
    "APIRouter",
    "ActorRef",
    "AgentBindingNotFoundError",
    "AgentBindingResolver",
    "AgentBindingUnavailableError",
    "Annotated",
    "ArtifactByteVerifier",
    "ArtifactBytesUnavailableError",
    "ArtifactCreateRequest",
    "ArtifactIntegrityError",
    "AsyncIterator",
    "ContractRevisionConflictError",
    "ContractRevisionRequest",
    "DESKTOP_TASK_WORK_UNIT_KIND",
    "DatabaseAgentBindingResolver",
    "DatabaseRunnerWorkspaceGrantAuthorizer",
    "DatabaseVerifierWorkspaceGrantAuthorizer",
    "DatabaseWorkspaceClaimAdmissionPolicyResolver",
    "DecisionConflictError",
    "DecisionNotFoundError",
    "DecisionResolution",
    "DecisionResolutionRequest",
    "DecisionStatus",
    "Depends",
    "EvaluationPolicyReason",
    "EvidenceVerdict",
    "ExecutionCheckpointCreateRequest",
    "HTTPException",
    "InvalidStateTransition",
    "Literal",
    "Mission",
    "MissionCreateRequest",
    "MissionForkRequest",
    "MissionGuidanceRequest",
    "MissionNotFoundError",
    "MissionRepository",
    "MissionService",
    "Path",
    "Query",
    "Request",
    "RunnerWorkspaceGrantAuthorizer",
    "RunnerWorkspaceGrantUnavailableError",
    "StreamingResponse",
    "VerifierWorkspaceGrantAuthorizer",
    "VerifierWorkspaceGrantUnavailableError",
    "WorkUnitClaimRequest",
    "WorkUnitCompletionRequest",
    "WorkUnitCreateRequest",
    "WorkUnitDelegationRequest",
    "WorkUnitExecutionContextRequest",
    "WorkUnitExecutionRequest",
    "WorkUnitHeartbeatRequest",
    "WorkUnitLeaseRequest",
    "WorkUnitNotFoundError",
    "WorkUnitStartRequest",
    "WorkUnitVerificationRequest",
    "WorkspaceClaimAdmissionDeniedError",
    "WorkspaceClaimAdmissionPolicy",
    "WorkspaceClaimAdmissionPolicyResolver",
    "WorkspaceClaimAdmissionUnavailableError",
    "WorkspaceVerificationDiscoveryRequest",
    "WorkspaceWorkUnitClaimRequest",
    "annotations",
    "asyncio",
    "authorize_verifier",
    "authorize_workspace",
    "build_artifact_byte_verifier",
    "build_human_actor",
    "build_runner_actor",
    "build_verifier_actor",
    "collect_desktop_changed_files",
    "get_current_user",
    "json",
    "resolve_desktop_execution_workspace_root",
    "status",
    "time",
]


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


router = APIRouter(prefix="/missions", tags=["missions"])

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
