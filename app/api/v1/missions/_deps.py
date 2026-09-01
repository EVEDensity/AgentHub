from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Query

from app.domain import EvaluationPolicyReason
from app.repositories import MissionRepository
from app.services.agent_binding_service import (
    AgentBindingResolver, DatabaseAgentBindingResolver,
)
from app.services.artifact_integrity_service import (
    ArtifactByteVerifier, build_artifact_byte_verifier,
)
from app.services.auth_service import get_current_user
from app.services.desktop_changed_files_service import (
    resolve_desktop_execution_workspace_root,
)
from app.services.workspace_access_service import (
    DatabaseRunnerWorkspaceGrantAuthorizer,
    DatabaseVerifierWorkspaceGrantAuthorizer,
    RunnerWorkspaceGrantAuthorizer,
    VerifierWorkspaceGrantAuthorizer,
)
from app.services.workspace_admission_service import (
    DatabaseWorkspaceClaimAdmissionPolicyResolver,
    WorkspaceClaimAdmissionPolicyResolver,
)

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
