from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.domain import (
    ActorRef,
    ActorType,
    Artifact,
    ArtifactKind,
    ArtifactRef,
    ArtifactRetention,
    ArtifactSensitivity,
    Decision,
    DecisionResolution,
    DecisionStatus,
    EventEnvelope,
    Evidence,
    EvidenceVerdict,
    ExecutionCheckpoint,
    ExecutionCheckpointPhase,
    Lease,
    Mission,
    MissionContract,
    MissionSource,
    MissionSourceType,
    MissionStatus,
    OutputSpec,
    VerifierRef,
    WorkUnit,
    WorkUnitStatus,
    transition_mission,
    transition_work_unit,
)
from app.repositories import MissionRepository
from app.services.agent_binding_service import (
    AgentBindingResolver,
    AgentBindingUnavailableError,
)
from app.services.artifact_integrity_service import (
    ArtifactBytesUnavailableError,
    ArtifactByteVerifier,
)
from app.services.evidence_integrity_service import (
    EvidenceIntegrityHasher,
    EvidenceIntegrityMaterial,
    Sha256EvidenceIntegrityHasher,
)
from app.services.verification_evaluator_service import (
    StrictVerificationEvaluator,
    VerificationEvaluationResult,
    VerificationEvaluator,
    canonicalize_artifact_byte_verifications,
)
from app.services.verification_policy_service import (
    ArtifactSetEvaluationPlan,
    EvaluationPolicyDecision,
    StrictVerificationPolicyResolver,
    VerificationPolicyResolver,
)
from app.services.workspace_admission_service import (
    WorkspaceClaimAdmissionPolicy,
    WorkspaceClaimAdmissionUnavailableError,
    WorkspaceClaimStatus,
)

_A2A_OUTBOUND_ADAPTER = "a2a.outbound"
_MAX_VERIFICATION_ARTIFACTS = 200

# Root WorkUnit kind derived for desktop local-runner tasks on manual
# Missions. Only the env-gated desktop local runner derivation creates
# this kind; every other claim shape is unchanged.
DESKTOP_TASK_WORK_UNIT_KIND = "desktop.task"
_DESKTOP_TASK_WORK_UNIT_KIND = DESKTOP_TASK_WORK_UNIT_KIND
_VERIFICATION_ARTIFACT_FIELDS = frozenset(
    {
        "id",
        "attempt",
        "kind",
        "digest",
        "contentAddress",
        "mediaType",
        "sizeBytes",
        "sourceRepository",
        "baseCommit",
        "sensitivity",
    }
)


def build_human_actor(user: dict) -> ActorRef:
    return ActorRef(
        type="human",
        id=str(user["id"]),
        display_name=str(user["name"]) if user.get("name") else None,
    )


def build_verifier_actor(user: dict) -> ActorRef:
    return ActorRef(
        type=ActorType.VERIFIER,
        id=str(user["id"]),
        display_name=str(user["name"]) if user.get("name") else None,
    )


def build_runner_actor(user: dict) -> ActorRef:
    return ActorRef(
        type=ActorType.RUNNER,
        id=str(user["id"]),
        display_name=str(user["name"]) if user.get("name") else None,
    )


def new_identifier(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _checkpoint_event_payload(
    checkpoint: ExecutionCheckpoint,
    *,
    tool_name: str | None = None,
    tool_success: bool | None = None,
) -> dict[str, Any]:
    """Observability payload for one ``work_unit.checkpoint.recorded`` event.

    Content-minimized on purpose: durable checkpoints stay content-free, so
    the desktop execution feed renders from these few factual fields only.
    """
    payload: dict[str, Any] = {
        "checkpointId": checkpoint.id,
        "attempt": checkpoint.attempt,
        "sequence": checkpoint.sequence,
        "phase": checkpoint.phase.value,
        "iteration": checkpoint.iteration,
        "toolCalls": checkpoint.tool_calls,
        "promptTokens": checkpoint.prompt_tokens,
        "completionTokens": checkpoint.completion_tokens,
        "terminal": checkpoint.terminal,
        "stateDigest": checkpoint.state_digest,
    }
    if checkpoint.failure_reason is not None:
        payload["failureReason"] = checkpoint.failure_reason
    if tool_name is not None:
        payload["toolName"] = tool_name
    if tool_success is not None:
        payload["toolSuccess"] = tool_success
    return payload


class MissionNotFoundError(LookupError):
    pass


class WorkUnitNotFoundError(LookupError):
    pass


class WorkUnitNotReadyError(ValueError):
    pass


class LeaseOwnershipError(ValueError):
    pass


class LeaseExpiredError(ValueError):
    pass


class AgentBindingNotFoundError(ValueError):
    pass


class DecisionNotFoundError(LookupError):
    pass


class DecisionConflictError(ValueError):
    pass


class ContractRevisionConflictError(ValueError):
    def __init__(self, *, expected_version: int, current_version: int) -> None:
        self.expected_version = expected_version
        self.current_version = current_version
        super().__init__(
            "contract revision version conflict "
            f"(expected={expected_version}, current={current_version})"
        )


@dataclass(frozen=True, slots=True)
class ClaimedExecutionContext:
    mission: Mission
    contract: MissionContract
    work_unit: WorkUnit

    def to_public_dict(self) -> dict:
        return {
            "version": 1,
            "mission": self.mission.to_public_dict(),
            "contract": self.contract.to_public_dict(),
            "workUnit": self.work_unit.to_public_dict(),
        }


@dataclass(frozen=True, slots=True)
class VerificationContext:
    mission: Mission
    contract: MissionContract
    work_unit: WorkUnit
    artifacts: tuple[Artifact, ...]
    evaluation_policy: EvaluationPolicyDecision

    def to_public_dict(self) -> dict:
        return {
            "version": 3,
            "mission": {
                "id": self.mission.id,
                "title": self.mission.title,
                "objective": self.mission.objective,
            },
            "contract": {
                "id": self.contract.id,
                "version": self.contract.version,
                "acceptanceCriteria": [
                    criterion.to_public_dict()
                    for criterion in self.contract.acceptance_criteria
                ],
            },
            "workUnit": {
                "id": self.work_unit.id,
                "kind": self.work_unit.kind,
                "inputRefs": [
                    artifact_ref.to_public_dict()
                    for artifact_ref in self.work_unit.input_refs
                ],
                "expectedOutputs": [
                    output.to_public_dict()
                    for output in self.work_unit.expected_outputs
                ],
                "status": self.work_unit.status.value,
                "attempt": self.work_unit.attempt,
            },
            "artifacts": [
                {
                    key: value
                    for key, value in artifact.to_public_dict().items()
                    if key in _VERIFICATION_ARTIFACT_FIELDS
                }
                for artifact in self.artifacts
            ],
            "evaluationPolicy": self.evaluation_policy.to_public_dict(),
        }


@dataclass(frozen=True, slots=True)
class VerificationDiscoveryOutcome:
    """Transient verifier result; inconclusive policy creates a durable Decision."""

    context: VerificationContext | None

    def to_public_dict(self) -> dict:
        return {
            "discoveryStatus": "ready" if self.context is not None else "idle",
            "verificationContext": (
                self.context.to_public_dict() if self.context is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class DecisionExpiryOutcome:
    decision: Decision | None
    work_unit: WorkUnit | None
    mission: Mission | None

    @property
    def expired(self) -> bool:
        return self.decision is not None


@dataclass(frozen=True, slots=True)
class WorkUnitClaimOutcome:
    """Transient claim result; it is not durable scheduling state."""

    status: WorkspaceClaimStatus
    work_unit: WorkUnit | None

    def __post_init__(self) -> None:
        has_work_unit = self.work_unit is not None
        if has_work_unit != (self.status == WorkspaceClaimStatus.CLAIMED):
            raise ValueError("claim status and WorkUnit payload are inconsistent")

    def to_public_dict(self) -> dict:
        return {
            "claimStatus": self.status.value,
            "workUnit": (
                self.work_unit.to_public_dict() if self.work_unit is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class MissionForkOutcome:
    mission: Mission
    work_unit: WorkUnit

    def to_public_dict(self) -> dict:
        return {
            "mission": self.mission.to_public_dict(),
            "workUnit": self.work_unit.to_public_dict(),
        }

