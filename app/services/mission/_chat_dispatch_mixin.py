"""Mission Control admission for one catalog-bound chat executor."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain import (
    ActorRef,
    EventEnvelope,
    MissionSourceType,
    MissionStatus,
    OutputSpec,
    WorkUnit,
    WorkUnitStatus,
)
from app.services.agent_binding_service import AgentBinding
from app.services.mission._types import MissionNotFoundError, new_identifier

CHAT_EXECUTION_ADAPTER = "function-calling"


def _existing_chat_unit(existing: list[WorkUnit], identifier: str, binding: AgentBinding) -> WorkUnit | None:
    if not existing:
        return None
    if len(existing) != 1 or (
        existing[0].id != identifier or existing[0].kind != "desktop.task"
        or existing[0].parent_work_unit_id is not None
        or existing[0].assigned_agent_id != binding.agent_id
        or existing[0].assigned_adapter != binding.adapter_type
        or existing[0].required_capabilities
    ):
        raise ValueError("chat Mission already has a different execution plan")
    return existing[0]


def validate_chat_dispatch(participants: list[dict], unresolved: list[dict]) -> AgentBinding:
    """Reject unsupported routing before a Mission is created.

    Catalog capability tags describe availability, not Contract tool grants.
    This slice admits one executor and does not infer a multi-agent workflow.
    """
    if unresolved:
        raise ValueError("chat executor mentions must resolve unambiguously")
    if len(participants) != 1:
        raise ValueError("chat dispatch requires exactly one enabled executor")
    binding = AgentBinding.from_mapping(participants[0])
    if binding.adapter_type != CHAT_EXECUTION_ADAPTER:
        raise ValueError("chat dispatch supports only the function-calling adapter")
    return binding


class MissionChatDispatchMixin:
    """Create the durable chat root without executing or verifying it."""

    async def create_chat_work_unit(self, mission_id: str, *, workspace_id: str) -> WorkUnit:
        async with self._repository.transaction() as repository:
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise MissionNotFoundError(mission_id)
            if mission.workspace_id != workspace_id:
                raise ValueError("chat dispatch Mission belongs to another workspace")
            if mission.source.type != MissionSourceType.CHAT:
                raise ValueError("chat dispatch requires a chat Mission")
            if mission.status != MissionStatus.RUNNING:
                raise ValueError("chat dispatch requires a RUNNING Mission")
            metadata = mission.source.metadata or {}
            binding = validate_chat_dispatch(
                metadata.get("participants", []), metadata.get("unresolved_mentions", []),
            )
            identifier = f"wu-chat-{mission.id}"
            existing = _existing_chat_unit(await repository.list_work_units(mission_id), identifier, binding)
            if existing is not None:
                return existing
            if self._agent_binding_resolver is None:
                raise ValueError("chat dispatch requires a scoped catalog resolver")
            current_binding = await self._agent_binding_resolver.resolve(
                scope_id=workspace_id, agent_id=binding.agent_id,
            )
            if current_binding != binding:
                raise ValueError("chat executor catalog binding changed or is disabled")
            contract = await repository.get_contract(mission.contract_id, mission.contract_version)
            if contract is None:
                raise ValueError("chat Mission contract not found")
            work_unit = WorkUnit(
                id=identifier, mission_id=mission_id, kind="desktop.task",
                assigned_agent_id=binding.agent_id, assigned_adapter=binding.adapter_type,
                dependencies=[], input_refs=[], expected_outputs=[OutputSpec(kind="text", required=False)],
                required_capabilities=[], status=WorkUnitStatus.PENDING, attempt=0,
            )
            event = EventEnvelope(
                event_id=new_identifier("evt"), aggregate_type="work_unit",
                aggregate_id=work_unit.id, sequence=1, event_type="work_unit.lifecycle.created",
                actor=ActorRef(type="service", id="mission-control.chat-dispatch"),
                occurred_at=datetime.now(UTC), correlation_id=mission_id,
                payload={"missionId": mission_id, "kind": work_unit.kind,
                         "status": work_unit.status.value, "assignedAgentId": binding.agent_id,
                         "assignedAdapter": binding.adapter_type}, schema_version=1,
            )
            await repository.add_work_unit(work_unit)
            await repository.append_event(event)
        return work_unit
