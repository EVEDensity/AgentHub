from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any

from app.domain import EventEnvelope, Mission, MissionContract

Execute = Callable[..., Awaitable[None]]
FetchOne = Callable[..., Awaitable[dict[str, Any] | None]]
FetchAll = Callable[..., Awaitable[list[dict[str, Any]]]]
TransactionFactory = Callable[..., Any]


def _encode_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _decode_json_object(value: object, field_name: str) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        raise TypeError(f"database field {field_name} must contain a JSON object")
    return dict(value)


class MissionRepository:
    """PostgreSQL persistence for immutable contracts and Mission snapshots."""

    def __init__(
        self,
        *,
        execute: Execute | None = None,
        fetch_one: FetchOne | None = None,
        fetch_all: FetchAll | None = None,
        transaction_factory: TransactionFactory | None = None,
    ) -> None:
        if execute is None or fetch_one is None or fetch_all is None:
            from app.db.session import aexecute, afetch_all, afetch_one

            execute = execute or aexecute
            fetch_one = fetch_one or afetch_one
            fetch_all = fetch_all or afetch_all
        self._execute = execute
        self._fetch_one = fetch_one
        self._fetch_all = fetch_all
        self._transaction_factory = transaction_factory

    @classmethod
    def from_connection(cls, connection: Any) -> MissionRepository:
        return cls(
            execute=connection.execute,
            fetch_one=connection.fetchrow,
            fetch_all=connection.fetch,
        )

    @asynccontextmanager
    async def transaction(self):
        transaction_factory = self._transaction_factory
        if transaction_factory is None:
            from app.db.session import atransaction

            transaction_factory = atransaction
        async with transaction_factory() as connection:
            yield self.from_connection(connection)

    async def add_contract(self, contract: MissionContract) -> None:
        await self._execute(
            """INSERT INTO mission_contracts(id, version, document)
               VALUES($1, $2, $3::jsonb)""",
            contract.id,
            contract.version,
            _encode_json(contract.to_public_dict()),
        )

    async def get_contract(self, contract_id: str) -> MissionContract | None:
        row = await self._fetch_one(
            "SELECT document FROM mission_contracts WHERE id=$1",
            contract_id,
        )
        if row is None:
            return None
        return MissionContract.model_validate(
            _decode_json_object(row["document"], "document")
        )

    async def add_mission(self, mission: Mission) -> None:
        await self._execute(
            """INSERT INTO missions(
                   id, workspace_id, title, objective, source, contract_id,
                   status, plan_version, created_by, created_at, updated_at
               ) VALUES(
                   $1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9::jsonb, $10, $11
               )""",
            mission.id,
            mission.workspace_id,
            mission.title,
            mission.objective,
            _encode_json(mission.source.to_public_dict()),
            mission.contract_id,
            mission.status.value,
            mission.plan_version,
            _encode_json(mission.created_by.to_public_dict()),
            mission.created_at,
            mission.updated_at,
        )

    async def get_mission(self, mission_id: str) -> Mission | None:
        row = await self._fetch_one(
            """SELECT id, workspace_id, title, objective, source, contract_id,
                      status, plan_version, created_by, created_at, updated_at
               FROM missions WHERE id=$1""",
            mission_id,
        )
        return self._mission_from_row(row) if row is not None else None

    async def append_event(self, event: EventEnvelope) -> None:
        await self._execute(
            """INSERT INTO mission_events(
                   event_id, aggregate_type, aggregate_id, sequence, event_type,
                   actor, occurred_at, correlation_id, causation_id, payload,
                   schema_version
               ) VALUES(
                   $1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10::jsonb, $11
               )""",
            event.event_id,
            event.aggregate_type.value,
            event.aggregate_id,
            event.sequence,
            event.event_type,
            _encode_json(event.actor.to_public_dict()),
            event.occurred_at,
            event.correlation_id,
            event.causation_id,
            _encode_json(event.payload),
            event.schema_version,
        )

    async def list_missions(
        self,
        workspace_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Mission]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if offset < 0:
            raise ValueError("offset cannot be negative")
        rows = await self._fetch_all(
            """SELECT id, workspace_id, title, objective, source, contract_id,
                      status, plan_version, created_by, created_at, updated_at
               FROM missions
               WHERE workspace_id=$1
               ORDER BY updated_at DESC, id ASC
               LIMIT $2 OFFSET $3""",
            workspace_id,
            limit,
            offset,
        )
        return [self._mission_from_row(row) for row in rows]

    @staticmethod
    def _mission_from_row(row: Mapping[str, Any]) -> Mission:
        values = dict(row)
        values["source"] = _decode_json_object(values["source"], "source")
        values["created_by"] = _decode_json_object(values["created_by"], "created_by")
        return Mission.model_validate(values)
