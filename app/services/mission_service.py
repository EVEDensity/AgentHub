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


class MissionService:
    def __init__(
        self,
        repository: MissionRepository | None = None,
        *,
        artifact_byte_verifier: ArtifactByteVerifier | None = None,
        agent_binding_resolver: AgentBindingResolver | None = None,
        verification_policy_resolver: VerificationPolicyResolver | None = None,
        verification_evaluator: VerificationEvaluator | None = None,
        evidence_integrity_hasher: EvidenceIntegrityHasher | None = None,
    ) -> None:
        self._repository = repository or MissionRepository()
        self._artifact_byte_verifier = artifact_byte_verifier
        self._agent_binding_resolver = agent_binding_resolver
        self._verification_policy_resolver = (
            verification_policy_resolver or StrictVerificationPolicyResolver()
        )
        self._verification_evaluator = (
            verification_evaluator or StrictVerificationEvaluator()
        )
        self._evidence_integrity_hasher = (
            evidence_integrity_hasher or Sha256EvidenceIntegrityHasher()
        )

    async def create_mission(
        self,
        *,
        mission_id: str | None,
        workspace_id: str,
        title: str,
        objective: str,
        source: MissionSource,
        contract: MissionContract,
        actor: ActorRef,
    ) -> Mission:
        occurred_at = datetime.now(timezone.utc)
        mission = Mission(
            id=mission_id or new_identifier("mis"),
            workspace_id=workspace_id,
            title=title,
            objective=objective,
            source=source,
            contract_id=contract.id,
            contract_version=contract.version,
            status="READY",
            plan_version=0,
            created_by=actor,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        event = EventEnvelope(
            event_id=new_identifier("evt"),
            aggregate_type="mission",
            aggregate_id=mission.id,
            sequence=1,
            event_type="mission.lifecycle.created",
            actor=actor,
            occurred_at=occurred_at,
            correlation_id=mission.id,
            payload={
                "contractId": contract.id,
                "contractVersion": contract.version,
                "status": mission.status.value,
            },
            schema_version=1,
        )
        async with self._repository.transaction() as repository:
            await repository.lock_contract_lineage(contract.id)
            lineage_workspace = await repository.get_contract_lineage_workspace(
                contract.id
            )
            existing_contract = await repository.get_contract(
                contract.id,
                contract.version,
            )
            if lineage_workspace is None:
                if existing_contract is not None:
                    raise ValueError("contract lineage workspace ownership is missing")
                if contract.version != 1:
                    raise ValueError(
                        "new contract lineages must start at version 1"
                    )
                await repository.add_contract_lineage(contract.id, workspace_id)
                await repository.add_contract(contract)
            elif lineage_workspace != workspace_id:
                raise ValueError("contract lineage belongs to another workspace")
            elif existing_contract is None:
                raise ValueError(
                    "contract revisions require the controlled revision command"
                )
            elif existing_contract != contract:
                raise ValueError(
                    "contract revision already exists with different content"
                )
            await repository.add_mission(mission)
            await repository.append_event(event)
        return mission

    async def fork_mission(
        self,
        source_mission_id: str,
        *,
        mission_id: str,
        work_unit_id: str,
        title: str,
        objective: str,
        checkpoint_id: str,
        artifact_refs: list[ArtifactRef],
        expected_outputs: list[OutputSpec],
        required_capabilities: list[str],
        agent_id: str,
        actor: ActorRef,
    ) -> MissionForkOutcome:
        if actor.type != ActorType.HUMAN:
            raise ValueError("only human actors can fork Missions")
        if mission_id == source_mission_id:
            raise ValueError("fork Mission id must differ from the source Mission")
        if not artifact_refs:
            raise WorkUnitNotReadyError("Mission fork requires Artifact references")
        if len(artifact_refs) > _MAX_VERIFICATION_ARTIFACTS:
            raise WorkUnitNotReadyError("Mission fork has too many Artifact references")

        (
            source,
            source_work_unit,
            checkpoint,
            contract,
            artifacts,
        ) = await self._load_fork_source(
            self._repository,
            source_mission_id=source_mission_id,
            checkpoint_id=checkpoint_id,
            artifact_refs=artifact_refs,
        )
        if work_unit_id == source_work_unit.id:
            raise ValueError("fork WorkUnit id must differ from the source WorkUnit")
        allowed_capabilities = {
            grant.capability for grant in contract.allowed_capabilities
        }
        unsupported = sorted(set(required_capabilities) - allowed_capabilities)
        if unsupported:
            raise WorkUnitNotReadyError(
                "fork WorkUnit requires capabilities outside the source Contract: "
                + ", ".join(unsupported)
            )

        resolver = self._agent_binding_resolver
        if resolver is None:
            raise AgentBindingUnavailableError(
                "workspace-scoped Agent binding resolver is not configured"
            )
        binding = await resolver.resolve(
            scope_id=source.workspace_id,
            agent_id=agent_id,
        )
        if binding is None:
            raise AgentBindingNotFoundError(
                f"Agent is not available in the Mission scope: {agent_id}"
            )
        if binding.adapter_type == _A2A_OUTBOUND_ADAPTER:
            raise WorkUnitNotReadyError(
                "Mission fork requires a non-outbound execution adapter"
            )
        missing_binding_capabilities = sorted(
            set(required_capabilities) - set(binding.capabilities)
        )
        if missing_binding_capabilities:
            raise WorkUnitNotReadyError(
                "Agent binding does not grant required capabilities: "
                + ", ".join(missing_binding_capabilities)
            )

        source_descriptor = MissionSource(
            type=MissionSourceType.MISSION_FORK,
            reference=source.id,
            external_id=checkpoint.id,
        )
        occurred_at = datetime.now(timezone.utc)
        candidate_mission = Mission(
            id=mission_id,
            workspace_id=source.workspace_id,
            title=title,
            objective=objective,
            source=source_descriptor,
            contract_id=contract.id,
            contract_version=contract.version,
            status=MissionStatus.READY,
            plan_version=0,
            created_by=actor,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        candidate_work_unit = WorkUnit(
            id=work_unit_id,
            mission_id=mission_id,
            assigned_agent_id=binding.agent_id,
            kind="mission.fork",
            dependencies=[],
            input_refs=artifact_refs,
            expected_outputs=expected_outputs,
            required_capabilities=required_capabilities,
            assigned_adapter=binding.adapter_type,
            status=WorkUnitStatus.PENDING,
            attempt=0,
        )
        replay = await self._match_existing_fork(
            self._repository,
            mission=candidate_mission,
            work_unit=candidate_work_unit,
        )
        if replay is not None:
            return replay

        verifier = self._artifact_byte_verifier
        if verifier is None:
            raise ArtifactBytesUnavailableError(
                "artifact byte verifier is not configured"
            )
        byte_verifications = await verifier.verify_all(artifacts)
        canonicalize_artifact_byte_verifications(
            tuple(artifacts),
            tuple(byte_verifications),
        )

        async with self._repository.transaction() as repository:
            (
                current_source,
                current_work_unit,
                current_checkpoint,
                current_contract,
                current_artifacts,
            ) = await self._load_fork_source(
                repository,
                source_mission_id=source_mission_id,
                checkpoint_id=checkpoint_id,
                artifact_refs=artifact_refs,
                for_update=True,
            )
            if (
                current_source.workspace_id != source.workspace_id
                or current_work_unit != source_work_unit
                or current_checkpoint != checkpoint
                or current_contract != contract
                or current_artifacts != artifacts
            ):
                raise WorkUnitNotReadyError(
                    "Mission fork source changed during Artifact verification"
                )
            canonicalize_artifact_byte_verifications(
                tuple(current_artifacts),
                tuple(byte_verifications),
            )

            replay = await self._match_existing_fork(
                repository,
                mission=candidate_mission,
                work_unit=candidate_work_unit,
                for_update=True,
            )
            if replay is not None:
                return replay

            mission_event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="mission",
                aggregate_id=candidate_mission.id,
                sequence=1,
                event_type="mission.lifecycle.created",
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=candidate_mission.id,
                payload={
                    "contractId": contract.id,
                    "contractVersion": contract.version,
                    "status": candidate_mission.status.value,
                    "sourceMissionId": source.id,
                    "sourceWorkUnitId": source_work_unit.id,
                    "sourceAttempt": checkpoint.attempt,
                    "sourceCheckpointId": checkpoint.id,
                    "artifactRefs": [ref.to_public_dict() for ref in artifact_refs],
                },
                schema_version=1,
            )
            work_unit_event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="work_unit",
                aggregate_id=candidate_work_unit.id,
                sequence=1,
                event_type="work_unit.lifecycle.created",
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=candidate_mission.id,
                causation_id=mission_event.event_id,
                payload={
                    "kind": candidate_work_unit.kind,
                    "missionId": candidate_mission.id,
                    "status": candidate_work_unit.status.value,
                    "assignedAgentId": binding.agent_id,
                    "assignedAdapter": binding.adapter_type,
                    "inputRefs": [ref.to_public_dict() for ref in artifact_refs],
                },
                schema_version=1,
            )
            await repository.add_mission(candidate_mission)
            await repository.add_work_unit(candidate_work_unit)
            await repository.append_event(mission_event)
            await repository.append_event(work_unit_event)
        return MissionForkOutcome(
            mission=candidate_mission,
            work_unit=candidate_work_unit,
        )

    async def revise_contract(
        self,
        mission_id: str,
        *,
        expected_version: int,
        contract: MissionContract,
        reason: str,
        actor: ActorRef,
    ) -> MissionContract:
        if actor.type != ActorType.HUMAN:
            raise ValueError("only human actors can revise Contracts")
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("contract revision reason is required")

        async with self._repository.transaction() as repository:
            mission = await repository.get_mission(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)
            if contract.id != mission.contract_id:
                raise ValueError("contract revision lineage does not match Mission")

            await repository.lock_contract_lineage(mission.contract_id)
            lineage_workspace = await repository.get_contract_lineage_workspace(
                mission.contract_id
            )
            if lineage_workspace is None:
                raise ValueError("contract lineage workspace ownership is missing")
            if lineage_workspace != mission.workspace_id:
                raise ValueError("contract lineage belongs to another workspace")
            latest = await repository.get_latest_contract(mission.contract_id)
            if latest is None:
                raise ValueError("mission contract lineage not found")
            if latest.version != expected_version:
                raise ContractRevisionConflictError(
                    expected_version=expected_version,
                    current_version=latest.version,
                )
            if contract.version != latest.version + 1:
                raise ValueError("contract revision must increment version by one")

            await repository.add_contract(contract)
            sequence = (
                await repository.get_last_event_sequence(
                    contract.id,
                    aggregate_type="mission_contract",
                )
                + 1
            )
            await repository.append_event(
                EventEnvelope(
                    event_id=new_identifier("evt"),
                    aggregate_type="mission_contract",
                    aggregate_id=contract.id,
                    sequence=sequence,
                    event_type="contract.lifecycle.revised",
                    actor=actor,
                    occurred_at=datetime.now(timezone.utc),
                    correlation_id=mission.id,
                    payload={
                        "sourceMissionId": mission.id,
                        "previousVersion": latest.version,
                        "version": contract.version,
                        "reason": normalized_reason,
                    },
                    schema_version=1,
                )
            )
        return contract

    async def start_mission(self, mission_id: str, *, actor: ActorRef) -> Mission:
        return await self._transition_mission(
            mission_id,
            target=MissionStatus.RUNNING,
            event_type="mission.lifecycle.started",
            actor=actor,
        )

    async def cancel_mission(self, mission_id: str, *, actor: ActorRef) -> Mission:
        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)

            occurred_at = datetime.now(timezone.utc)
            updated_mission = transition_mission(
                mission,
                MissionStatus.CANCELLED,
                occurred_at=occurred_at,
            )
            mission_sequence = await repository.get_last_event_sequence(mission_id) + 1
            mission_event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="mission",
                aggregate_id=mission_id,
                sequence=mission_sequence,
                event_type="mission.lifecycle.cancelled",
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission_id,
                payload={
                    "previousStatus": mission.status.value,
                    "status": updated_mission.status.value,
                },
                schema_version=1,
            )
            work_units = await repository.list_work_units_for_update(mission_id)
            pending_decisions = await repository.list_pending_decisions_for_update(
                mission_id
            )
            await repository.update_mission(updated_mission)
            await repository.append_event(mission_event)

            for decision in pending_decisions:
                cancelled_decision = Decision.model_validate(
                    {
                        **decision.model_dump(),
                        "status": DecisionStatus.CANCELLED,
                        "version": decision.version + 1,
                        "rationale": "Mission cancelled while Decision was pending.",
                        "resolved_by": actor,
                        "resolved_at": occurred_at,
                    }
                )
                decision_sequence = (
                    await repository.get_last_event_sequence(
                        decision.id,
                        aggregate_type="decision",
                    )
                    + 1
                )
                decision_event = EventEnvelope(
                    event_id=new_identifier("evt"),
                    aggregate_type="decision",
                    aggregate_id=decision.id,
                    sequence=decision_sequence,
                    event_type="decision.lifecycle.cancelled",
                    actor=actor,
                    occurred_at=occurred_at,
                    correlation_id=mission_id,
                    causation_id=mission_event.event_id,
                    payload={
                        "previousStatus": decision.status.value,
                        "status": cancelled_decision.status.value,
                        "previousVersion": decision.version,
                        "version": cancelled_decision.version,
                    },
                    schema_version=1,
                )
                await repository.update_decision(cancelled_decision)
                await repository.append_event(decision_event)

            cancellable_statuses = {
                WorkUnitStatus.PENDING,
                WorkUnitStatus.LEASED,
                WorkUnitStatus.RUNNING,
                WorkUnitStatus.VERIFYING,
                WorkUnitStatus.WAITING,
                WorkUnitStatus.RETRYING,
            }
            for work_unit in work_units:
                if work_unit.status not in cancellable_statuses:
                    continue
                updated_work_unit = transition_work_unit(
                    work_unit,
                    WorkUnitStatus.CANCELLED,
                    occurred_at=occurred_at,
                )
                work_unit_sequence = (
                    await repository.get_last_event_sequence(
                        work_unit.id,
                        aggregate_type="work_unit",
                    )
                    + 1
                )
                work_unit_event = EventEnvelope(
                    event_id=new_identifier("evt"),
                    aggregate_type="work_unit",
                    aggregate_id=work_unit.id,
                    sequence=work_unit_sequence,
                    event_type="work_unit.lifecycle.cancelled",
                    actor=actor,
                    occurred_at=occurred_at,
                    correlation_id=mission_id,
                    causation_id=mission_event.event_id,
                    payload={
                        "previousStatus": work_unit.status.value,
                        "status": updated_work_unit.status.value,
                        "reason": "mission cancelled",
                    },
                    schema_version=1,
                )
                await repository.update_work_unit(updated_work_unit)
                await repository.append_event(work_unit_event)
        return updated_mission

    async def fail_mission(
        self,
        mission_id: str,
        *,
        actor: ActorRef,
        reason: str,
    ) -> Mission:
        return await self._transition_mission(
            mission_id,
            target=MissionStatus.FAILED,
            event_type="mission.lifecycle.failed",
            actor=actor,
            reason=reason,
        )

    async def _transition_mission(
        self,
        mission_id: str,
        *,
        target: MissionStatus,
        event_type: str,
        actor: ActorRef,
        reason: str | None = None,
    ) -> Mission:
        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)

            occurred_at = datetime.now(timezone.utc)
            updated = transition_mission(mission, target, occurred_at=occurred_at)
            sequence = await repository.get_last_event_sequence(mission_id) + 1
            payload = {
                "previousStatus": mission.status.value,
                "status": updated.status.value,
            }
            if reason is not None:
                payload["reason"] = reason
            event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="mission",
                aggregate_id=mission_id,
                sequence=sequence,
                event_type=event_type,
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission_id,
                payload=payload,
                schema_version=1,
            )
            await repository.update_mission(updated)
            await repository.append_event(event)
        return updated

    async def _fail_mission_for_work_unit(
        self,
        repository: MissionRepository,
        mission: Mission,
        *,
        work_unit_id: str,
        actor: ActorRef,
        reason: str,
        occurred_at: datetime,
        causation_id: str,
    ) -> Mission:
        if mission.status == MissionStatus.FAILED:
            return mission
        if mission.status not in {MissionStatus.RUNNING, MissionStatus.VERIFYING}:
            raise WorkUnitNotReadyError(
                f"failed WorkUnit cannot replace {mission.status.value} Mission"
            )
        updated = transition_mission(
            mission,
            MissionStatus.FAILED,
            occurred_at=occurred_at,
        )
        sequence = await repository.get_last_event_sequence(mission.id) + 1
        event = EventEnvelope(
            event_id=new_identifier("evt"),
            aggregate_type="mission",
            aggregate_id=mission.id,
            sequence=sequence,
            event_type="mission.lifecycle.failed",
            actor=actor,
            occurred_at=occurred_at,
            correlation_id=mission.id,
            causation_id=causation_id,
            payload={
                "previousStatus": mission.status.value,
                "status": updated.status.value,
                "workUnitId": work_unit_id,
                "reason": reason,
            },
            schema_version=1,
        )
        await repository.update_mission(updated)
        await repository.append_event(event)
        return updated

    async def create_work_unit(
        self,
        mission_id: str,
        *,
        work_unit_id: str | None,
        kind: str,
        dependencies: list[str],
        input_refs: list[ArtifactRef],
        expected_outputs: list[OutputSpec],
        required_capabilities: list[str],
        assigned_adapter: str | None,
        actor: ActorRef,
        assigned_agent_id: str | None = None,
    ) -> WorkUnit:
        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)
            if mission.status != MissionStatus.RUNNING:
                raise ValueError("work units require a RUNNING mission")

            contract = await repository.get_contract(
                mission.contract_id,
                mission.contract_version,
            )
            if contract is None:
                raise ValueError("mission contract not found")
            allowed_capabilities = {
                grant.capability for grant in contract.allowed_capabilities
            }
            unsupported = sorted(set(required_capabilities) - allowed_capabilities)
            if unsupported:
                raise ValueError(
                    "work unit requires capabilities outside the mission contract: "
                    + ", ".join(unsupported)
                )
            if assigned_agent_id is not None and assigned_adapter is None:
                raise ValueError("an assigned Agent requires an execution adapter")
            await self._validate_artifact_refs(repository, mission_id, input_refs)

            identifier = work_unit_id or new_identifier("wu")
            for dependency_id in dependencies:
                dependency = await repository.get_work_unit(dependency_id)
                if dependency is None or dependency.mission_id != mission_id:
                    raise ValueError(
                        f"work unit dependency is not part of the mission: {dependency_id}"
                    )

            work_unit = WorkUnit(
                id=identifier,
                mission_id=mission_id,
                assigned_agent_id=assigned_agent_id,
                kind=kind,
                dependencies=dependencies,
                input_refs=input_refs,
                expected_outputs=expected_outputs,
                required_capabilities=required_capabilities,
                assigned_adapter=assigned_adapter,
                status="PENDING",
                attempt=0,
            )
            occurred_at = datetime.now(timezone.utc)
            event_payload = {
                "kind": work_unit.kind,
                "missionId": mission_id,
                "status": work_unit.status.value,
            }
            if work_unit.assigned_agent_id is not None:
                event_payload["assignedAgentId"] = work_unit.assigned_agent_id
            if work_unit.assigned_adapter is not None:
                event_payload["assignedAdapter"] = work_unit.assigned_adapter
            event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="work_unit",
                aggregate_id=work_unit.id,
                sequence=1,
                event_type="work_unit.lifecycle.created",
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission_id,
                payload=event_payload,
                schema_version=1,
            )
            await repository.add_work_unit(work_unit)
            await repository.append_event(event)
        return work_unit

    async def delegate_work_unit(
        self,
        mission_id: str,
        parent_work_unit_id: str,
        *,
        work_unit_id: str,
        kind: str,
        input_refs: list[ArtifactRef],
        expected_outputs: list[OutputSpec],
        required_capabilities: list[str],
        agent_id: str,
        lease_id: str,
        runner_id: str,
        actor: ActorRef,
    ) -> WorkUnit:
        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)
            if mission.status != MissionStatus.RUNNING:
                raise WorkUnitNotReadyError("delegation requires a RUNNING mission")

            parent = await repository.get_work_unit_for_update(parent_work_unit_id)
            if parent is None or parent.mission_id != mission_id:
                raise WorkUnitNotFoundError(parent_work_unit_id)
            if parent.status != WorkUnitStatus.RUNNING:
                raise WorkUnitNotReadyError(
                    "only a RUNNING work unit can create a delegation"
                )
            if parent.lease is None:
                raise LeaseOwnershipError("parent work unit has no active lease")
            if parent.lease.id != lease_id:
                raise LeaseOwnershipError(
                    "lease id does not match the parent work unit"
                )
            if parent.lease.runner_id != runner_id:
                raise LeaseOwnershipError("parent lease belongs to another runner")

            occurred_at = datetime.now(timezone.utc)
            if parent.lease.expires_at <= occurred_at:
                raise LeaseExpiredError("parent work unit lease has expired")

            contract = await repository.get_contract(
                mission.contract_id, mission.contract_version
            )
            if contract is None:
                raise ValueError("mission contract not found")
            allowed_capabilities = {
                grant.capability for grant in contract.allowed_capabilities
            }
            unsupported = sorted(set(required_capabilities) - allowed_capabilities)
            if unsupported:
                raise ValueError(
                    "delegated work unit requires capabilities outside the mission "
                    "contract: " + ", ".join(unsupported)
                )
            await self._validate_artifact_refs(repository, mission_id, input_refs)

            resolver = self._agent_binding_resolver
            if resolver is None:
                raise AgentBindingUnavailableError(
                    "tenant-scoped Agent binding resolver is not configured"
                )
            binding = await resolver.resolve(
                scope_id=mission.workspace_id,
                agent_id=agent_id,
            )
            if binding is None:
                raise AgentBindingNotFoundError(
                    f"Agent is not available in the mission scope: {agent_id}"
                )
            missing_binding_capabilities = sorted(
                set(required_capabilities) - set(binding.capabilities)
            )
            if missing_binding_capabilities:
                raise ValueError(
                    "Agent binding does not grant required capabilities: "
                    + ", ".join(missing_binding_capabilities)
                )

            candidate = WorkUnit(
                id=work_unit_id,
                mission_id=mission_id,
                parent_work_unit_id=parent_work_unit_id,
                assigned_agent_id=binding.agent_id,
                kind=kind,
                dependencies=[],
                input_refs=input_refs,
                expected_outputs=expected_outputs,
                required_capabilities=required_capabilities,
                assigned_adapter=binding.adapter_type,
                status=WorkUnitStatus.PENDING,
                attempt=0,
            )
            existing = await repository.get_work_unit_for_update(work_unit_id)
            if existing is not None:
                immutable_fields_match = all(
                    getattr(existing, field_name) == getattr(candidate, field_name)
                    for field_name in (
                        "mission_id",
                        "parent_work_unit_id",
                        "assigned_agent_id",
                        "kind",
                        "dependencies",
                        "input_refs",
                        "expected_outputs",
                        "required_capabilities",
                        "assigned_adapter",
                    )
                )
                if immutable_fields_match:
                    return existing
                raise ValueError(
                    "delegation id already exists with different immutable fields"
                )

            parent_sequence = (
                await repository.get_last_event_sequence(
                    parent.id,
                    aggregate_type="work_unit",
                )
                + 1
            )
            delegation_event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="work_unit",
                aggregate_id=parent.id,
                sequence=parent_sequence,
                event_type="work_unit.delegation.requested",
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission_id,
                payload={
                    "childWorkUnitId": candidate.id,
                    "parentAttempt": parent.attempt,
                    "assignedAgentId": binding.agent_id,
                    "assignedAdapter": binding.adapter_type,
                    "inputRefs": [item.to_public_dict() for item in input_refs],
                },
                schema_version=1,
            )
            created_event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="work_unit",
                aggregate_id=candidate.id,
                sequence=1,
                event_type="work_unit.lifecycle.created",
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission_id,
                causation_id=delegation_event.event_id,
                payload={
                    "kind": candidate.kind,
                    "missionId": mission_id,
                    "parentWorkUnitId": parent.id,
                    "status": candidate.status.value,
                },
                schema_version=1,
            )
            await repository.add_work_unit(candidate)
            await repository.append_event(delegation_event)
            await repository.append_event(created_event)
        return candidate

    async def fail_pending_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        actor: ActorRef,
        reason: str,
    ) -> WorkUnit:
        return await self._transition_pending_work_unit(
            mission_id,
            work_unit_id,
            target=WorkUnitStatus.FAILED,
            event_type="work_unit.lifecycle.failed",
            actor=actor,
            reason=reason,
        )

    async def cancel_pending_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        actor: ActorRef,
    ) -> WorkUnit:
        return await self._transition_pending_work_unit(
            mission_id,
            work_unit_id,
            target=WorkUnitStatus.CANCELLED,
            event_type="work_unit.lifecycle.cancelled",
            actor=actor,
        )

    async def _transition_pending_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        target: WorkUnitStatus,
        event_type: str,
        actor: ActorRef,
        reason: str | None = None,
    ) -> WorkUnit:
        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)
            work_unit = await repository.get_work_unit_for_update(work_unit_id)
            if work_unit is None or work_unit.mission_id != mission_id:
                raise WorkUnitNotFoundError(work_unit_id)
            if work_unit.status != WorkUnitStatus.PENDING:
                raise WorkUnitNotReadyError(
                    "only PENDING work units can use an adapter pre-execution transition"
                )

            occurred_at = datetime.now(timezone.utc)
            updated = transition_work_unit(
                work_unit,
                target,
                occurred_at=occurred_at,
            )
            sequence = (
                await repository.get_last_event_sequence(
                    work_unit.id,
                    aggregate_type="work_unit",
                )
                + 1
            )
            payload = {
                "previousStatus": work_unit.status.value,
                "status": updated.status.value,
            }
            if reason is not None:
                payload["reason"] = reason
            event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="work_unit",
                aggregate_id=work_unit.id,
                sequence=sequence,
                event_type=event_type,
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission_id,
                payload=payload,
                schema_version=1,
            )
            await repository.update_work_unit(updated)
            await repository.append_event(event)
            if target == WorkUnitStatus.FAILED:
                await self._fail_mission_for_work_unit(
                    repository,
                    mission,
                    work_unit_id=work_unit.id,
                    actor=actor,
                    reason=reason or "work unit failed before execution",
                    occurred_at=occurred_at,
                    causation_id=event.event_id,
                )
        return updated

    async def lease_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        actor: ActorRef,
        lease_seconds: int,
    ) -> WorkUnit:
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)
            if mission.status != MissionStatus.RUNNING:
                raise WorkUnitNotReadyError("work units require a RUNNING mission")

            work_unit = await repository.get_work_unit_for_update(work_unit_id)
            if work_unit is None or work_unit.mission_id != mission_id:
                raise WorkUnitNotFoundError(work_unit_id)

            occurred_at = datetime.now(timezone.utc)
            for dependency_id in work_unit.dependencies:
                dependency = await repository.get_work_unit(dependency_id)
                if dependency is None:
                    raise WorkUnitNotReadyError(
                        f"work unit dependency is missing: {dependency_id}"
                    )
                if dependency.status != WorkUnitStatus.SUCCEEDED:
                    raise WorkUnitNotReadyError(
                        f"work unit dependency is not complete: {dependency_id}"
                    )

            lease = Lease(
                id=new_identifier("lease"),
                runner_id=runner_id,
                expires_at=occurred_at + timedelta(seconds=lease_seconds),
            )
            updated = transition_work_unit(
                work_unit,
                WorkUnitStatus.LEASED,
                occurred_at=occurred_at,
                lease=lease,
            )
            sequence = (
                await repository.get_last_event_sequence(
                    work_unit.id,
                    aggregate_type="work_unit",
                )
                + 1
            )
            event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="work_unit",
                aggregate_id=work_unit.id,
                sequence=sequence,
                event_type="work_unit.lifecycle.leased",
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission_id,
                payload={
                    "previousStatus": work_unit.status.value,
                    "status": updated.status.value,
                    "leaseId": lease.id,
                    "runnerId": lease.runner_id,
                    "attempt": updated.attempt,
                    "expiresAt": lease.expires_at.isoformat(),
                },
                schema_version=1,
            )
            await repository.update_work_unit(updated)
            await repository.append_event(event)
        return updated

    async def claim_bound_work_unit(
        self,
        mission_id: str,
        *,
        agent_id: str,
        adapter_type: str,
        runner_id: str,
        actor: ActorRef,
        lease_seconds: int,
        admission_policy: WorkspaceClaimAdmissionPolicy,
    ) -> WorkUnitClaimOutcome:
        """Atomically claim the next ready unit for one explicit binding."""
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        async with self._repository.transaction() as repository:
            if not await self._runner_claim_is_admitted(
                repository,
                admission_policy,
            ):
                return WorkUnitClaimOutcome(
                    status=WorkspaceClaimStatus.CAPACITY_SATURATED,
                    work_unit=None,
                )
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)
            if mission.status != MissionStatus.RUNNING:
                raise WorkUnitNotReadyError("work units require a RUNNING mission")

            allowed_root_kind = {
                MissionSourceType.A2A_INBOUND: "a2a.inbound",
                MissionSourceType.A2A: "a2a.delegate",
                MissionSourceType.MISSION_FORK: "mission.fork",
            }.get(mission.source.type)
            work_unit = await repository.get_bound_work_unit_for_claim(
                mission_id,
                agent_id=agent_id,
                adapter_type=adapter_type,
                allowed_root_kind=allowed_root_kind,
            )
            if work_unit is None:
                return WorkUnitClaimOutcome(
                    status=WorkspaceClaimStatus.IDLE,
                    work_unit=None,
                )

            return WorkUnitClaimOutcome(
                status=WorkspaceClaimStatus.CLAIMED,
                work_unit=await self._lease_bound_claim_candidate(
                    repository,
                    mission,
                    work_unit,
                    agent_id=agent_id,
                    adapter_type=adapter_type,
                    runner_id=runner_id,
                    actor=actor,
                    lease_seconds=lease_seconds,
                ),
            )

    async def claim_workspace_bound_work_unit(
        self,
        workspace_id: str,
        *,
        agent_id: str,
        adapter_type: str,
        supported_work_unit_kinds: tuple[str, ...],
        runner_id: str,
        actor: ActorRef,
        lease_seconds: int,
        admission_policy: WorkspaceClaimAdmissionPolicy,
    ) -> WorkUnitClaimOutcome:
        """Atomically discover and claim one bound unit in a workspace."""

        if not workspace_id.strip():
            raise ValueError("workspace_id must be non-empty")
        if not supported_work_unit_kinds:
            raise ValueError("supported_work_unit_kinds must be non-empty")
        if len(supported_work_unit_kinds) > 32:
            raise ValueError("supported_work_unit_kinds exceeds limit")
        if len(supported_work_unit_kinds) != len(set(supported_work_unit_kinds)):
            raise ValueError("supported_work_unit_kinds must be unique")
        if any(
            not isinstance(kind, str)
            or not kind.strip()
            or kind != kind.strip()
            or len(kind) > 255
            for kind in supported_work_unit_kinds
        ):
            raise ValueError("supported_work_unit_kinds is invalid")
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        async with self._repository.transaction() as repository:
            if not await self._runner_claim_is_admitted(
                repository,
                admission_policy,
            ):
                return WorkUnitClaimOutcome(
                    status=WorkspaceClaimStatus.CAPACITY_SATURATED,
                    work_unit=None,
                )
            selection = await repository.get_workspace_bound_work_unit_for_claim(
                workspace_id,
                agent_id=agent_id,
                adapter_type=adapter_type,
                supported_work_unit_kinds=supported_work_unit_kinds,
            )
            if selection is None:
                return WorkUnitClaimOutcome(
                    status=WorkspaceClaimStatus.IDLE,
                    work_unit=None,
                )
            mission, work_unit = selection
            if mission.workspace_id != workspace_id:
                raise WorkUnitNotReadyError(
                    "claim repository returned a Mission from another workspace"
                )
            return WorkUnitClaimOutcome(
                status=WorkspaceClaimStatus.CLAIMED,
                work_unit=await self._lease_bound_claim_candidate(
                    repository,
                    mission,
                    work_unit,
                    agent_id=agent_id,
                    adapter_type=adapter_type,
                    runner_id=runner_id,
                    actor=actor,
                    lease_seconds=lease_seconds,
                ),
            )

    @staticmethod
    async def _runner_claim_is_admitted(
        repository: MissionRepository,
        policy: WorkspaceClaimAdmissionPolicy,
    ) -> bool:
        if policy.max_concurrent == 0:
            return True
        try:
            await repository.lock_tenant_claim_admission(policy.tenant_id)
            active_count = await repository.count_tenant_active_runner_work_units(
                policy.tenant_id
            )
        except Exception as exc:
            raise WorkspaceClaimAdmissionUnavailableError(
                "Workspace claim admission state is unavailable"
            ) from exc
        return active_count < policy.max_concurrent

    async def _lease_bound_claim_candidate(
        self,
        repository: MissionRepository,
        mission: Mission,
        work_unit: WorkUnit,
        *,
        agent_id: str,
        adapter_type: str,
        runner_id: str,
        actor: ActorRef,
        lease_seconds: int,
    ) -> WorkUnit:
        if mission.status != MissionStatus.RUNNING:
            raise WorkUnitNotReadyError("work units require a RUNNING mission")
        if work_unit.mission_id != mission.id:
            raise WorkUnitNotReadyError(
                "claim repository returned a WorkUnit from another Mission"
            )

        if (
            work_unit.assigned_agent_id != agent_id
            or work_unit.assigned_adapter != adapter_type
        ):
            raise WorkUnitNotReadyError(
                "claim repository returned a WorkUnit for another binding"
            )
        if work_unit.parent_work_unit_id is not None:
            claim_mode = "delegated"
        elif (
            mission.source.type == MissionSourceType.A2A_INBOUND
            and work_unit.kind == "a2a.inbound"
            and work_unit.assigned_adapter != _A2A_OUTBOUND_ADAPTER
        ):
            claim_mode = "a2a.inbound"
        elif (
            mission.source.type == MissionSourceType.A2A
            and work_unit.kind == "a2a.delegate"
            and work_unit.assigned_adapter == _A2A_OUTBOUND_ADAPTER
        ):
            claim_mode = "a2a.outbound"
        elif (
            mission.source.type == MissionSourceType.MISSION_FORK
            and work_unit.kind == "mission.fork"
            and work_unit.assigned_adapter != _A2A_OUTBOUND_ADAPTER
        ):
            claim_mode = "mission.fork"
        elif (
            mission.source.type == MissionSourceType.MANUAL
            and work_unit.parent_work_unit_id is None
            and work_unit.kind == _DESKTOP_TASK_WORK_UNIT_KIND
            and work_unit.assigned_adapter != _A2A_OUTBOUND_ADAPTER
        ):
            claim_mode = "desktop.task"
        else:
            raise WorkUnitNotReadyError(
                "claim repository returned an ineligible root WorkUnit"
            )

        # Keep the application-level dependency check as a defense-in-depth
        # guard for alternate repository implementations and test doubles.
        for dependency_id in work_unit.dependencies:
            dependency = await repository.get_work_unit(dependency_id)
            if dependency is None or dependency.mission_id != mission.id:
                raise WorkUnitNotReadyError(
                    f"work unit dependency is missing: {dependency_id}"
                )
            if dependency.status != WorkUnitStatus.SUCCEEDED:
                raise WorkUnitNotReadyError(
                    f"work unit dependency is not complete: {dependency_id}"
                )

        occurred_at = datetime.now(timezone.utc)
        lease = Lease(
            id=new_identifier("lease"),
            runner_id=runner_id,
            expires_at=occurred_at + timedelta(seconds=lease_seconds),
        )
        updated = transition_work_unit(
            work_unit,
            WorkUnitStatus.LEASED,
            occurred_at=occurred_at,
            lease=lease,
        )
        sequence = (
            await repository.get_last_event_sequence(
                work_unit.id,
                aggregate_type="work_unit",
            )
            + 1
        )
        event = EventEnvelope(
            event_id=new_identifier("evt"),
            aggregate_type="work_unit",
            aggregate_id=work_unit.id,
            sequence=sequence,
            event_type="work_unit.lifecycle.leased",
            actor=actor,
            occurred_at=occurred_at,
            correlation_id=mission.id,
            payload={
                "previousStatus": work_unit.status.value,
                "status": updated.status.value,
                "leaseId": lease.id,
                "runnerId": lease.runner_id,
                "attempt": updated.attempt,
                "expiresAt": lease.expires_at.isoformat(),
                "claimMode": claim_mode,
                "assignedAgentId": updated.assigned_agent_id,
                "assignedAdapter": updated.assigned_adapter,
            },
            schema_version=1,
        )
        await repository.update_work_unit(updated)
        await repository.append_event(event)
        return updated

    async def get_claimed_execution_context(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        lease_id: str,
        runner_id: str,
    ) -> ClaimedExecutionContext:
        """Read a controlled root snapshot behind the active lease fence."""
        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)
            if mission.status != MissionStatus.RUNNING:
                raise WorkUnitNotReadyError("work units require a RUNNING mission")

            work_unit = await repository.get_work_unit_for_update(work_unit_id)
            if work_unit is None or work_unit.mission_id != mission_id:
                raise WorkUnitNotFoundError(work_unit_id)
            is_inbound_root = (
                mission.source.type == MissionSourceType.A2A_INBOUND
                and work_unit.parent_work_unit_id is None
                and work_unit.kind == "a2a.inbound"
                and work_unit.assigned_adapter != _A2A_OUTBOUND_ADAPTER
                and "a2a.receive" in work_unit.required_capabilities
            )
            is_outbound_root = (
                mission.source.type == MissionSourceType.A2A
                and work_unit.parent_work_unit_id is None
                and work_unit.kind == "a2a.delegate"
                and work_unit.assigned_agent_id is not None
                and work_unit.assigned_adapter == _A2A_OUTBOUND_ADAPTER
                and "a2a.send" in work_unit.required_capabilities
            )
            is_mission_fork_root = (
                mission.source.type == MissionSourceType.MISSION_FORK
                and work_unit.parent_work_unit_id is None
                and work_unit.kind == "mission.fork"
                and work_unit.assigned_agent_id is not None
                and work_unit.assigned_adapter is not None
                and work_unit.assigned_adapter != _A2A_OUTBOUND_ADAPTER
                and bool(work_unit.input_refs)
            )
            is_desktop_task_root = (
                mission.source.type == MissionSourceType.MANUAL
                and work_unit.parent_work_unit_id is None
                and work_unit.kind == _DESKTOP_TASK_WORK_UNIT_KIND
                and work_unit.assigned_agent_id is not None
                and work_unit.assigned_adapter is not None
                and work_unit.assigned_adapter != _A2A_OUTBOUND_ADAPTER
            )
            if not (
                is_inbound_root
                or is_outbound_root
                or is_mission_fork_root
                or is_desktop_task_root
            ):
                raise WorkUnitNotReadyError(
                    "execution context is only available for controlled roots"
                )
            if work_unit.status not in {
                WorkUnitStatus.LEASED,
                WorkUnitStatus.RUNNING,
            }:
                raise WorkUnitNotReadyError(
                    "execution context requires a LEASED or RUNNING work unit"
                )
            if work_unit.lease is None:
                raise LeaseOwnershipError("work unit has no active lease")
            if work_unit.lease.id != lease_id or work_unit.lease.runner_id != runner_id:
                raise LeaseOwnershipError("work unit lease ownership mismatch")
            if work_unit.lease.expires_at <= datetime.now(timezone.utc):
                raise LeaseExpiredError("work unit lease has expired")

            contract = await repository.get_contract(
                mission.contract_id, mission.contract_version
            )
            if contract is None:
                raise WorkUnitNotReadyError("mission contract not found")
            return ClaimedExecutionContext(
                mission=mission,
                contract=contract,
                work_unit=work_unit,
            )

    async def heartbeat_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        lease_id: str,
        runner_id: str,
        actor: ActorRef,
        lease_seconds: int,
    ) -> WorkUnit:
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)
            if mission.status != MissionStatus.RUNNING:
                raise WorkUnitNotReadyError("work units require a RUNNING mission")

            work_unit = await repository.get_work_unit_for_update(work_unit_id)
            if work_unit is None or work_unit.mission_id != mission_id:
                raise WorkUnitNotFoundError(work_unit_id)
            if work_unit.status not in {
                WorkUnitStatus.LEASED,
                WorkUnitStatus.RUNNING,
            }:
                raise WorkUnitNotReadyError(
                    "only LEASED or RUNNING work units can send a heartbeat"
                )
            if work_unit.lease is None:
                raise LeaseOwnershipError("work unit has no active lease")
            if work_unit.lease.id != lease_id:
                raise LeaseOwnershipError("lease id does not match the work unit")
            if work_unit.lease.runner_id != runner_id:
                raise LeaseOwnershipError("lease belongs to another runner")

            occurred_at = datetime.now(timezone.utc)
            if work_unit.lease.expires_at <= occurred_at:
                raise LeaseExpiredError("work unit lease has expired")
            renewed_lease = Lease(
                id=work_unit.lease.id,
                runner_id=work_unit.lease.runner_id,
                expires_at=occurred_at + timedelta(seconds=lease_seconds),
            )
            updated = work_unit.model_copy(update={"lease": renewed_lease})
            sequence = (
                await repository.get_last_event_sequence(
                    work_unit.id,
                    aggregate_type="work_unit",
                )
                + 1
            )
            event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="work_unit",
                aggregate_id=work_unit.id,
                sequence=sequence,
                event_type="work_unit.lifecycle.heartbeat",
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission_id,
                payload={
                    "status": updated.status.value,
                    "leaseId": renewed_lease.id,
                    "runnerId": renewed_lease.runner_id,
                    "previousExpiresAt": work_unit.lease.expires_at.isoformat(),
                    "expiresAt": renewed_lease.expires_at.isoformat(),
                },
                schema_version=1,
            )
            await repository.update_work_unit(updated)
            await repository.append_event(event)
        return updated

    async def record_execution_checkpoint(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        checkpoint_id: str,
        lease_id: str,
        runner_id: str,
        sequence: int,
        phase: ExecutionCheckpointPhase,
        iteration: int,
        tool_calls: int,
        prompt_tokens: int,
        completion_tokens: int,
        model_cost: float,
        terminal: bool,
        failure_reason: str | None,
        actor: ActorRef,
        tool_name: str | None = None,
        tool_success: bool | None = None,
    ) -> ExecutionCheckpoint:
        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)
            if mission.status != MissionStatus.RUNNING:
                raise WorkUnitNotReadyError(
                    "execution checkpoints require a RUNNING mission"
                )
            work_unit = await repository.get_work_unit_for_update(work_unit_id)
            if work_unit is None or work_unit.mission_id != mission_id:
                raise WorkUnitNotFoundError(work_unit_id)
            if work_unit.status != WorkUnitStatus.RUNNING:
                raise WorkUnitNotReadyError(
                    "execution checkpoints require a RUNNING work unit"
                )
            if work_unit.lease is None:
                raise LeaseOwnershipError("work unit has no active lease")
            if (
                work_unit.lease.id != lease_id
                or work_unit.lease.runner_id != runner_id
            ):
                raise LeaseOwnershipError("work unit lease ownership mismatch")
            occurred_at = datetime.now(timezone.utc)
            if work_unit.lease.expires_at <= occurred_at:
                raise LeaseExpiredError("work unit lease has expired")

            requested = {
                "mission_id": mission_id,
                "work_unit_id": work_unit_id,
                "attempt": work_unit.attempt,
                "sequence": sequence,
                "phase": phase,
                "iteration": iteration,
                "tool_calls": tool_calls,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "model_cost": model_cost,
                "terminal": terminal,
                "failure_reason": failure_reason,
            }
            existing = await repository.get_execution_checkpoint(checkpoint_id)
            if existing is not None:
                if any(
                    getattr(existing, field_name) != value
                    for field_name, value in requested.items()
                ):
                    raise ValueError(
                        "execution checkpoint id already exists with different content"
                    )
                return existing

            latest = await repository.get_latest_execution_checkpoint(
                work_unit_id,
                work_unit.attempt,
            )
            if latest is not None and latest.terminal:
                raise ValueError("terminal execution checkpoint already exists")
            expected_sequence = 1 if latest is None else latest.sequence + 1
            if sequence != expected_sequence:
                raise ValueError(
                    "execution checkpoint sequence must be contiguous "
                    f"(expected={expected_sequence}, actual={sequence})"
                )
            if sequence == 1 and phase != ExecutionCheckpointPhase.EXECUTION_STARTED:
                raise ValueError(
                    "first execution checkpoint must mark execution start"
                )

            digest_material = {
                "attempt": work_unit.attempt,
                "completionTokens": completion_tokens,
                "failureReason": failure_reason,
                "iteration": iteration,
                "missionId": mission_id,
                "modelCost": model_cost,
                "phase": phase.value,
                "promptTokens": prompt_tokens,
                "sequence": sequence,
                "terminal": terminal,
                "toolCalls": tool_calls,
                "workUnitId": work_unit_id,
            }
            state_digest = "sha256:" + hashlib.sha256(
                json.dumps(
                    digest_material,
                    allow_nan=False,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            checkpoint = ExecutionCheckpoint(
                id=checkpoint_id,
                **requested,
                state_digest=state_digest,
                created_by=actor,
                created_at=occurred_at,
            )
            await repository.add_execution_checkpoint(checkpoint)
            event_sequence = (
                await repository.get_last_event_sequence(
                    work_unit_id,
                    aggregate_type="work_unit",
                )
                + 1
            )
            await repository.append_event(
                EventEnvelope(
                    event_id=new_identifier("evt"),
                    aggregate_type="work_unit",
                    aggregate_id=work_unit_id,
                    sequence=event_sequence,
                    event_type="work_unit.checkpoint.recorded",
                    actor=actor,
                    occurred_at=occurred_at,
                    correlation_id=mission_id,
                    payload=_checkpoint_event_payload(
                        checkpoint,
                        tool_name=tool_name,
                        tool_success=tool_success,
                    ),
                    schema_version=1,
                )
            )
        return checkpoint

    async def start_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        lease_id: str,
        runner_id: str,
        actor: ActorRef,
    ) -> WorkUnit:
        return await self._transition_execution_work_unit(
            mission_id,
            work_unit_id,
            target=WorkUnitStatus.RUNNING,
            event_type="work_unit.lifecycle.started",
            lease_id=lease_id,
            runner_id=runner_id,
            actor=actor,
        )

    async def complete_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        lease_id: str,
        runner_id: str,
        actor: ActorRef,
        artifact_refs: list[ArtifactRef],
    ) -> WorkUnit:
        if not artifact_refs:
            raise ValueError("work unit completion requires at least one artifact")
        return await self._transition_execution_work_unit(
            mission_id,
            work_unit_id,
            target=WorkUnitStatus.VERIFYING,
            event_type="work_unit.lifecycle.completed",
            lease_id=lease_id,
            runner_id=runner_id,
            actor=actor,
            artifact_refs=artifact_refs,
        )

    async def register_artifact(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        artifact_id: str,
        lease_id: str,
        runner_id: str,
        kind: ArtifactKind,
        digest: str,
        content_address: str,
        media_type: str,
        size_bytes: int,
        source_repository: str | None,
        base_commit: str | None,
        retention: ArtifactRetention,
        sensitivity: ArtifactSensitivity,
        actor: ActorRef,
    ) -> Artifact:
        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)
            if mission.status != MissionStatus.RUNNING:
                raise WorkUnitNotReadyError("artifacts require a RUNNING mission")

            work_unit = await repository.get_work_unit_for_update(work_unit_id)
            if work_unit is None or work_unit.mission_id != mission_id:
                raise WorkUnitNotFoundError(work_unit_id)
            if work_unit.status != WorkUnitStatus.RUNNING:
                raise WorkUnitNotReadyError(
                    "artifacts can only be registered for a RUNNING work unit"
                )
            if work_unit.lease is None:
                raise LeaseOwnershipError("work unit has no active lease")
            if work_unit.lease.id != lease_id:
                raise LeaseOwnershipError("lease id does not match the work unit")
            if work_unit.lease.runner_id != runner_id:
                raise LeaseOwnershipError("lease belongs to another runner")

            occurred_at = datetime.now(timezone.utc)
            if work_unit.lease.expires_at <= occurred_at:
                raise LeaseExpiredError("work unit lease has expired")
            digest_value = digest.removeprefix("sha256:")
            if digest not in content_address and digest_value not in content_address:
                raise ValueError("artifact content address must include its digest")

            existing = await repository.get_artifact(artifact_id)
            expected_values = {
                "mission_id": mission_id,
                "work_unit_id": work_unit_id,
                "attempt": work_unit.attempt,
                "kind": kind,
                "digest": digest,
                "content_address": content_address,
                "media_type": media_type,
                "size_bytes": size_bytes,
                "source_repository": source_repository,
                "base_commit": base_commit,
                "retention": retention,
                "sensitivity": sensitivity,
                "created_by": actor,
            }
            if existing is not None:
                if all(
                    getattr(existing, field_name) == value
                    for field_name, value in expected_values.items()
                ):
                    return existing
                raise ValueError("artifact id already exists with different metadata")

            artifact = Artifact(
                id=artifact_id,
                created_at=occurred_at,
                **expected_values,
            )
            event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="artifact",
                aggregate_id=artifact.id,
                sequence=1,
                event_type="artifact.lifecycle.registered",
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission_id,
                payload=artifact.to_public_dict(),
                schema_version=1,
            )
            await repository.add_artifact(artifact)
            await repository.append_event(event)
        return artifact

    async def discover_workspace_verification_work(
        self,
        workspace_id: str,
    ) -> VerificationDiscoveryOutcome:
        """Return one authorized workspace's minimal, consistent verifier input."""

        normalized_workspace_id = workspace_id.strip()
        if not normalized_workspace_id:
            raise ValueError("workspace_id must be non-empty")
        async with self._repository.transaction() as repository:
            selection = await repository.get_workspace_verification_candidate(
                normalized_workspace_id
            )
            if selection is None:
                return VerificationDiscoveryOutcome(context=None)
            mission, work_unit = selection
            if mission.workspace_id != normalized_workspace_id:
                raise WorkUnitNotReadyError(
                    "verification repository returned another workspace"
                )
            if mission.status not in {MissionStatus.RUNNING, MissionStatus.VERIFYING}:
                raise WorkUnitNotReadyError(
                    "verification requires a RUNNING or VERIFYING mission"
                )
            if (
                work_unit.mission_id != mission.id
                or work_unit.status != WorkUnitStatus.VERIFYING
            ):
                raise WorkUnitNotReadyError(
                    "verification repository returned an ineligible WorkUnit"
                )
            if work_unit.attempt < 1:
                raise WorkUnitNotReadyError(
                    "verification requires a positive WorkUnit attempt"
                )
            contract = await repository.get_contract(
                mission.contract_id, mission.contract_version
            )
            if contract is None:
                raise WorkUnitNotReadyError("mission contract not found")
            artifacts = await repository.list_work_unit_artifacts(
                mission.id,
                work_unit.id,
                work_unit.attempt,
                limit=_MAX_VERIFICATION_ARTIFACTS + 1,
            )
            if not artifacts:
                raise WorkUnitNotReadyError(
                    "verification requires current-attempt Artifacts"
                )
            if len(artifacts) > _MAX_VERIFICATION_ARTIFACTS:
                raise WorkUnitNotReadyError("verification Artifact count exceeds 200")
            if len({artifact.id for artifact in artifacts}) != len(artifacts):
                raise WorkUnitNotReadyError(
                    "verification repository returned duplicate Artifacts"
                )
            if any(
                artifact.mission_id != mission.id
                or artifact.work_unit_id != work_unit.id
                or artifact.attempt != work_unit.attempt
                for artifact in artifacts
            ):
                raise WorkUnitNotReadyError(
                    "verification repository returned unrelated Artifacts"
                )
            evaluation_policy = self._verification_policy_resolver.resolve(
                contract,
                work_unit,
                tuple(artifacts),
            )
            context = VerificationContext(
                mission=mission,
                contract=contract,
                work_unit=work_unit,
                artifacts=tuple(artifacts),
                evaluation_policy=evaluation_policy,
            )
            if evaluation_policy.plan is None:
                await self._request_verification_decision(
                    repository,
                    mission=mission,
                    contract=contract,
                    work_unit=work_unit,
                    artifacts=tuple(artifacts),
                    evaluation_policy=evaluation_policy,
                )
            return VerificationDiscoveryOutcome(context=context)

    async def _request_verification_decision(
        self,
        repository: MissionRepository,
        *,
        mission: Mission,
        contract: MissionContract,
        work_unit: WorkUnit,
        artifacts: tuple[Artifact, ...],
        evaluation_policy: EvaluationPolicyDecision,
    ) -> Decision:
        if evaluation_policy.plan is not None or evaluation_policy.reason is None:
            raise ValueError("verification Decision requires an inconclusive policy")
        occurred_at = datetime.now(timezone.utc)
        can_retry = work_unit.attempt < contract.budgets.retries + 1
        options = (
            (DecisionResolution.RETRY_WORK_UNIT, DecisionResolution.FAIL_MISSION)
            if can_retry
            else (DecisionResolution.FAIL_MISSION,)
        )
        recommended_option = (
            DecisionResolution.RETRY_WORK_UNIT
            if can_retry
            and evaluation_policy.reason.value == "artifact_requirements_not_met"
            else DecisionResolution.FAIL_MISSION
        )
        material = {
            "schemaVersion": 1,
            "missionId": mission.id,
            "contractId": contract.id,
            "contractVersion": contract.version,
            "workUnitId": work_unit.id,
            "workUnitKind": work_unit.kind,
            "attempt": work_unit.attempt,
            "reasonCode": evaluation_policy.reason.value,
            "criterionIds": list(evaluation_policy.criterion_ids),
            "artifacts": [
                {
                    "id": artifact.id,
                    "digest": artifact.digest.lower(),
                    "sizeBytes": artifact.size_bytes,
                }
                for artifact in sorted(artifacts, key=lambda item: item.id)
            ],
        }
        encoded_material = json.dumps(
            material,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        decision = Decision(
            id=new_identifier("dec"),
            mission_id=mission.id,
            work_unit_id=work_unit.id,
            attempt=work_unit.attempt,
            context_digest="sha256:" + hashlib.sha256(encoded_material).hexdigest(),
            reason_code=evaluation_policy.reason.value,
            criterion_ids=evaluation_policy.criterion_ids,
            options=options,
            recommended_option=recommended_option,
            risk_summary=(
                "Independent verification cannot prove the affected Contract "
                "criteria under the current policy and Artifact set."
            ),
            status=DecisionStatus.PENDING,
            version=1,
            requested_by=ActorRef(type=ActorType.SERVICE, id="mission-control"),
            requested_at=occurred_at,
            expires_at=occurred_at
            + timedelta(seconds=contract.governance.decision_timeout_seconds),
        )
        decision_event = EventEnvelope(
            event_id=new_identifier("evt"),
            aggregate_type="decision",
            aggregate_id=decision.id,
            sequence=1,
            event_type="decision.lifecycle.requested",
            actor=decision.requested_by,
            occurred_at=occurred_at,
            correlation_id=mission.id,
            payload=decision.to_public_dict(),
            schema_version=1,
        )
        updated_mission = transition_mission(
            mission,
            MissionStatus.WAITING_DECISION,
            occurred_at=occurred_at,
        )
        mission_sequence = await repository.get_last_event_sequence(mission.id) + 1
        mission_event = EventEnvelope(
            event_id=new_identifier("evt"),
            aggregate_type="mission",
            aggregate_id=mission.id,
            sequence=mission_sequence,
            event_type="mission.lifecycle.waiting_decision",
            actor=decision.requested_by,
            occurred_at=occurred_at,
            correlation_id=mission.id,
            causation_id=decision_event.event_id,
            payload={
                "previousStatus": mission.status.value,
                "status": updated_mission.status.value,
                "decisionId": decision.id,
                "workUnitId": work_unit.id,
            },
            schema_version=1,
        )
        await repository.add_decision(decision)
        await repository.append_event(decision_event)
        await repository.update_mission(updated_mission)
        await repository.append_event(mission_event)
        return decision

    async def resolve_decision(
        self,
        mission_id: str,
        decision_id: str,
        *,
        expected_version: int,
        resolution: DecisionResolution,
        rationale: str,
        actor: ActorRef,
    ) -> tuple[Decision, WorkUnit, Mission]:
        if actor.type != ActorType.HUMAN:
            raise ValueError("only human actors can resolve Decisions")
        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)
            decision = await repository.get_decision_for_update(decision_id)
            if decision is None or decision.mission_id != mission_id:
                raise DecisionNotFoundError(decision_id)
            if decision.status != DecisionStatus.PENDING:
                raise DecisionConflictError("Decision is already resolved")
            if decision.version != expected_version:
                raise DecisionConflictError("Decision version conflict")
            if resolution not in decision.options:
                raise DecisionConflictError("Decision resolution is not offered")
            if mission.status != MissionStatus.WAITING_DECISION:
                raise DecisionConflictError(
                    "Mission is not waiting for a Decision"
                )
            occurred_at = datetime.now(timezone.utc)
            if decision.expires_at is not None and decision.expires_at <= occurred_at:
                raise DecisionConflictError("Decision has expired")
            work_unit = await repository.get_work_unit_for_update(
                decision.work_unit_id
            )
            if work_unit is None or work_unit.mission_id != mission_id:
                raise WorkUnitNotFoundError(decision.work_unit_id)
            if (
                work_unit.status != WorkUnitStatus.VERIFYING
                or work_unit.attempt != decision.attempt
            ):
                raise DecisionConflictError(
                    "Decision no longer matches the verifying WorkUnit attempt"
                )

            if resolution == DecisionResolution.RETRY_WORK_UNIT:
                contract = await repository.get_contract(
                    mission.contract_id, mission.contract_version
                )
                if contract is None:
                    raise WorkUnitNotReadyError("mission contract not found")
                if work_unit.attempt >= contract.budgets.retries + 1:
                    raise DecisionConflictError("work unit retry budget is exhausted")

            resolved_decision = Decision.model_validate(
                {
                    **decision.model_dump(),
                    "status": DecisionStatus.RESOLVED,
                    "version": decision.version + 1,
                    "resolution": resolution,
                    "rationale": rationale,
                    "resolved_by": actor,
                    "resolved_at": occurred_at,
                }
            )
            decision_sequence = (
                await repository.get_last_event_sequence(
                    decision.id,
                    aggregate_type="decision",
                )
                + 1
            )
            decision_event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="decision",
                aggregate_id=decision.id,
                sequence=decision_sequence,
                event_type="decision.lifecycle.resolved",
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission.id,
                payload={
                    "previousStatus": decision.status.value,
                    "status": resolved_decision.status.value,
                    "previousVersion": decision.version,
                    "version": resolved_decision.version,
                    "resolution": resolution.value,
                },
                schema_version=1,
            )
            work_unit_target = (
                WorkUnitStatus.RETRYING
                if resolution == DecisionResolution.RETRY_WORK_UNIT
                else WorkUnitStatus.FAILED
            )
            updated_work_unit = transition_work_unit(
                work_unit,
                work_unit_target,
                occurred_at=occurred_at,
            )
            work_unit_sequence = (
                await repository.get_last_event_sequence(
                    work_unit.id,
                    aggregate_type="work_unit",
                )
                + 1
            )
            work_unit_event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="work_unit",
                aggregate_id=work_unit.id,
                sequence=work_unit_sequence,
                event_type="work_unit.lifecycle.decision_resolved",
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission.id,
                causation_id=decision_event.event_id,
                payload={
                    "previousStatus": work_unit.status.value,
                    "status": updated_work_unit.status.value,
                    "decisionId": decision.id,
                    "resolution": resolution.value,
                },
                schema_version=1,
            )
            mission_target = (
                MissionStatus.RUNNING
                if resolution == DecisionResolution.RETRY_WORK_UNIT
                else MissionStatus.FAILED
            )
            updated_mission = transition_mission(
                mission,
                mission_target,
                occurred_at=occurred_at,
            )
            mission_sequence = await repository.get_last_event_sequence(mission.id) + 1
            mission_event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="mission",
                aggregate_id=mission.id,
                sequence=mission_sequence,
                event_type="mission.lifecycle.decision_resolved",
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission.id,
                causation_id=work_unit_event.event_id,
                payload={
                    "previousStatus": mission.status.value,
                    "status": updated_mission.status.value,
                    "decisionId": decision.id,
                    "resolution": resolution.value,
                },
                schema_version=1,
            )

            await repository.update_decision(resolved_decision)
            await repository.append_event(decision_event)
            await repository.update_work_unit(updated_work_unit)
            await repository.append_event(work_unit_event)
            await repository.update_mission(updated_mission)
            await repository.append_event(mission_event)
            return resolved_decision, updated_work_unit, updated_mission

    async def expire_next_decision(
        self,
        *,
        occurred_at: datetime | None = None,
    ) -> DecisionExpiryOutcome:
        expiration_time = occurred_at or datetime.now(timezone.utc)
        if expiration_time.tzinfo is None or expiration_time.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        actor = ActorRef(type=ActorType.SERVICE, id="mission-control")
        async with self._repository.transaction() as repository:
            candidate = await repository.get_expired_decision_candidate_for_update(
                expiration_time
            )
            if candidate is None:
                return DecisionExpiryOutcome(None, None, None)
            mission, decision = candidate
            if decision.mission_id != mission.id:
                raise DecisionConflictError(
                    "expired Decision candidate belongs to another Mission"
                )
            if mission.status != MissionStatus.WAITING_DECISION:
                raise DecisionConflictError(
                    "expired Decision Mission is not waiting for a Decision"
                )
            if decision.status != DecisionStatus.PENDING:
                raise DecisionConflictError("expired Decision candidate is not pending")
            if decision.expires_at is None or decision.expires_at > expiration_time:
                raise DecisionConflictError("Decision has not expired")

            work_unit = await repository.get_work_unit_for_update(
                decision.work_unit_id
            )
            if work_unit is None or work_unit.mission_id != mission.id:
                raise WorkUnitNotFoundError(decision.work_unit_id)
            if (
                work_unit.status != WorkUnitStatus.VERIFYING
                or work_unit.attempt != decision.attempt
            ):
                raise DecisionConflictError(
                    "expired Decision no longer matches the verifying WorkUnit attempt"
                )

            expired_decision = Decision.model_validate(
                {
                    **decision.model_dump(),
                    "status": DecisionStatus.EXPIRED,
                    "version": decision.version + 1,
                    "rationale": "Decision expired before human resolution.",
                    "resolved_by": actor,
                    "resolved_at": expiration_time,
                }
            )
            decision_sequence = (
                await repository.get_last_event_sequence(
                    decision.id,
                    aggregate_type="decision",
                )
                + 1
            )
            decision_event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="decision",
                aggregate_id=decision.id,
                sequence=decision_sequence,
                event_type="decision.lifecycle.expired",
                actor=actor,
                occurred_at=expiration_time,
                correlation_id=mission.id,
                payload={
                    "previousStatus": decision.status.value,
                    "status": expired_decision.status.value,
                    "previousVersion": decision.version,
                    "version": expired_decision.version,
                    "expiresAt": decision.expires_at.isoformat(),
                },
                schema_version=1,
            )
            updated_work_unit = transition_work_unit(
                work_unit,
                WorkUnitStatus.FAILED,
                occurred_at=expiration_time,
            )
            work_unit_sequence = (
                await repository.get_last_event_sequence(
                    work_unit.id,
                    aggregate_type="work_unit",
                )
                + 1
            )
            work_unit_event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="work_unit",
                aggregate_id=work_unit.id,
                sequence=work_unit_sequence,
                event_type="work_unit.lifecycle.decision_expired",
                actor=actor,
                occurred_at=expiration_time,
                correlation_id=mission.id,
                causation_id=decision_event.event_id,
                payload={
                    "previousStatus": work_unit.status.value,
                    "status": updated_work_unit.status.value,
                    "decisionId": decision.id,
                },
                schema_version=1,
            )
            updated_mission = transition_mission(
                mission,
                MissionStatus.FAILED,
                occurred_at=expiration_time,
            )
            mission_sequence = await repository.get_last_event_sequence(mission.id) + 1
            mission_event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="mission",
                aggregate_id=mission.id,
                sequence=mission_sequence,
                event_type="mission.lifecycle.decision_expired",
                actor=actor,
                occurred_at=expiration_time,
                correlation_id=mission.id,
                causation_id=work_unit_event.event_id,
                payload={
                    "previousStatus": mission.status.value,
                    "status": updated_mission.status.value,
                    "decisionId": decision.id,
                },
                schema_version=1,
            )

            await repository.update_decision(expired_decision)
            await repository.append_event(decision_event)
            await repository.update_work_unit(updated_work_unit)
            await repository.append_event(work_unit_event)
            await repository.update_mission(updated_mission)
            await repository.append_event(mission_event)
            return DecisionExpiryOutcome(
                expired_decision,
                updated_work_unit,
                updated_mission,
            )

    async def _load_fork_source(
        self,
        repository: MissionRepository,
        *,
        source_mission_id: str,
        checkpoint_id: str,
        artifact_refs: list[ArtifactRef],
        for_update: bool = False,
    ) -> tuple[Mission, WorkUnit, ExecutionCheckpoint, MissionContract, list[Artifact]]:
        get_mission = (
            repository.get_mission_for_update if for_update else repository.get_mission
        )
        source = await get_mission(source_mission_id)
        if source is None:
            raise MissionNotFoundError(source_mission_id)
        checkpoint = await repository.get_execution_checkpoint(checkpoint_id)
        if checkpoint is None or checkpoint.mission_id != source_mission_id:
            raise WorkUnitNotReadyError("Mission fork checkpoint was not found")
        if (
            not checkpoint.terminal
            or checkpoint.phase != ExecutionCheckpointPhase.EXECUTION_COMPLETED
        ):
            raise WorkUnitNotReadyError(
                "Mission fork requires a successful terminal checkpoint"
            )
        get_work_unit = (
            repository.get_work_unit_for_update
            if for_update
            else repository.get_work_unit
        )
        source_work_unit = await get_work_unit(checkpoint.work_unit_id)
        if source_work_unit is None or source_work_unit.mission_id != source_mission_id:
            raise WorkUnitNotFoundError(checkpoint.work_unit_id)
        if source_work_unit.status != WorkUnitStatus.SUCCEEDED:
            raise WorkUnitNotReadyError(
                "Mission fork requires a SUCCEEDED source WorkUnit"
            )
        if source_work_unit.attempt != checkpoint.attempt:
            raise WorkUnitNotReadyError(
                "Mission fork checkpoint does not match the source WorkUnit attempt"
            )
        contract = await repository.get_contract(
            source.contract_id,
            source.contract_version,
        )
        if contract is None:
            raise WorkUnitNotReadyError("source Mission Contract was not found")
        lineage_workspace = await repository.get_contract_lineage_workspace(contract.id)
        if lineage_workspace != source.workspace_id:
            raise WorkUnitNotReadyError(
                "source Contract lineage workspace ownership does not match"
            )
        artifacts = await self._validate_artifact_refs(
            repository,
            source_mission_id,
            artifact_refs,
            work_unit_id=source_work_unit.id,
            attempt=checkpoint.attempt,
        )
        return source, source_work_unit, checkpoint, contract, artifacts

    @staticmethod
    async def _match_existing_fork(
        repository: MissionRepository,
        *,
        mission: Mission,
        work_unit: WorkUnit,
        for_update: bool = False,
    ) -> MissionForkOutcome | None:
        get_mission = (
            repository.get_mission_for_update if for_update else repository.get_mission
        )
        get_work_unit = (
            repository.get_work_unit_for_update
            if for_update
            else repository.get_work_unit
        )
        existing_mission = await get_mission(mission.id)
        existing_work_unit = await get_work_unit(work_unit.id)
        if existing_mission is None and existing_work_unit is None:
            return None
        if existing_mission is None or existing_work_unit is None:
            raise ValueError("Mission fork ids conflict with incomplete existing state")

        mission_matches = (
            existing_mission.workspace_id == mission.workspace_id
            and existing_mission.title == mission.title
            and existing_mission.objective == mission.objective
            and existing_mission.source == mission.source
            and existing_mission.contract_id == mission.contract_id
            and existing_mission.contract_version == mission.contract_version
            and existing_mission.created_by.type == mission.created_by.type
            and existing_mission.created_by.id == mission.created_by.id
        )
        immutable_work_unit_fields = (
            "mission_id",
            "parent_work_unit_id",
            "assigned_agent_id",
            "kind",
            "dependencies",
            "input_refs",
            "expected_outputs",
            "required_capabilities",
            "assigned_adapter",
        )
        work_unit_matches = all(
            getattr(existing_work_unit, field_name) == getattr(work_unit, field_name)
            for field_name in immutable_work_unit_fields
        )
        if not mission_matches or not work_unit_matches:
            raise ValueError("Mission fork ids already exist with different content")
        return MissionForkOutcome(
            mission=existing_mission,
            work_unit=existing_work_unit,
        )

    async def _validate_artifact_refs(
        self,
        repository: MissionRepository,
        mission_id: str,
        artifact_refs: list[ArtifactRef],
        *,
        work_unit_id: str | None = None,
        attempt: int | None = None,
    ) -> list[Artifact]:
        artifact_ids = [artifact_ref.id for artifact_ref in artifact_refs]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise WorkUnitNotReadyError("artifact references must be unique")
        artifacts: list[Artifact] = []
        for artifact_ref in artifact_refs:
            artifact = await repository.get_artifact(artifact_ref.id)
            if artifact is None:
                raise WorkUnitNotReadyError(
                    f"artifact is not registered: {artifact_ref.id}"
                )
            if artifact.mission_id != mission_id:
                raise WorkUnitNotReadyError(
                    f"artifact belongs to another mission: {artifact_ref.id}"
                )
            if artifact.digest.lower() != artifact_ref.digest.lower():
                raise WorkUnitNotReadyError(
                    f"artifact digest does not match: {artifact_ref.id}"
                )
            if work_unit_id is not None and artifact.work_unit_id != work_unit_id:
                raise WorkUnitNotReadyError(
                    f"artifact belongs to another work unit: {artifact_ref.id}"
                )
            if attempt is not None and artifact.attempt != attempt:
                raise WorkUnitNotReadyError(
                    f"artifact belongs to another attempt: {artifact_ref.id}"
                )
            artifacts.append(artifact)
        return artifacts

    async def verify_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        criterion_id: str,
        verifier_id: str,
        verifier_version: str,
        configuration_digest: str | None,
        verdict: EvidenceVerdict,
        artifact_refs: list[ArtifactRef],
        summary: str,
        actor: ActorRef,
    ) -> tuple[Evidence, WorkUnit, Mission]:
        if actor.type != ActorType.VERIFIER:
            raise ValueError("only verifier actors can record Evidence")
        work_unit = await self._repository.get_work_unit(work_unit_id)
        if work_unit is None or work_unit.mission_id != mission_id:
            raise WorkUnitNotFoundError(work_unit_id)
        if work_unit.status != WorkUnitStatus.VERIFYING:
            raise WorkUnitNotReadyError(
                "Evidence can only be recorded for a VERIFYING work unit"
            )
        mission = await self._repository.get_mission(mission_id)
        if mission is None:
            raise MissionNotFoundError(mission_id)
        if mission.status not in {MissionStatus.RUNNING, MissionStatus.VERIFYING}:
            raise WorkUnitNotReadyError(
                "mission must be RUNNING or VERIFYING to record Evidence"
            )
        contract = await self._repository.get_contract(
            mission.contract_id, mission.contract_version
        )
        if contract is None:
            raise WorkUnitNotReadyError("mission contract not found")
        if criterion_id not in {
            criterion.id for criterion in contract.acceptance_criteria
        }:
            raise WorkUnitNotReadyError(
                "Evidence criterion is not part of the mission contract"
            )
        if verdict == EvidenceVerdict.INCONCLUSIVE:
            raise WorkUnitNotReadyError(
                "INCONCLUSIVE verification requires a Mission Decision"
            )
        verification_attempt = work_unit.attempt if work_unit.attempt > 0 else None
        artifacts = await self._validate_artifact_refs(
            self._repository,
            mission_id,
            artifact_refs,
            work_unit_id=work_unit_id,
            attempt=verification_attempt,
        )
        evaluation_plan: ArtifactSetEvaluationPlan | None = None
        if verdict == EvidenceVerdict.PASS:
            evaluation_plan = self._admit_pass_evidence(
                contract=contract,
                work_unit=work_unit,
                artifacts=tuple(artifacts),
                criterion_id=criterion_id,
                configuration_digest=configuration_digest,
            )
        if self._artifact_byte_verifier is None:
            raise ArtifactBytesUnavailableError(
                "artifact byte verifier is not configured"
            )
        byte_verifications = await self._artifact_byte_verifier.verify_all(artifacts)
        evaluation_result: VerificationEvaluationResult | None = None
        if evaluation_plan is not None:
            evaluation_result = self._verification_evaluator.evaluate(
                evaluation_plan,
                tuple(artifacts),
                tuple(byte_verifications),
            )

        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)
            if mission.status not in {MissionStatus.RUNNING, MissionStatus.VERIFYING}:
                raise WorkUnitNotReadyError(
                    "mission must be RUNNING or VERIFYING to record Evidence"
                )

            work_unit = await repository.get_work_unit_for_update(work_unit_id)
            if work_unit is None or work_unit.mission_id != mission_id:
                raise WorkUnitNotFoundError(work_unit_id)
            if work_unit.status != WorkUnitStatus.VERIFYING:
                raise WorkUnitNotReadyError(
                    "Evidence can only be recorded for a VERIFYING work unit"
                )

            contract = await repository.get_contract(
                mission.contract_id, mission.contract_version
            )
            if contract is None:
                raise WorkUnitNotReadyError("mission contract not found")
            if criterion_id not in {
                criterion.id for criterion in contract.acceptance_criteria
            }:
                raise WorkUnitNotReadyError(
                    "Evidence criterion is not part of the mission contract"
                )
            current_artifacts = await self._validate_artifact_refs(
                repository,
                mission_id,
                artifact_refs,
                work_unit_id=work_unit.id,
                attempt=work_unit.attempt if work_unit.attempt > 0 else None,
            )
            if current_artifacts != artifacts:
                raise WorkUnitNotReadyError(
                    "artifact metadata changed during byte verification"
                )
            admitted_evaluation: VerificationEvaluationResult | None = None
            if verdict == EvidenceVerdict.PASS:
                current_plan = self._admit_pass_evidence(
                    contract=contract,
                    work_unit=work_unit,
                    artifacts=tuple(current_artifacts),
                    criterion_id=criterion_id,
                    configuration_digest=configuration_digest,
                )
                current_evaluation = self._verification_evaluator.evaluate(
                    current_plan,
                    tuple(current_artifacts),
                    tuple(byte_verifications),
                )
                if current_evaluation != evaluation_result:
                    raise WorkUnitNotReadyError(
                        "verification evaluation changed before Evidence admission"
                    )
                admitted_evaluation = current_evaluation

            occurred_at = datetime.now(timezone.utc)
            evidence_id = new_identifier("evd")
            verifier = VerifierRef(
                id=verifier_id,
                version=verifier_version,
                configuration_digest=configuration_digest,
            )
            integrity_hash = self._evidence_integrity_hasher.compute(
                EvidenceIntegrityMaterial(
                    evidence_id=evidence_id,
                    mission_id=mission_id,
                    contract_id=contract.id,
                    contract_version=contract.version,
                    work_unit_id=work_unit_id,
                    work_unit_attempt=work_unit.attempt,
                    criterion_id=criterion_id,
                    verifier=verifier,
                    verdict=verdict,
                    artifact_refs=tuple(artifact_refs),
                    artifacts=tuple(current_artifacts),
                    byte_verifications=tuple(byte_verifications),
                    evaluation=admitted_evaluation,
                    summary=summary,
                    generated_at=occurred_at,
                )
            )
            evidence = Evidence(
                id=evidence_id,
                mission_id=mission_id,
                work_unit_id=work_unit_id,
                criterion_id=criterion_id,
                verifier=verifier,
                verdict=verdict,
                artifact_refs=artifact_refs,
                summary=summary,
                generated_at=occurred_at,
                integrity_hash=integrity_hash,
            )
            evidence_event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="evidence",
                aggregate_id=evidence.id,
                sequence=1,
                event_type="evidence.lifecycle.recorded",
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission_id,
                payload=evidence.to_public_dict(),
                schema_version=1,
            )
            await repository.add_evidence(evidence)
            await repository.append_event(evidence_event)

            if verdict == EvidenceVerdict.PASS:
                work_unit_event_type = "work_unit.lifecycle.verified"
                work_unit_target = WorkUnitStatus.SUCCEEDED
            else:
                work_unit_event_type = "work_unit.lifecycle.verification_failed"
                work_unit_target = WorkUnitStatus.FAILED
            updated_work_unit = transition_work_unit(
                work_unit,
                work_unit_target,
                occurred_at=occurred_at,
            )
            await repository.update_work_unit(updated_work_unit)
            work_unit_sequence = (
                await repository.get_last_event_sequence(
                    work_unit_id,
                    aggregate_type="work_unit",
                )
                + 1
            )
            work_unit_event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="work_unit",
                aggregate_id=work_unit_id,
                sequence=work_unit_sequence,
                event_type=work_unit_event_type,
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission_id,
                payload={
                    "previousStatus": work_unit.status.value,
                    "status": updated_work_unit.status.value,
                    "evidenceId": evidence.id,
                    "verdict": verdict.value,
                },
                schema_version=1,
            )
            await repository.append_event(work_unit_event)

            updated_mission = mission
            if verdict == EvidenceVerdict.FAIL:
                updated_mission = await self._fail_mission_for_work_unit(
                    repository,
                    mission,
                    work_unit_id=work_unit.id,
                    actor=actor,
                    reason=summary,
                    occurred_at=occurred_at,
                    causation_id=work_unit_event.event_id,
                )
            elif verdict == EvidenceVerdict.PASS:
                work_units = await repository.list_work_units(mission_id)
                passed_criteria = await repository.list_passed_evidence_criterion_ids(
                    mission_id
                )
                required_criteria = {
                    criterion.id
                    for criterion in contract.acceptance_criteria
                    if criterion.required
                }
                if (
                    work_units
                    and all(
                        item.status == WorkUnitStatus.SUCCEEDED for item in work_units
                    )
                    and required_criteria <= passed_criteria
                ):
                    mission_sequence = await repository.get_last_event_sequence(
                        mission_id
                    )
                    if mission.status == MissionStatus.RUNNING:
                        updated_mission = transition_mission(
                            mission,
                            MissionStatus.VERIFYING,
                            occurred_at=occurred_at,
                        )
                        mission_sequence += 1
                        await repository.append_event(
                            EventEnvelope(
                                event_id=new_identifier("evt"),
                                aggregate_type="mission",
                                aggregate_id=mission_id,
                                sequence=mission_sequence,
                                event_type="mission.lifecycle.verifying",
                                actor=actor,
                                occurred_at=occurred_at,
                                correlation_id=mission_id,
                                payload={
                                    "previousStatus": mission.status.value,
                                    "status": updated_mission.status.value,
                                },
                                schema_version=1,
                            )
                        )
                    updated_mission = transition_mission(
                        updated_mission,
                        MissionStatus.SUCCEEDED,
                        occurred_at=occurred_at,
                    )
                    mission_sequence += 1
                    await repository.append_event(
                        EventEnvelope(
                            event_id=new_identifier("evt"),
                            aggregate_type="mission",
                            aggregate_id=mission_id,
                            sequence=mission_sequence,
                            event_type="mission.lifecycle.succeeded",
                            actor=actor,
                            occurred_at=occurred_at,
                            correlation_id=mission_id,
                            payload={
                                "previousStatus": (
                                    MissionStatus.VERIFYING.value
                                    if mission.status == MissionStatus.RUNNING
                                    else mission.status.value
                                ),
                                "status": updated_mission.status.value,
                            },
                            schema_version=1,
                        )
                    )
                    await repository.update_mission(updated_mission)

            return evidence, updated_work_unit, updated_mission

    def _admit_pass_evidence(
        self,
        *,
        contract: MissionContract,
        work_unit: WorkUnit,
        artifacts: tuple[Artifact, ...],
        criterion_id: str,
        configuration_digest: str | None,
    ) -> ArtifactSetEvaluationPlan:
        decision = self._verification_policy_resolver.resolve(
            contract,
            work_unit,
            artifacts,
        )
        if decision.plan is None:
            assert decision.reason is not None
            raise WorkUnitNotReadyError(
                "PASS evidence is not admitted by the evaluation policy: "
                f"{decision.reason.value}"
            )
        if criterion_id != decision.plan.criterion_id:
            raise WorkUnitNotReadyError(
                "Evidence criterion does not match the evaluation policy"
            )
        if configuration_digest is None:
            raise WorkUnitNotReadyError(
                "PASS evidence requires the evaluation policy configuration digest"
            )
        if configuration_digest != decision.plan.configuration_digest:
            raise WorkUnitNotReadyError(
                "Evidence configuration digest does not match the evaluation policy"
            )
        return decision.plan

    async def fail_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        lease_id: str,
        runner_id: str,
        actor: ActorRef,
        reason: str | None = None,
    ) -> WorkUnit:
        return await self._transition_execution_work_unit(
            mission_id,
            work_unit_id,
            target=WorkUnitStatus.FAILED,
            event_type="work_unit.lifecycle.failed",
            lease_id=lease_id,
            runner_id=runner_id,
            actor=actor,
            reason=reason,
        )

    async def retry_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        lease_id: str,
        runner_id: str,
        actor: ActorRef,
        reason: str | None = None,
    ) -> WorkUnit:
        return await self._transition_execution_work_unit(
            mission_id,
            work_unit_id,
            target=WorkUnitStatus.RETRYING,
            event_type="work_unit.lifecycle.retrying",
            lease_id=lease_id,
            runner_id=runner_id,
            actor=actor,
            reason=reason,
        )

    async def _transition_execution_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        target: WorkUnitStatus,
        event_type: str,
        lease_id: str,
        runner_id: str,
        actor: ActorRef,
        reason: str | None = None,
        artifact_refs: list[ArtifactRef] | None = None,
    ) -> WorkUnit:
        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)
            if mission.status != MissionStatus.RUNNING:
                raise WorkUnitNotReadyError("work units require a RUNNING mission")

            work_unit = await repository.get_work_unit_for_update(work_unit_id)
            if work_unit is None or work_unit.mission_id != mission_id:
                raise WorkUnitNotFoundError(work_unit_id)
            if work_unit.lease is None:
                raise LeaseOwnershipError("work unit has no active lease")
            if work_unit.lease.id != lease_id:
                raise LeaseOwnershipError("lease id does not match the work unit")
            if work_unit.lease.runner_id != runner_id:
                raise LeaseOwnershipError("lease belongs to another runner")

            occurred_at = datetime.now(timezone.utc)
            if work_unit.lease.expires_at <= occurred_at:
                raise LeaseExpiredError("work unit lease has expired")
            if target == WorkUnitStatus.RETRYING:
                contract = await repository.get_contract(
                    mission.contract_id, mission.contract_version
                )
                if contract is None:
                    raise WorkUnitNotReadyError("mission contract not found")
                if work_unit.attempt >= contract.budgets.retries + 1:
                    raise WorkUnitNotReadyError("work unit retry budget is exhausted")
            if target == WorkUnitStatus.VERIFYING and not artifact_refs:
                raise WorkUnitNotReadyError(
                    "work unit verification requires at least one artifact"
                )
            if target == WorkUnitStatus.VERIFYING:
                await self._validate_artifact_refs(
                    repository,
                    mission_id,
                    artifact_refs or [],
                    work_unit_id=work_unit.id,
                    attempt=work_unit.attempt,
                )
            updated = transition_work_unit(
                work_unit,
                target,
                occurred_at=occurred_at,
            )
            sequence = (
                await repository.get_last_event_sequence(
                    work_unit.id,
                    aggregate_type="work_unit",
                )
                + 1
            )
            event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="work_unit",
                aggregate_id=work_unit.id,
                sequence=sequence,
                event_type=event_type,
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission_id,
                payload={
                    "previousStatus": work_unit.status.value,
                    "status": updated.status.value,
                    "leaseId": lease_id,
                    "attempt": updated.attempt,
                    **(
                        {
                            "artifactRefs": [
                                ref.to_public_dict() for ref in artifact_refs
                            ]
                        }
                        if artifact_refs is not None
                        else {}
                    ),
                    **({"reason": reason} if reason is not None else {}),
                },
                schema_version=1,
            )
            await repository.update_work_unit(updated)
            await repository.append_event(event)
            if target == WorkUnitStatus.FAILED:
                await self._fail_mission_for_work_unit(
                    repository,
                    mission,
                    work_unit_id=work_unit.id,
                    actor=actor,
                    reason=reason or "work unit execution failed",
                    occurred_at=occurred_at,
                    causation_id=event.event_id,
                )
        return updated

    async def recover_expired_lease(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        actor: ActorRef,
    ) -> WorkUnit:
        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)
            if mission.status != MissionStatus.RUNNING:
                raise WorkUnitNotReadyError("work units require a RUNNING mission")

            work_unit = await repository.get_work_unit_for_update(work_unit_id)
            if work_unit is None or work_unit.mission_id != mission_id:
                raise WorkUnitNotFoundError(work_unit_id)
            if work_unit.lease is None:
                raise LeaseOwnershipError("work unit has no active lease")

            occurred_at = datetime.now(timezone.utc)
            if work_unit.lease.expires_at > occurred_at:
                raise LeaseExpiredError("work unit lease has not expired")
            contract = await repository.get_contract(
                mission.contract_id, mission.contract_version
            )
            if contract is None:
                raise WorkUnitNotReadyError("mission contract not found")
            retry_budget_exhausted = work_unit.attempt >= contract.budgets.retries + 1
            updated = transition_work_unit(
                work_unit,
                (
                    WorkUnitStatus.FAILED
                    if retry_budget_exhausted
                    else WorkUnitStatus.RETRYING
                ),
                occurred_at=occurred_at,
            )
            sequence = (
                await repository.get_last_event_sequence(
                    work_unit.id,
                    aggregate_type="work_unit",
                )
                + 1
            )
            event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="work_unit",
                aggregate_id=work_unit.id,
                sequence=sequence,
                event_type="work_unit.lifecycle.lease_expired",
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission_id,
                payload={
                    "previousStatus": work_unit.status.value,
                    "status": updated.status.value,
                    "leaseId": work_unit.lease.id,
                    "attempt": updated.attempt,
                    "retryBudgetExhausted": retry_budget_exhausted,
                },
                schema_version=1,
            )
            await repository.update_work_unit(updated)
            await repository.append_event(event)
            if retry_budget_exhausted:
                await self._fail_mission_for_work_unit(
                    repository,
                    mission,
                    work_unit_id=work_unit.id,
                    actor=actor,
                    reason="work unit retry budget exhausted after lease expiry",
                    occurred_at=occurred_at,
                    causation_id=event.event_id,
                )
        return updated
