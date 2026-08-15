from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.domain import (
    ActorRef,
    ActorType,
    Artifact,
    ArtifactKind,
    ArtifactRef,
    ArtifactRetention,
    ArtifactSensitivity,
    EventEnvelope,
    Evidence,
    EvidenceVerdict,
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


class MissionService:
    def __init__(
        self,
        repository: MissionRepository | None = None,
        *,
        artifact_byte_verifier: ArtifactByteVerifier | None = None,
        agent_binding_resolver: AgentBindingResolver | None = None,
    ) -> None:
        self._repository = repository or MissionRepository()
        self._artifact_byte_verifier = artifact_byte_verifier
        self._agent_binding_resolver = agent_binding_resolver

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
                "status": mission.status.value,
            },
            schema_version=1,
        )
        async with self._repository.transaction() as repository:
            existing_contract = await repository.get_contract(contract.id)
            if existing_contract is None:
                await repository.add_contract(contract)
            elif existing_contract != contract:
                raise ValueError("contract id already exists with different content")
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
            mission_sequence = (
                await repository.get_last_event_sequence(mission_id) + 1
            )
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
            await repository.update_mission(updated_mission)
            await repository.append_event(mission_event)

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

            contract = await repository.get_contract(mission.contract_id)
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
                raise LeaseOwnershipError("lease id does not match the parent work unit")
            if parent.lease.runner_id != runner_id:
                raise LeaseOwnershipError("parent lease belongs to another runner")

            occurred_at = datetime.now(timezone.utc)
            if parent.lease.expires_at <= occurred_at:
                raise LeaseExpiredError("parent work unit lease has expired")

            contract = await repository.get_contract(mission.contract_id)
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
    ) -> WorkUnit | None:
        """Atomically claim the next ready unit for one explicit binding."""
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)
            if mission.status != MissionStatus.RUNNING:
                raise WorkUnitNotReadyError("work units require a RUNNING mission")

            allow_inbound_root = mission.source.type == MissionSourceType.A2A_INBOUND
            work_unit = await repository.get_bound_work_unit_for_claim(
                mission_id,
                agent_id=agent_id,
                adapter_type=adapter_type,
                allow_inbound_root=allow_inbound_root,
            )
            if work_unit is None:
                return None

            if (
                work_unit.assigned_agent_id != agent_id
                or work_unit.assigned_adapter != adapter_type
            ):
                raise WorkUnitNotReadyError(
                    "claim repository returned a WorkUnit for another binding"
                )
            if work_unit.parent_work_unit_id is not None:
                claim_mode = "delegated"
            elif allow_inbound_root and work_unit.kind == "a2a.inbound":
                claim_mode = "a2a.inbound"
            else:
                raise WorkUnitNotReadyError(
                    "claim repository returned an ineligible root WorkUnit"
                )

            # Keep the application-level dependency check as a defense-in-depth
            # guard for alternate repository implementations and test doubles.
            for dependency_id in work_unit.dependencies:
                dependency = await repository.get_work_unit(dependency_id)
                if dependency is None or dependency.mission_id != mission_id:
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
                correlation_id=mission_id,
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
        integrity_hash: str,
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
        verification_attempt = work_unit.attempt if work_unit.attempt > 0 else None
        artifacts = await self._validate_artifact_refs(
            self._repository,
            mission_id,
            artifact_refs,
            work_unit_id=work_unit_id,
            attempt=verification_attempt,
        )
        if self._artifact_byte_verifier is None:
            raise ArtifactBytesUnavailableError(
                "artifact byte verifier is not configured"
            )
        await self._artifact_byte_verifier.verify_all(artifacts)

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

            contract = await repository.get_contract(mission.contract_id)
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

            occurred_at = datetime.now(timezone.utc)
            evidence = Evidence(
                id=new_identifier("evd"),
                mission_id=mission_id,
                work_unit_id=work_unit_id,
                criterion_id=criterion_id,
                verifier=VerifierRef(
                    id=verifier_id,
                    version=verifier_version,
                    configuration_digest=configuration_digest,
                ),
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

            updated_work_unit = work_unit
            work_unit_event_type = {
                EvidenceVerdict.PASS: "work_unit.lifecycle.verified",
                EvidenceVerdict.FAIL: "work_unit.lifecycle.verification_failed",
                EvidenceVerdict.INCONCLUSIVE: (
                    "work_unit.lifecycle.verification_inconclusive"
                ),
            }[verdict]
            if verdict != EvidenceVerdict.INCONCLUSIVE:
                updated_work_unit = transition_work_unit(
                    work_unit,
                    (
                        WorkUnitStatus.SUCCEEDED
                        if verdict == EvidenceVerdict.PASS
                        else WorkUnitStatus.FAILED
                    ),
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
                passed_criteria = (
                    await repository.list_passed_evidence_criterion_ids(mission_id)
                )
                required_criteria = {
                    criterion.id
                    for criterion in contract.acceptance_criteria
                    if criterion.required
                }
                if (
                    work_units
                    and all(
                        item.status == WorkUnitStatus.SUCCEEDED
                        for item in work_units
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
                contract = await repository.get_contract(mission.contract_id)
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
                        {"artifactRefs": [ref.to_public_dict() for ref in artifact_refs]}
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
            contract = await repository.get_contract(mission.contract_id)
            if contract is None:
                raise WorkUnitNotReadyError("mission contract not found")
            retry_budget_exhausted = (
                work_unit.attempt >= contract.budgets.retries + 1
            )
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
