from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.domain import (
    ActorRef,
    ArtifactRef,
    EventEnvelope,
    Lease,
    Mission,
    MissionContract,
    MissionSource,
    MissionStatus,
    OutputSpec,
    WorkUnit,
    WorkUnitStatus,
    transition_mission,
    transition_work_unit,
)
from app.repositories import MissionRepository


def build_human_actor(user: dict) -> ActorRef:
    return ActorRef(
        type="human",
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


class MissionService:
    def __init__(self, repository: MissionRepository | None = None) -> None:
        self._repository = repository or MissionRepository()

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
        return await self._transition_mission(
            mission_id,
            target=MissionStatus.CANCELLED,
            event_type="mission.lifecycle.cancelled",
            actor=actor,
        )

    async def _transition_mission(
        self,
        mission_id: str,
        *,
        target: MissionStatus,
        event_type: str,
        actor: ActorRef,
    ) -> Mission:
        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)

            occurred_at = datetime.now(timezone.utc)
            updated = transition_mission(mission, target, occurred_at=occurred_at)
            sequence = await repository.get_last_event_sequence(mission_id) + 1
            event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="mission",
                aggregate_id=mission_id,
                sequence=sequence,
                event_type=event_type,
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission_id,
                payload={
                    "previousStatus": mission.status.value,
                    "status": updated.status.value,
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
            event = EventEnvelope(
                event_id=new_identifier("evt"),
                aggregate_type="work_unit",
                aggregate_id=work_unit.id,
                sequence=1,
                event_type="work_unit.lifecycle.created",
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission_id,
                payload={
                    "kind": work_unit.kind,
                    "missionId": mission_id,
                    "status": work_unit.status.value,
                },
                schema_version=1,
            )
            await repository.add_work_unit(work_unit)
            await repository.append_event(event)
        return work_unit

    async def lease_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        runner_id: str,
        actor: ActorRef,
        lease_seconds: int,
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

    async def start_work_unit(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        lease_id: str,
        runner_id: str,
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
            if work_unit.lease.id != lease_id:
                raise LeaseOwnershipError("lease id does not match the work unit")
            if work_unit.lease.runner_id != runner_id:
                raise LeaseOwnershipError("lease belongs to another runner")

            occurred_at = datetime.now(timezone.utc)
            if work_unit.lease.expires_at <= occurred_at:
                raise LeaseExpiredError("work unit lease has expired")
            updated = transition_work_unit(
                work_unit,
                WorkUnitStatus.RUNNING,
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
                event_type="work_unit.lifecycle.started",
                actor=actor,
                occurred_at=occurred_at,
                correlation_id=mission_id,
                payload={
                    "previousStatus": work_unit.status.value,
                    "status": updated.status.value,
                    "leaseId": lease_id,
                    "attempt": updated.attempt,
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
            updated = transition_work_unit(
                work_unit,
                WorkUnitStatus.RETRYING,
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
                },
                schema_version=1,
            )
            await repository.update_work_unit(updated)
            await repository.append_event(event)
        return updated
