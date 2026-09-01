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

# Re-export types/errors/helpers from _types — MissionService uses them.
# Note: `from module import *` skips underscore-prefixed names, so we list
# the ones actually referenced by the MissionService implementation.
from app.services.mission._types import *  # noqa: F401,F403  # surface errors/helpers
from app.services.mission._types import (
    _A2A_OUTBOUND_ADAPTER,
    _DESKTOP_TASK_WORK_UNIT_KIND,
    _MAX_VERIFICATION_ARTIFACTS,
    _VERIFICATION_ARTIFACT_FIELDS,
    _checkpoint_event_payload,
)


class MissionForkMixin:
    """Mixin holding MissionService fork methods."""

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
