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


class MissionRunnerClaimMixin:
    """Mixin holding MissionService runner claim & lease methods."""

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
