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


class MissionWorkUnitLifecycleMixin:
    """Mixin holding MissionService work_unit lifecycle methods."""

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
