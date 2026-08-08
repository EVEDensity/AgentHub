from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any

from app.domain import (
    Artifact,
    EventEnvelope,
    Evidence,
    Mission,
    MissionContract,
    WorkUnit,
)

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


def _decode_json_array(value: object, field_name: str) -> list[Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise TypeError(f"database field {field_name} must contain a JSON array")
    return value


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

    async def get_mission_by_source(
        self,
        workspace_id: str,
        *,
        source_type: str,
        external_id: str,
    ) -> Mission | None:
        row = await self._fetch_one(
            """SELECT id, workspace_id, title, objective, source, contract_id,
                      status, plan_version, created_by, created_at, updated_at
               FROM missions
               WHERE workspace_id=$1
                 AND source->>'type'=$2
                 AND source->>'externalId'=$3""",
            workspace_id,
            source_type,
            external_id,
        )
        return self._mission_from_row(row) if row is not None else None

    async def get_mission_for_update(self, mission_id: str) -> Mission | None:
        row = await self._fetch_one(
            """SELECT id, workspace_id, title, objective, source, contract_id,
                      status, plan_version, created_by, created_at, updated_at
               FROM missions WHERE id=$1
               FOR UPDATE""",
            mission_id,
        )
        return self._mission_from_row(row) if row is not None else None

    async def update_mission(self, mission: Mission) -> None:
        await self._execute(
            """UPDATE missions
               SET status=$2, plan_version=$3, updated_at=$4
               WHERE id=$1""",
            mission.id,
            mission.status.value,
            mission.plan_version,
            mission.updated_at,
        )

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

    async def get_last_event_sequence(
        self,
        aggregate_id: str,
        *,
        aggregate_type: str = "mission",
    ) -> int:
        row = await self._fetch_one(
            """SELECT sequence
               FROM mission_events
               WHERE aggregate_type=$1 AND aggregate_id=$2
               ORDER BY sequence DESC
               LIMIT 1""",
            aggregate_type,
            aggregate_id,
        )
        return int(row["sequence"]) if row is not None else 0

    async def list_events(
        self,
        mission_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[EventEnvelope]:
        if after_sequence < 0:
            raise ValueError("after_sequence cannot be negative")
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        rows = await self._fetch_all(
            """SELECT event_id, aggregate_type, aggregate_id, sequence, event_type,
                      actor, occurred_at, correlation_id, causation_id, payload,
                      schema_version
               FROM mission_events
               WHERE aggregate_type='mission' AND aggregate_id=$1 AND sequence>$2
               ORDER BY sequence ASC
               LIMIT $3""",
            mission_id,
            after_sequence,
            limit,
        )
        return [self._event_from_row(row) for row in rows]

    async def add_evidence(self, evidence: Evidence) -> None:
        await self._execute(
            """INSERT INTO evidence(
                   id, mission_id, work_unit_id, criterion_id, verifier, verdict,
                   artifact_refs, summary, generated_at, integrity_hash
               ) VALUES($1, $2, $3, $4, $5::jsonb, $6, $7::jsonb, $8, $9, $10)""",
            evidence.id,
            evidence.mission_id,
            evidence.work_unit_id,
            evidence.criterion_id,
            _encode_json(evidence.verifier.to_public_dict()),
            evidence.verdict.value,
            _encode_json(
                [artifact_ref.to_public_dict() for artifact_ref in evidence.artifact_refs]
            ),
            evidence.summary,
            evidence.generated_at,
            evidence.integrity_hash,
        )

    async def get_evidence(self, evidence_id: str) -> Evidence | None:
        row = await self._fetch_one(
            """SELECT id, mission_id, work_unit_id, criterion_id, verifier, verdict,
                      artifact_refs, summary, generated_at, integrity_hash
               FROM evidence WHERE id=$1""",
            evidence_id,
        )
        return self._evidence_from_row(row) if row is not None else None

    async def list_evidence(
        self,
        mission_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Evidence]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if offset < 0:
            raise ValueError("offset cannot be negative")
        rows = await self._fetch_all(
            """SELECT id, mission_id, work_unit_id, criterion_id, verifier, verdict,
                      artifact_refs, summary, generated_at, integrity_hash
               FROM evidence
               WHERE mission_id=$1
               ORDER BY generated_at ASC, id ASC
               LIMIT $2 OFFSET $3""",
            mission_id,
            limit,
            offset,
        )
        return [self._evidence_from_row(row) for row in rows]

    async def list_passed_evidence_criterion_ids(self, mission_id: str) -> set[str]:
        rows = await self._fetch_all(
            """SELECT DISTINCT criterion_id
               FROM evidence
               WHERE mission_id=$1 AND verdict='PASS'""",
            mission_id,
        )
        return {str(row["criterion_id"]) for row in rows}

    async def add_artifact(self, artifact: Artifact) -> None:
        await self._execute(
            """INSERT INTO artifacts(
                   id, mission_id, work_unit_id, attempt, kind, digest,
                   content_address, media_type, size_bytes, source_repository,
                   base_commit, retention, sensitivity, created_by, created_at
               ) VALUES(
                   $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                   $14::jsonb, $15
               )""",
            artifact.id,
            artifact.mission_id,
            artifact.work_unit_id,
            artifact.attempt,
            artifact.kind.value,
            artifact.digest,
            artifact.content_address,
            artifact.media_type,
            artifact.size_bytes,
            artifact.source_repository,
            artifact.base_commit,
            artifact.retention.value,
            artifact.sensitivity.value,
            _encode_json(artifact.created_by.to_public_dict()),
            artifact.created_at,
        )

    async def get_artifact(self, artifact_id: str) -> Artifact | None:
        row = await self._fetch_one(
            """SELECT id, mission_id, work_unit_id, attempt, kind, digest,
                      content_address, media_type, size_bytes, source_repository,
                      base_commit, retention, sensitivity, created_by, created_at
               FROM artifacts WHERE id=$1""",
            artifact_id,
        )
        return self._artifact_from_row(row) if row is not None else None

    async def list_artifacts(
        self,
        mission_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Artifact]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if offset < 0:
            raise ValueError("offset cannot be negative")
        rows = await self._fetch_all(
            """SELECT id, mission_id, work_unit_id, attempt, kind, digest,
                      content_address, media_type, size_bytes, source_repository,
                      base_commit, retention, sensitivity, created_by, created_at
               FROM artifacts
               WHERE mission_id=$1
               ORDER BY created_at ASC, id ASC
               LIMIT $2 OFFSET $3""",
            mission_id,
            limit,
            offset,
        )
        return [self._artifact_from_row(row) for row in rows]

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

    async def add_work_unit(self, work_unit: WorkUnit) -> None:
        await self._execute(
            """INSERT INTO work_units(
                   id, mission_id, kind, dependencies, input_refs, expected_outputs,
                   required_capabilities, assigned_adapter, status, attempt, lease
               ) VALUES(
                   $1, $2, $3, $4::jsonb, $5::jsonb, $6::jsonb,
                   $7::jsonb, $8, $9, $10, $11::jsonb
               )""",
            work_unit.id,
            work_unit.mission_id,
            work_unit.kind,
            _encode_json(list(work_unit.dependencies)),
            _encode_json([item.to_public_dict() for item in work_unit.input_refs]),
            _encode_json(
                [item.to_public_dict() for item in work_unit.expected_outputs]
            ),
            _encode_json(list(work_unit.required_capabilities)),
            work_unit.assigned_adapter,
            work_unit.status.value,
            work_unit.attempt,
            _encode_json(work_unit.lease.to_public_dict())
            if work_unit.lease is not None
            else None,
        )

    async def get_work_unit(self, work_unit_id: str) -> WorkUnit | None:
        row = await self._fetch_one(
            """SELECT id, mission_id, kind, dependencies, input_refs,
                      expected_outputs, required_capabilities, assigned_adapter,
                      status, attempt, lease
               FROM work_units WHERE id=$1""",
            work_unit_id,
        )
        return self._work_unit_from_row(row) if row is not None else None

    async def get_work_unit_for_update(self, work_unit_id: str) -> WorkUnit | None:
        row = await self._fetch_one(
            """SELECT id, mission_id, kind, dependencies, input_refs,
                      expected_outputs, required_capabilities, assigned_adapter,
                      status, attempt, lease
               FROM work_units WHERE id=$1
               FOR UPDATE""",
            work_unit_id,
        )
        return self._work_unit_from_row(row) if row is not None else None

    async def update_work_unit(self, work_unit: WorkUnit) -> None:
        await self._execute(
            """UPDATE work_units
               SET status=$2, attempt=$3, lease=$4::jsonb
               WHERE id=$1""",
            work_unit.id,
            work_unit.status.value,
            work_unit.attempt,
            _encode_json(work_unit.lease.to_public_dict())
            if work_unit.lease is not None
            else None,
        )

    async def list_work_units(
        self,
        mission_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[WorkUnit]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if offset < 0:
            raise ValueError("offset cannot be negative")
        rows = await self._fetch_all(
            """SELECT id, mission_id, kind, dependencies, input_refs,
                      expected_outputs, required_capabilities, assigned_adapter,
                      status, attempt, lease
               FROM work_units
               WHERE mission_id=$1
               ORDER BY id ASC
               LIMIT $2 OFFSET $3""",
            mission_id,
            limit,
            offset,
        )
        return [self._work_unit_from_row(row) for row in rows]

    async def list_work_units_for_update(self, mission_id: str) -> list[WorkUnit]:
        rows = await self._fetch_all(
            """SELECT id, mission_id, kind, dependencies, input_refs,
                      expected_outputs, required_capabilities, assigned_adapter,
                      status, attempt, lease
               FROM work_units
               WHERE mission_id=$1
               ORDER BY id ASC
               FOR UPDATE""",
            mission_id,
        )
        return [self._work_unit_from_row(row) for row in rows]

    @staticmethod
    def _mission_from_row(row: Mapping[str, Any]) -> Mission:
        values = dict(row)
        values["source"] = _decode_json_object(values["source"], "source")
        values["created_by"] = _decode_json_object(values["created_by"], "created_by")
        return Mission.model_validate(values)

    @staticmethod
    def _event_from_row(row: Mapping[str, Any]) -> EventEnvelope:
        values = dict(row)
        values["actor"] = _decode_json_object(values["actor"], "actor")
        values["payload"] = _decode_json_object(values["payload"], "payload")
        return EventEnvelope.model_validate(values)

    @staticmethod
    def _evidence_from_row(row: Mapping[str, Any]) -> Evidence:
        values = dict(row)
        values["verifier"] = _decode_json_object(values["verifier"], "verifier")
        values["artifact_refs"] = _decode_json_array(
            values["artifact_refs"], "artifact_refs"
        )
        return Evidence.model_validate(values)

    @staticmethod
    def _artifact_from_row(row: Mapping[str, Any]) -> Artifact:
        values = dict(row)
        values["created_by"] = _decode_json_object(values["created_by"], "created_by")
        return Artifact.model_validate(values)

    @staticmethod
    def _work_unit_from_row(row: Mapping[str, Any]) -> WorkUnit:
        values = dict(row)
        for field_name in (
            "dependencies",
            "input_refs",
            "expected_outputs",
            "required_capabilities",
        ):
            values[field_name] = _decode_json_array(values[field_name], field_name)
        if values["lease"] is not None:
            values["lease"] = _decode_json_object(values["lease"], "lease")
        return WorkUnit.model_validate(values)
