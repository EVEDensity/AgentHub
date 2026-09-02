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


class MissionLifecycleMixin:
    """Mixin holding MissionService lifecycle methods."""

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
        # Emit mission.completed session event for terminal transitions.
        if target in {MissionStatus.FAILED, MissionStatus.CANCELLED}:
            await self._emit_session_terminal(
                updated,
                previous_status=mission.status.value,
                reason=reason,
            )
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
        await self._emit_session_terminal(
            updated,
            previous_status=mission.status.value,
            reason=reason,
            extra={"work_unit_id": work_unit_id},
        )
        return updated

    async def add_mission_guidance(
        self,
        mission_id: str,
        *,
        content: str,
        actor: ActorRef,
    ) -> EventEnvelope:
        """Append one run-time guidance entry as a Mission event (P1-1).

        Guidance is an append-only ledger entry — it never mutates the
        objective or transitions Mission state. The desktop runner consumes
        it before the next model call and injects it into the prompt once.
        """
        stripped = content.strip()
        if not stripped:
            raise ValueError("mission guidance content must not be empty")
        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)
            occurred_at = datetime.now(timezone.utc)
            sequence = await repository.get_last_event_sequence(mission.id) + 1
            event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="mission",
                aggregate_id=mission.id,
                sequence=sequence,
                event_type="mission.guidance.added",
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission.id,
                payload={"content": stripped},
                schema_version=1,
            )
            await repository.append_event(event)
        return event

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
