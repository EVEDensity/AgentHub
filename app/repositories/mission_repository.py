from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from app.domain import (
    Artifact,
    Decision,
    DecisionStatus,
    EvaluationPolicyReason,
    EventEnvelope,
    Evidence,
    ExecutionCheckpoint,
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

    async def get_contract(
        self,
        contract_id: str,
        contract_version: int,
    ) -> MissionContract | None:
        row = await self._fetch_one(
            "SELECT document FROM mission_contracts WHERE id=$1 AND version=$2",
            contract_id,
            contract_version,
        )
        if row is None:
            return None
        return MissionContract.model_validate(
            _decode_json_object(row["document"], "document")
        )

    async def lock_contract_lineage(self, contract_id: str) -> None:
        try:
            await self._fetch_one(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                contract_id,
            )
        except Exception as error:
            # SQLite has no advisory-lock functions; its single serialized
            # connection already orders concurrent contract creation, so the
            # lineage lock degrades to a no-op there. Anything else still
            # fails loudly.
            if "hashtextextended" not in str(error):
                raise
            await self._fetch_one("SELECT 1")

    async def add_contract_lineage(
        self,
        contract_id: str,
        workspace_id: str,
    ) -> None:
        await self._execute(
            """INSERT INTO mission_contract_lineages(contract_id, workspace_id)
               VALUES($1, $2)""",
            contract_id,
            workspace_id,
        )

    async def get_contract_lineage_workspace(
        self,
        contract_id: str,
    ) -> str | None:
        row = await self._fetch_one(
            """SELECT workspace_id
               FROM mission_contract_lineages
               WHERE contract_id=$1""",
            contract_id,
        )
        return str(row["workspace_id"]) if row is not None else None

    async def get_latest_contract(self, contract_id: str) -> MissionContract | None:
        row = await self._fetch_one(
            """SELECT document
               FROM mission_contracts
               WHERE id=$1
               ORDER BY version DESC
               LIMIT 1""",
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
                   contract_version,
                   status, plan_version, created_by, created_at, updated_at
               ) VALUES(
                   $1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10::jsonb, $11, $12
               )""",
            mission.id,
            mission.workspace_id,
            mission.title,
            mission.objective,
            _encode_json(mission.source.to_public_dict()),
            mission.contract_id,
            mission.contract_version,
            mission.status.value,
            mission.plan_version,
            _encode_json(mission.created_by.to_public_dict()),
            mission.created_at,
            mission.updated_at,
        )

    async def get_mission(self, mission_id: str) -> Mission | None:
        row = await self._fetch_one(
            """SELECT id, workspace_id, title, objective, source, contract_id,
                      contract_version, status, plan_version, created_by,
                      created_at, updated_at
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
        source_reference: str | None = None,
    ) -> Mission | None:
        reference_filter = (
            "" if source_reference is None else " AND source->>'reference'=$4"
        )
        row = await self._fetch_one(
            f"""SELECT id, workspace_id, title, objective, source, contract_id,
                      contract_version, status, plan_version, created_by,
                      created_at, updated_at
               FROM missions
               WHERE workspace_id=$1
                 AND source->>'type'=$2
                 AND source->>'externalId'=$3{reference_filter}""",
            workspace_id,
            source_type,
            external_id,
            *(() if source_reference is None else (source_reference,)),
        )
        return self._mission_from_row(row) if row is not None else None

    async def get_mission_for_update(self, mission_id: str) -> Mission | None:
        row = await self._fetch_one(
            """SELECT id, workspace_id, title, objective, source, contract_id,
                      contract_version, status, plan_version, created_by,
                      created_at, updated_at
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
        statement = """INSERT INTO mission_events(
                       event_id, aggregate_type, aggregate_id, sequence, event_type,
                       actor, occurred_at, correlation_id, causation_id, payload,
                       schema_version
                   ) VALUES(
                       $1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10::jsonb, $11
                   )"""
        candidate = event
        # The local SQLite profile serializes individual statements, not the
        # whole read-max+insert sequence allocation. Concurrent heartbeats can
        # therefore race on the same next sequence. Retry only that known
        # conflict with the newly observed maximum; all other integrity errors
        # remain fail-fast.
        for _ in range(4):
            try:
                await self._execute(
                    statement,
                    candidate.event_id,
                    candidate.aggregate_type.value,
                    candidate.aggregate_id,
                    candidate.sequence,
                    candidate.event_type,
                    _encode_json(candidate.actor.to_public_dict()),
                    candidate.occurred_at,
                    candidate.correlation_id,
                    candidate.causation_id,
                    _encode_json(candidate.payload),
                    candidate.schema_version,
                )
                return
            except Exception as exc:  # noqa: BLE001 - backend-specific integrity errors
                detail = str(exc).lower()
                sequence_conflict = (
                    "mission_events" in detail
                    and "sequence" in detail
                    and ("unique constraint failed" in detail or "duplicate key" in detail)
                )
                if not sequence_conflict:
                    raise
                next_sequence = await self.get_last_event_sequence(
                    candidate.aggregate_id,
                    aggregate_type=candidate.aggregate_type.value,
                ) + 1
                candidate = candidate.model_copy(update={"sequence": next_sequence})
        raise RuntimeError(
            "could not allocate a unique mission event sequence after retries"
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

    async def list_work_unit_events(
        self,
        mission_id: str,
        *,
        limit: int = 100,
    ) -> list[EventEnvelope]:
        """Latest work-unit events correlated with a mission, oldest first.

        Harness checkpoint and work-unit lifecycle events are stored on the
        ``work_unit`` aggregate (``correlation_id`` = mission id), so the
        desktop execution feed reads them through this companion query to
        :meth:`list_events`.
        """
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        rows = await self._fetch_all(
            """SELECT event_id, aggregate_type, aggregate_id, sequence, event_type,
                      actor, occurred_at, correlation_id, causation_id, payload,
                      schema_version
               FROM mission_events
               WHERE aggregate_type='work_unit' AND correlation_id=$1
               ORDER BY occurred_at DESC, sequence DESC, event_id DESC
               LIMIT $2""",
            mission_id,
            limit,
        )
        return [self._event_from_row(row) for row in reversed(rows)]

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

    async def add_decision(self, decision: Decision) -> None:
        await self._execute(
            """INSERT INTO decisions(
                   id, mission_id, work_unit_id, attempt, context_digest,
                   reason_code, criterion_ids, options, recommended_option,
                   risk_summary, status, version, requested_by, requested_at,
                   expires_at, resolution, rationale, resolved_by, resolved_at
               ) VALUES(
                   $1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9, $10,
                   $11, $12, $13::jsonb, $14, $15, $16, $17, $18::jsonb, $19
               )""",
            decision.id,
            decision.mission_id,
            decision.work_unit_id,
            decision.attempt,
            decision.context_digest,
            decision.reason_code.value,
            _encode_json(list(decision.criterion_ids)),
            _encode_json([option.value for option in decision.options]),
            decision.recommended_option.value,
            decision.risk_summary,
            decision.status.value,
            decision.version,
            _encode_json(decision.requested_by.to_public_dict()),
            decision.requested_at,
            decision.expires_at,
            decision.resolution.value if decision.resolution is not None else None,
            decision.rationale,
            (
                _encode_json(decision.resolved_by.to_public_dict())
                if decision.resolved_by is not None
                else None
            ),
            decision.resolved_at,
        )

    async def get_decision(self, decision_id: str) -> Decision | None:
        row = await self._fetch_one(
            """SELECT id, mission_id, work_unit_id, attempt, context_digest,
                      reason_code, criterion_ids, options, recommended_option,
                      risk_summary, status, version, requested_by, requested_at,
                      expires_at, resolution, rationale, resolved_by, resolved_at
               FROM decisions WHERE id=$1""",
            decision_id,
        )
        return self._decision_from_row(row) if row is not None else None

    async def get_decision_for_update(self, decision_id: str) -> Decision | None:
        row = await self._fetch_one(
            """SELECT id, mission_id, work_unit_id, attempt, context_digest,
                      reason_code, criterion_ids, options, recommended_option,
                      risk_summary, status, version, requested_by, requested_at,
                      expires_at, resolution, rationale, resolved_by, resolved_at
               FROM decisions WHERE id=$1
               FOR UPDATE""",
            decision_id,
        )
        return self._decision_from_row(row) if row is not None else None

    async def list_decisions(
        self,
        mission_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Decision]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if offset < 0:
            raise ValueError("offset cannot be negative")
        rows = await self._fetch_all(
            """SELECT id, mission_id, work_unit_id, attempt, context_digest,
                      reason_code, criterion_ids, options, recommended_option,
                      risk_summary, status, version, requested_by, requested_at,
                      expires_at, resolution, rationale, resolved_by, resolved_at
               FROM decisions
               WHERE mission_id=$1
               ORDER BY requested_at ASC, id ASC
               LIMIT $2 OFFSET $3""",
            mission_id,
            limit,
            offset,
        )
        return [self._decision_from_row(row) for row in rows]

    async def list_workspace_decisions(
        self,
        workspace_id: str,
        *,
        status: DecisionStatus | None = DecisionStatus.PENDING,
        mission_id: str | None = None,
        reason_code: EvaluationPolicyReason | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Decision]:
        if not workspace_id:
            raise ValueError("workspace_id must be non-empty")
        if mission_id == "":
            raise ValueError("mission_id must be non-empty when provided")
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if offset < 0:
            raise ValueError("offset cannot be negative")
        rows = await self._fetch_all(
            """SELECT decision.id, decision.mission_id, decision.work_unit_id,
                      decision.attempt, decision.context_digest,
                      decision.reason_code, decision.criterion_ids,
                      decision.options, decision.recommended_option,
                      decision.risk_summary, decision.status, decision.version,
                      decision.requested_by, decision.requested_at,
                      decision.expires_at, decision.resolution,
                      decision.rationale, decision.resolved_by,
                      decision.resolved_at
               FROM decisions AS decision
               JOIN missions AS mission ON mission.id=decision.mission_id
               WHERE mission.workspace_id=$1
                 AND ($2::text IS NULL OR decision.status=$2)
                 AND ($3::text IS NULL OR decision.mission_id=$3)
                 AND ($4::text IS NULL OR decision.reason_code=$4)
               ORDER BY decision.requested_at ASC, decision.id ASC
               LIMIT $5 OFFSET $6""",
            workspace_id,
            status.value if status is not None else None,
            mission_id,
            reason_code.value if reason_code is not None else None,
            limit,
            offset,
        )
        return [self._decision_from_row(row) for row in rows]

    async def get_expired_decision_candidate_for_update(
        self,
        occurred_at: datetime,
    ) -> tuple[Mission, Decision] | None:
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        row = await self._fetch_one(
            """SELECT
                      mission.id AS selected_mission_id,
                      mission.workspace_id AS selected_workspace_id,
                      mission.title AS selected_title,
                      mission.objective AS selected_objective,
                      mission.source AS selected_source,
                      mission.contract_id AS selected_contract_id,
                      mission.contract_version AS selected_contract_version,
                      mission.status AS selected_status,
                      mission.plan_version AS selected_plan_version,
                      mission.created_by AS selected_created_by,
                      mission.created_at AS selected_created_at,
                      mission.updated_at AS selected_updated_at,
                      decision.id, decision.mission_id, decision.work_unit_id,
                      decision.attempt, decision.context_digest,
                      decision.reason_code, decision.criterion_ids,
                      decision.options, decision.recommended_option,
                      decision.risk_summary, decision.status, decision.version,
                      decision.requested_by, decision.requested_at,
                      decision.expires_at, decision.resolution,
                      decision.rationale, decision.resolved_by,
                      decision.resolved_at
               FROM decisions AS decision
               JOIN missions AS mission ON mission.id=decision.mission_id
               WHERE decision.status='PENDING'
                 AND decision.expires_at IS NOT NULL
                 AND decision.expires_at <= $1
                 AND mission.status='WAITING_DECISION'
               ORDER BY decision.expires_at ASC, decision.id ASC
               LIMIT 1
               FOR UPDATE OF mission, decision SKIP LOCKED""",
            occurred_at,
        )
        if row is None:
            return None
        return self._mission_from_claim_row(row), self._decision_from_row(row)

    async def list_pending_decisions_for_update(
        self,
        mission_id: str,
    ) -> list[Decision]:
        rows = await self._fetch_all(
            """SELECT id, mission_id, work_unit_id, attempt, context_digest,
                      reason_code, criterion_ids, options, recommended_option,
                      risk_summary, status, version, requested_by, requested_at,
                      expires_at, resolution, rationale, resolved_by, resolved_at
               FROM decisions
               WHERE mission_id=$1 AND status='PENDING'
               ORDER BY requested_at ASC, id ASC
               FOR UPDATE""",
            mission_id,
        )
        return [self._decision_from_row(row) for row in rows]

    async def update_decision(self, decision: Decision) -> None:
        await self._execute(
            """UPDATE decisions
               SET status=$2, version=$3, resolution=$4, rationale=$5,
                   resolved_by=$6::jsonb, resolved_at=$7
               WHERE id=$1""",
            decision.id,
            decision.status.value,
            decision.version,
            decision.resolution.value if decision.resolution is not None else None,
            decision.rationale,
            (
                _encode_json(decision.resolved_by.to_public_dict())
                if decision.resolved_by is not None
                else None
            ),
            decision.resolved_at,
        )

    async def add_artifact(self, artifact: Artifact) -> None:
        await self._execute(
            """INSERT INTO mission_artifacts(
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

    async def add_execution_checkpoint(
        self,
        checkpoint: ExecutionCheckpoint,
    ) -> None:
        await self._execute(
            """INSERT INTO execution_checkpoints(
                   id, mission_id, work_unit_id, attempt, sequence, phase,
                   iteration, tool_calls, prompt_tokens, completion_tokens,
                   model_cost, terminal, failure_reason, state_digest,
                   created_by, created_at
               ) VALUES(
                   $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                   $13, $14, $15::jsonb, $16
               )""",
            checkpoint.id,
            checkpoint.mission_id,
            checkpoint.work_unit_id,
            checkpoint.attempt,
            checkpoint.sequence,
            checkpoint.phase.value,
            checkpoint.iteration,
            checkpoint.tool_calls,
            checkpoint.prompt_tokens,
            checkpoint.completion_tokens,
            checkpoint.model_cost,
            checkpoint.terminal,
            checkpoint.failure_reason,
            checkpoint.state_digest,
            _encode_json(checkpoint.created_by.to_public_dict()),
            checkpoint.created_at,
        )

    async def get_execution_checkpoint(
        self,
        checkpoint_id: str,
    ) -> ExecutionCheckpoint | None:
        row = await self._fetch_one(
            """SELECT id, mission_id, work_unit_id, attempt, sequence, phase,
                      iteration, tool_calls, prompt_tokens, completion_tokens,
                      model_cost, terminal, failure_reason, state_digest,
                      created_by, created_at
               FROM execution_checkpoints WHERE id=$1""",
            checkpoint_id,
        )
        return self._execution_checkpoint_from_row(row) if row is not None else None

    async def get_latest_execution_checkpoint(
        self,
        work_unit_id: str,
        attempt: int,
    ) -> ExecutionCheckpoint | None:
        row = await self._fetch_one(
            """SELECT id, mission_id, work_unit_id, attempt, sequence, phase,
                      iteration, tool_calls, prompt_tokens, completion_tokens,
                      model_cost, terminal, failure_reason, state_digest,
                      created_by, created_at
               FROM execution_checkpoints
               WHERE work_unit_id=$1 AND attempt=$2
               ORDER BY sequence DESC LIMIT 1""",
            work_unit_id,
            attempt,
        )
        return self._execution_checkpoint_from_row(row) if row is not None else None

    async def get_artifact(self, artifact_id: str) -> Artifact | None:
        row = await self._fetch_one(
            """SELECT id, mission_id, work_unit_id, attempt, kind, digest,
                      content_address, media_type, size_bytes, source_repository,
                      base_commit, retention, sensitivity, created_by, created_at
               FROM mission_artifacts WHERE id=$1""",
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
               FROM mission_artifacts
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
                      contract_version, status, plan_version, created_by,
                      created_at, updated_at
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
                   id, mission_id, parent_work_unit_id, assigned_agent_id, kind,
                   dependencies, input_refs, expected_outputs, required_capabilities,
                   assigned_adapter, status, attempt, lease
               ) VALUES(
                   $1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8::jsonb,
                   $9::jsonb, $10, $11, $12, $13::jsonb
               )""",
            work_unit.id,
            work_unit.mission_id,
            work_unit.parent_work_unit_id,
            work_unit.assigned_agent_id,
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
            """SELECT id, mission_id, parent_work_unit_id, assigned_agent_id, kind,
                      dependencies, input_refs,
                      expected_outputs, required_capabilities, assigned_adapter,
                      status, attempt, lease
               FROM work_units WHERE id=$1""",
            work_unit_id,
        )
        return self._work_unit_from_row(row) if row is not None else None

    async def get_work_unit_for_update(self, work_unit_id: str) -> WorkUnit | None:
        row = await self._fetch_one(
            """SELECT id, mission_id, parent_work_unit_id, assigned_agent_id, kind,
                      dependencies, input_refs,
                      expected_outputs, required_capabilities, assigned_adapter,
                      status, attempt, lease
               FROM work_units WHERE id=$1
               FOR UPDATE""",
            work_unit_id,
        )
        return self._work_unit_from_row(row) if row is not None else None

    def _is_sqlite_connection(self) -> bool:
        """Return True when this repository is bound to the SQLite adapter.

        Claim-path repositories are always transaction-scoped
        (``MissionRepository.from_connection``), so the dialect follows the
        actual connection instead of process-wide configuration. Non-bound
        callables (module-level session functions, test doubles) default to
        the PostgreSQL dialect.
        """
        connection = getattr(self._fetch_one, "__self__", None)
        if connection is None:
            return False
        from app.db.sqlite_pool import SQLiteConnection

        return isinstance(connection, SQLiteConnection)

    @staticmethod
    def _sqlite_lease_expires_at(row: Mapping[str, Any]) -> datetime | None:
        """Parse a candidate row's lease ``expiresAt`` without SQL time functions."""
        lease = row.get("lease")
        if lease is None:
            return None
        if isinstance(lease, str):
            try:
                lease = json.loads(lease)
            except json.JSONDecodeError:
                return None
        if not isinstance(lease, Mapping):
            return None
        expires_at = lease.get("expiresAt")
        if not isinstance(expires_at, str) or not expires_at:
            return None
        try:
            parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _first_claimable_row(
        self,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Skip candidates holding a stale (expired) lease, return the first."""
        now = datetime.now(timezone.utc)
        for row in rows:
            expires_at = self._sqlite_lease_expires_at(row)
            if expires_at is not None and expires_at <= now:
                continue
            return row
        return None

    async def get_bound_work_unit_for_claim(
        self,
        mission_id: str,
        *,
        agent_id: str,
        adapter_type: str,
        allowed_root_kind: str | None,
    ) -> WorkUnit | None:
        """Lock one ready, explicitly bound WorkUnit for a Runner."""
        if self._is_sqlite_connection():
            row = await self._first_claimable_row(
                await self._fetch_all(
                    """SELECT candidate.id, candidate.mission_id,
                              candidate.parent_work_unit_id, candidate.assigned_agent_id,
                              candidate.kind, candidate.dependencies, candidate.input_refs,
                              candidate.expected_outputs, candidate.required_capabilities,
                              candidate.assigned_adapter, candidate.status,
                              candidate.attempt, candidate.lease
                       FROM work_units AS candidate
                       WHERE candidate.mission_id=$1
                         AND (
                             candidate.parent_work_unit_id IS NOT NULL
                             OR (
                                 $4 IS NOT NULL
                                 AND candidate.parent_work_unit_id IS NULL
                                 AND candidate.kind = $4
                                 AND (
                                     ($4 = 'a2a.inbound'
                                      AND candidate.assigned_adapter <> 'a2a.outbound')
                                     OR ($4 = 'a2a.delegate'
                                         AND candidate.assigned_adapter = 'a2a.outbound')
                                     OR ($4 = 'mission.fork'
                                         AND candidate.assigned_adapter <> 'a2a.outbound')
                                 )
                             )
                         )
                         AND candidate.assigned_agent_id=$2
                         AND candidate.assigned_adapter=$3
                         AND candidate.status IN ('PENDING', 'RETRYING')
                         AND NOT EXISTS (
                             SELECT 1
                             FROM json_each(candidate.dependencies) AS dep
                             LEFT JOIN work_units AS dependency_unit
                               ON dependency_unit.id = dep.value
                             WHERE dependency_unit.id IS NULL
                                OR dependency_unit.mission_id <> candidate.mission_id
                                OR dependency_unit.status <> 'SUCCEEDED'
                         )
                       ORDER BY candidate.id ASC
                       LIMIT 32""",
                    mission_id,
                    agent_id,
                    adapter_type,
                    allowed_root_kind,
                )
            )
            return self._work_unit_from_row(row) if row is not None else None
        row = await self._fetch_one(
            """SELECT candidate.id, candidate.mission_id,
                      candidate.parent_work_unit_id, candidate.assigned_agent_id,
                      candidate.kind, candidate.dependencies, candidate.input_refs,
                      candidate.expected_outputs, candidate.required_capabilities,
                      candidate.assigned_adapter, candidate.status,
                      candidate.attempt, candidate.lease
               FROM work_units AS candidate
               WHERE candidate.mission_id=$1
                 AND (
                     candidate.parent_work_unit_id IS NOT NULL
                     OR (
                         $4::text IS NOT NULL
                         AND candidate.parent_work_unit_id IS NULL
                         AND candidate.kind = $4
                         AND (
                             ($4 = 'a2a.inbound'
                              AND candidate.assigned_adapter <> 'a2a.outbound')
                             OR ($4 = 'a2a.delegate'
                                 AND candidate.assigned_adapter = 'a2a.outbound')
                             OR ($4 = 'mission.fork'
                                 AND candidate.assigned_adapter <> 'a2a.outbound')
                         )
                     )
                 )
                 AND candidate.assigned_agent_id=$2
                 AND candidate.assigned_adapter=$3
                 AND candidate.status IN ('PENDING', 'RETRYING')
                 AND NOT EXISTS (
                     SELECT 1
                     FROM jsonb_array_elements_text(candidate.dependencies) AS dep(id)
                     LEFT JOIN work_units AS dependency_unit
                       ON dependency_unit.id=dep.id
                     WHERE dependency_unit.id IS NULL
                        OR dependency_unit.mission_id <> candidate.mission_id
                        OR dependency_unit.status <> 'SUCCEEDED'
               )
               ORDER BY candidate.id ASC
               LIMIT 1
               FOR UPDATE SKIP LOCKED""",
            mission_id,
            agent_id,
            adapter_type,
            allowed_root_kind,
        )
        return self._work_unit_from_row(row) if row is not None else None

    async def get_workspace_bound_work_unit_for_claim(
        self,
        workspace_id: str,
        *,
        agent_id: str,
        adapter_type: str,
        supported_work_unit_kinds: tuple[str, ...],
    ) -> tuple[Mission, WorkUnit] | None:
        """Lock one fairly ordered ready unit and its owning Mission."""

        if self._is_sqlite_connection():
            kinds = list(supported_work_unit_kinds)
            kind_placeholders = ", ".join(
                f"${4 + index}" for index in range(len(kinds))
            )
            row = self._first_claimable_row(
                await self._fetch_all(
                    f"""SELECT
                              mission.id AS selected_mission_id,
                              mission.workspace_id AS selected_workspace_id,
                              mission.title AS selected_title,
                              mission.objective AS selected_objective,
                              mission.source AS selected_source,
                              mission.contract_id AS selected_contract_id,
                              mission.contract_version AS selected_contract_version,
                              mission.status AS selected_status,
                              mission.plan_version AS selected_plan_version,
                              mission.created_by AS selected_created_by,
                              mission.created_at AS selected_created_at,
                              mission.updated_at AS selected_updated_at,
                              candidate.id, candidate.mission_id,
                              candidate.parent_work_unit_id, candidate.assigned_agent_id,
                              candidate.kind, candidate.dependencies, candidate.input_refs,
                              candidate.expected_outputs, candidate.required_capabilities,
                              candidate.assigned_adapter, candidate.status,
                              candidate.attempt, candidate.lease
                       FROM missions AS mission
                       JOIN work_units AS candidate
                         ON candidate.mission_id=mission.id
                       WHERE mission.workspace_id=$1
                         AND mission.status='RUNNING'
                         AND (
                             candidate.parent_work_unit_id IS NOT NULL
                             OR (
                                 mission.source->>'type' = 'a2a.inbound'
                                 AND candidate.parent_work_unit_id IS NULL
                                 AND candidate.kind = 'a2a.inbound'
                                 AND candidate.assigned_adapter <> 'a2a.outbound'
                             )
                             OR (
                                 mission.source->>'type' = 'a2a'
                                 AND candidate.parent_work_unit_id IS NULL
                                 AND candidate.kind = 'a2a.delegate'
                                 AND candidate.assigned_adapter = 'a2a.outbound'
                             )
                             OR (
                                 mission.source->>'type' = 'mission.fork'
                                 AND candidate.parent_work_unit_id IS NULL
                                 AND candidate.kind = 'mission.fork'
                                 AND candidate.assigned_adapter <> 'a2a.outbound'
                             )
                             OR (
                                 mission.source->>'type' = 'manual'
                                 AND candidate.parent_work_unit_id IS NULL
                                 AND candidate.kind = 'desktop.task'
                                 AND candidate.assigned_adapter <> 'a2a.outbound'
                             )
                         )
                         AND candidate.assigned_agent_id=$2
                         AND candidate.assigned_adapter=$3
                         AND candidate.kind IN ({kind_placeholders})
                         AND candidate.status IN ('PENDING', 'RETRYING')
                         AND NOT EXISTS (
                             SELECT 1
                             FROM json_each(candidate.dependencies) AS dep
                             LEFT JOIN work_units AS dependency_unit
                               ON dependency_unit.id = dep.value
                             WHERE dependency_unit.id IS NULL
                                OR dependency_unit.mission_id <> candidate.mission_id
                                OR dependency_unit.status <> 'SUCCEEDED'
                         )
                       ORDER BY (
                           SELECT COUNT(*)
                           FROM work_units AS active_unit
                           WHERE active_unit.mission_id=mission.id
                             AND active_unit.status IN ('LEASED', 'RUNNING')
                       ) ASC,
                       mission.created_at ASC,
                       mission.id ASC,
                       candidate.id ASC
                       LIMIT 32""",
                    workspace_id,
                    agent_id,
                    adapter_type,
                    *kinds,
                )
            )
            if row is None:
                return None
            return self._mission_from_claim_row(row), self._work_unit_from_row(row)
        row = await self._fetch_one(
            """SELECT
                      mission.id AS selected_mission_id,
                      mission.workspace_id AS selected_workspace_id,
                      mission.title AS selected_title,
                      mission.objective AS selected_objective,
                      mission.source AS selected_source,
                      mission.contract_id AS selected_contract_id,
                      mission.contract_version AS selected_contract_version,
                      mission.status AS selected_status,
                      mission.plan_version AS selected_plan_version,
                      mission.created_by AS selected_created_by,
                      mission.created_at AS selected_created_at,
                      mission.updated_at AS selected_updated_at,
                      candidate.id, candidate.mission_id,
                      candidate.parent_work_unit_id, candidate.assigned_agent_id,
                      candidate.kind, candidate.dependencies, candidate.input_refs,
                      candidate.expected_outputs, candidate.required_capabilities,
                      candidate.assigned_adapter, candidate.status,
                      candidate.attempt, candidate.lease
               FROM missions AS mission
               JOIN work_units AS candidate
                 ON candidate.mission_id=mission.id
               WHERE mission.workspace_id=$1
                 AND mission.status='RUNNING'
                 AND (
                     candidate.parent_work_unit_id IS NOT NULL
                     OR (
                         mission.source->>'type' = 'a2a.inbound'
                         AND candidate.parent_work_unit_id IS NULL
                         AND candidate.kind = 'a2a.inbound'
                         AND candidate.assigned_adapter <> 'a2a.outbound'
                     )
                     OR (
                         mission.source->>'type' = 'a2a'
                         AND candidate.parent_work_unit_id IS NULL
                         AND candidate.kind = 'a2a.delegate'
                         AND candidate.assigned_adapter = 'a2a.outbound'
                     )
                     OR (
                         mission.source->>'type' = 'mission.fork'
                         AND candidate.parent_work_unit_id IS NULL
                         AND candidate.kind = 'mission.fork'
                         AND candidate.assigned_adapter <> 'a2a.outbound'
                     )
                     OR (
                         mission.source->>'type' = 'manual'
                         AND candidate.parent_work_unit_id IS NULL
                         AND candidate.kind = 'desktop.task'
                         AND candidate.assigned_adapter <> 'a2a.outbound'
                     )
                 )
                 AND candidate.assigned_agent_id=$2
                 AND candidate.assigned_adapter=$3
                 AND candidate.kind = ANY($4::text[])
                 AND candidate.status IN ('PENDING', 'RETRYING')
                 AND NOT EXISTS (
                     SELECT 1
                     FROM jsonb_array_elements_text(candidate.dependencies) AS dep(id)
                     LEFT JOIN work_units AS dependency_unit
                       ON dependency_unit.id=dep.id
                     WHERE dependency_unit.id IS NULL
                        OR dependency_unit.mission_id <> candidate.mission_id
                        OR dependency_unit.status <> 'SUCCEEDED'
                 )
               ORDER BY (
                   SELECT COUNT(*)
                   FROM work_units AS active_unit
                   WHERE active_unit.mission_id=mission.id
                     AND active_unit.status IN ('LEASED', 'RUNNING', 'VERIFYING')
               ) ASC,
               mission.created_at ASC,
               mission.id ASC,
               candidate.id ASC
               LIMIT 1
               FOR UPDATE OF mission, candidate SKIP LOCKED""",
            workspace_id,
            agent_id,
            adapter_type,
            list(supported_work_unit_kinds),
        )
        if row is None:
            return None
        mission = self._mission_from_claim_row(row)
        return mission, self._work_unit_from_row(row)

    async def get_workspace_verification_candidate(
        self,
        workspace_id: str,
    ) -> tuple[Mission, WorkUnit] | None:
        """Lock one deterministic VERIFYING unit for a short context read."""

        row = await self._fetch_one(
            """SELECT
                      mission.id AS selected_mission_id,
                      mission.workspace_id AS selected_workspace_id,
                      mission.title AS selected_title,
                      mission.objective AS selected_objective,
                      mission.source AS selected_source,
                      mission.contract_id AS selected_contract_id,
                      mission.contract_version AS selected_contract_version,
                      mission.status AS selected_status,
                      mission.plan_version AS selected_plan_version,
                      mission.created_by AS selected_created_by,
                      mission.created_at AS selected_created_at,
                      mission.updated_at AS selected_updated_at,
                      candidate.id, candidate.mission_id,
                      candidate.parent_work_unit_id, candidate.assigned_agent_id,
                      candidate.kind, candidate.dependencies, candidate.input_refs,
                      candidate.expected_outputs, candidate.required_capabilities,
                      candidate.assigned_adapter, candidate.status,
                      candidate.attempt, candidate.lease
               FROM missions AS mission
               JOIN work_units AS candidate
                 ON candidate.mission_id=mission.id
               WHERE mission.workspace_id=$1
                 AND mission.status IN ('RUNNING', 'VERIFYING')
                 AND candidate.status='VERIFYING'
               ORDER BY (
                   SELECT MAX(verifier_evidence.generated_at)
                   FROM evidence AS verifier_evidence
                   WHERE verifier_evidence.work_unit_id=candidate.id
               ) ASC NULLS FIRST,
               mission.created_at ASC, mission.id ASC, candidate.id ASC
               LIMIT 1
               FOR UPDATE OF mission, candidate SKIP LOCKED""",
            workspace_id,
        )
        if row is None:
            return None
        return self._mission_from_claim_row(row), self._work_unit_from_row(row)

    async def lock_tenant_claim_admission(self, tenant_id: str) -> None:
        """Serialize bounded claim admission for one tenant transaction."""

        if self._is_sqlite_connection():
            # SQLite has no advisory-lock functions; its single serialized
            # connection already orders concurrent claim transactions, so the
            # admission lock degrades to a no-op there (same as contract
            # lineage locking). Anything else still fails loudly.
            await self._fetch_one("SELECT 1 AS locked")
            return
        await self._fetch_one(
            """SELECT pg_advisory_xact_lock(hashtextextended($1, 0)) AS locked""",
            f"mission-claim-admission:{tenant_id}",
        )

    async def count_tenant_active_runner_work_units(self, tenant_id: str) -> int:
        """Count non-expired Runner attempts against the tenant quota."""

        if self._is_sqlite_connection():
            # The local profile maps the tenant onto the workspace (see the
            # SQLite admission resolver); expired leases are excluded by the
            # application-level expiry checks, not by SQL time functions.
            row = await self._fetch_one(
                """SELECT COUNT(*) AS active_count
                   FROM work_units AS active_unit
                   JOIN missions AS mission ON mission.id = active_unit.mission_id
                   WHERE mission.workspace_id = $1
                     AND active_unit.status IN ('LEASED', 'RUNNING')
                     AND active_unit.lease IS NOT NULL""",
                tenant_id,
            )
        else:
            row = await self._fetch_one(
                """SELECT COUNT(*) AS active_count
                   FROM work_units AS active_unit
                   JOIN missions AS mission ON mission.id = active_unit.mission_id
                   JOIN platform_workspaces AS workspace
                     ON workspace.id = mission.workspace_id
                   WHERE workspace.tenant_id = $1
                     AND active_unit.status IN ('LEASED', 'RUNNING')
                     AND active_unit.lease IS NOT NULL
                     AND (active_unit.lease->>'expiresAt')::timestamptz
                         > CURRENT_TIMESTAMP""",
                tenant_id,
            )
        if row is None:
            raise RuntimeError("Tenant Runner concurrency query returned no row")
        active_count = row.get("active_count")
        if isinstance(active_count, bool) or not isinstance(active_count, int):
            raise TypeError("Tenant Runner concurrency count is invalid")
        if active_count < 0:
            raise ValueError("Tenant Runner concurrency count is invalid")
        return active_count

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

    async def list_work_unit_artifacts(
        self,
        mission_id: str,
        work_unit_id: str,
        attempt: int,
        *,
        limit: int = 201,
    ) -> list[Artifact]:
        if not 1 <= limit <= 201:
            raise ValueError("limit must be between 1 and 201")
        rows = await self._fetch_all(
            """SELECT id, mission_id, work_unit_id, attempt, kind, digest,
                      content_address, media_type, size_bytes, source_repository,
                      base_commit, retention, sensitivity, created_by, created_at
               FROM mission_artifacts
               WHERE mission_id=$1 AND work_unit_id=$2 AND attempt=$3
               ORDER BY created_at ASC, id ASC
               LIMIT $4""",
            mission_id,
            work_unit_id,
            attempt,
            limit,
        )
        return [self._artifact_from_row(row) for row in rows]

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
            """SELECT id, mission_id, parent_work_unit_id, assigned_agent_id, kind,
                      dependencies, input_refs,
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
            """SELECT id, mission_id, parent_work_unit_id, assigned_agent_id, kind,
                      dependencies, input_refs,
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
    def _execution_checkpoint_from_row(row: Mapping[str, Any]) -> ExecutionCheckpoint:
        return ExecutionCheckpoint.model_validate(
            {
                "id": row["id"],
                "mission_id": row["mission_id"],
                "work_unit_id": row["work_unit_id"],
                "attempt": row["attempt"],
                "sequence": row["sequence"],
                "phase": row["phase"],
                "iteration": row["iteration"],
                "tool_calls": row["tool_calls"],
                "prompt_tokens": row["prompt_tokens"],
                "completion_tokens": row["completion_tokens"],
                "model_cost": row["model_cost"],
                "terminal": row["terminal"],
                "failure_reason": row["failure_reason"],
                "state_digest": row["state_digest"],
                "created_by": _decode_json_object(row["created_by"], "created_by"),
                "created_at": row["created_at"],
            }
        )

    @staticmethod
    def _mission_from_row(row: Mapping[str, Any]) -> Mission:
        values = dict(row)
        values["source"] = _decode_json_object(values["source"], "source")
        values["created_by"] = _decode_json_object(values["created_by"], "created_by")
        return Mission.model_validate(values)

    @staticmethod
    def _mission_from_claim_row(row: Mapping[str, Any]) -> Mission:
        values = {
            field_name: row[f"selected_{field_name}"]
            for field_name in (
                "mission_id",
                "workspace_id",
                "title",
                "objective",
                "source",
                "contract_id",
                "contract_version",
                "status",
                "plan_version",
                "created_by",
                "created_at",
                "updated_at",
            )
        }
        values["id"] = values.pop("mission_id")
        values["source"] = _decode_json_object(values["source"], "selected_source")
        values["created_by"] = _decode_json_object(
            values["created_by"],
            "selected_created_by",
        )
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
    def _decision_from_row(row: Mapping[str, Any]) -> Decision:
        values = {
            field_name: row[field_name]
            for field_name in (
                "id",
                "mission_id",
                "work_unit_id",
                "attempt",
                "context_digest",
                "reason_code",
                "criterion_ids",
                "options",
                "recommended_option",
                "risk_summary",
                "status",
                "version",
                "requested_by",
                "requested_at",
                "expires_at",
                "resolution",
                "rationale",
                "resolved_by",
                "resolved_at",
            )
        }
        for field_name in ("criterion_ids", "options"):
            values[field_name] = _decode_json_array(values[field_name], field_name)
        values["requested_by"] = _decode_json_object(
            values["requested_by"], "requested_by"
        )
        if values["resolved_by"] is not None:
            values["resolved_by"] = _decode_json_object(
                values["resolved_by"], "resolved_by"
            )
        return Decision.model_validate(values)

    @staticmethod
    def _artifact_from_row(row: Mapping[str, Any]) -> Artifact:
        values = dict(row)
        values["created_by"] = _decode_json_object(values["created_by"], "created_by")
        return Artifact.model_validate(values)

    @staticmethod
    def _work_unit_from_row(row: Mapping[str, Any]) -> WorkUnit:
        values = {
            field_name: row[field_name]
            for field_name in (
                "id",
                "mission_id",
                "parent_work_unit_id",
                "assigned_agent_id",
                "kind",
                "dependencies",
                "input_refs",
                "expected_outputs",
                "required_capabilities",
                "assigned_adapter",
                "status",
                "attempt",
                "lease",
            )
        }
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
