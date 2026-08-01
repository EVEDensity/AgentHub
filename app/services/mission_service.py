from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.domain import ActorRef, EventEnvelope, Mission, MissionContract, MissionSource
from app.repositories import MissionRepository


def build_human_actor(user: dict) -> ActorRef:
    return ActorRef(
        type="human",
        id=str(user["id"]),
        display_name=str(user["name"]) if user.get("name") else None,
    )


def new_identifier(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


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
