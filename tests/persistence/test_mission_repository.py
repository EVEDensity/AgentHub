from __future__ import annotations

import json
import unittest
from typing import Any

from app.domain import (
    Artifact,
    Decision,
    EventEnvelope,
    Evidence,
    Lease,
    Mission,
    WorkUnit,
)
from app.repositories import MissionRepository
from tests.domain.factories import (
    build_artifact,
    build_contract,
    build_decision,
    build_event,
    build_evidence,
    build_mission,
    build_work_unit,
)


class FakeDatabase:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.fetched_one: list[tuple[str, tuple[Any, ...]]] = []
        self.fetched_all: list[tuple[str, tuple[Any, ...]]] = []
        self.one: dict[str, Any] | None = None
        self.all: list[dict[str, Any]] = []

    async def execute(self, sql: str, *args: Any) -> None:
        self.executed.append((sql, args))

    async def fetch_one(self, sql: str, *args: Any) -> dict[str, Any] | None:
        self.fetched_one.append((sql, args))
        return self.one

    async def fetch_all(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.fetched_all.append((sql, args))
        return self.all


class MissionRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.database = FakeDatabase()
        self.repository = MissionRepository(
            execute=self.database.execute,
            fetch_one=self.database.fetch_one,
            fetch_all=self.database.fetch_all,
        )

    async def test_contract_round_trip_accepts_json_text(self) -> None:
        contract = build_contract()
        await self.repository.add_contract(contract)

        _sql, args = self.database.executed[-1]
        self.assertEqual(args[0:2], (contract.id, contract.version))
        self.assertEqual(json.loads(args[2]), contract.to_public_dict())

        self.database.one = {"document": args[2]}
        restored = await self.repository.get_contract(contract.id)
        self.assertEqual(restored, contract)

    async def test_mission_round_trip_accepts_decoded_json(self) -> None:
        mission = build_mission()
        await self.repository.add_mission(mission)

        _sql, args = self.database.executed[-1]
        self.assertEqual(args[0], mission.id)
        self.assertEqual(args[6], mission.status.value)
        self.database.one = self.build_mission_row(mission)
        restored = await self.repository.get_mission(mission.id)
        self.assertEqual(restored, mission)

    async def test_missing_records_return_none(self) -> None:
        self.assertIsNone(await self.repository.get_contract("missing"))
        self.assertIsNone(await self.repository.get_mission("missing"))
        self.assertIsNone(await self.repository.get_evidence("missing"))
        self.assertIsNone(await self.repository.get_decision("missing"))

    async def test_mission_source_lookup_is_workspace_and_protocol_scoped(self) -> None:
        mission = build_mission(
            source={
                "type": "a2a",
                "reference": "https://agent.example.test",
                "externalId": "task-1",
            }
        )
        self.database.one = self.build_mission_row(mission)

        restored = await self.repository.get_mission_by_source(
            "workspace-1",
            source_type="a2a",
            external_id="task-1",
        )

        self.assertEqual(restored, mission)
        sql, args = self.database.fetched_one[-1]
        self.assertIn("workspace_id=$1", sql)
        self.assertIn("source->>'type'=$2", sql)
        self.assertIn("source->>'externalId'=$3", sql)
        self.assertEqual(args, ("workspace-1", "a2a", "task-1"))

    async def test_mission_source_lookup_can_bind_source_reference(self) -> None:
        mission = build_mission(
            source={
                "type": "a2a.inbound",
                "reference": "https://sender.example.test",
                "externalId": "task-1",
            }
        )
        self.database.one = self.build_mission_row(mission)

        restored = await self.repository.get_mission_by_source(
            "workspace-1",
            source_type="a2a.inbound",
            external_id="task-1",
            source_reference="https://sender.example.test",
        )

        self.assertEqual(restored, mission)
        sql, args = self.database.fetched_one[-1]
        self.assertIn("source->>'reference'=$4", sql)
        self.assertEqual(
            args,
            (
                "workspace-1",
                "a2a.inbound",
                "task-1",
                "https://sender.example.test",
            ),
        )

    async def test_list_is_workspace_scoped_and_bounded(self) -> None:
        mission = build_mission()
        self.database.all = [self.build_mission_row(mission)]

        restored = await self.repository.list_missions(
            "workspace-1", limit=20, offset=5
        )

        self.assertEqual(restored, [mission])
        with self.assertRaises(ValueError):
            await self.repository.list_missions("workspace-1", limit=0)
        with self.assertRaises(ValueError):
            await self.repository.list_missions("workspace-1", offset=-1)

    async def test_append_event_persists_public_envelope_fields(self) -> None:
        event = build_event()

        await self.repository.append_event(event)

        sql, args = self.database.executed[-1]
        self.assertIn("INSERT INTO mission_events", sql)
        self.assertEqual(args[0], event.event_id)
        self.assertEqual(args[1], "mission")
        self.assertEqual(args[3], 1)
        self.assertEqual(json.loads(args[5]), event.actor.to_public_dict())
        self.assertEqual(json.loads(args[9]), event.payload)
        self.assertEqual(args[10], 1)

    async def test_lock_and_update_mission_snapshot(self) -> None:
        mission = build_mission(status="RUNNING")
        self.database.one = self.build_mission_row(mission)

        locked = await self.repository.get_mission_for_update(mission.id)
        await self.repository.update_mission(mission)

        self.assertEqual(locked, mission)
        lock_sql, lock_args = self.database.fetched_one[-1]
        self.assertIn("FOR UPDATE", lock_sql)
        self.assertEqual(lock_args, (mission.id,))
        update_sql, update_args = self.database.executed[-1]
        self.assertIn("UPDATE missions", update_sql)
        self.assertEqual(
            update_args,
            (mission.id, "RUNNING", mission.plan_version, mission.updated_at),
        )

    async def test_last_sequence_and_ordered_event_reads(self) -> None:
        self.database.one = {"sequence": 2}

        sequence = await self.repository.get_last_event_sequence("mis-1")

        self.assertEqual(sequence, 2)
        self.database.all = [
            self.build_event_row(build_event(sequence=2, event_id="evt-2")),
            self.build_event_row(build_event(sequence=3, event_id="evt-3")),
        ]
        events = await self.repository.list_events(
            "mis-1",
            after_sequence=1,
            limit=20,
        )

        self.assertEqual([event.sequence for event in events], [2, 3])
        list_sql, list_args = self.database.fetched_all[-1]
        self.assertIn("ORDER BY sequence ASC", list_sql)
        self.assertEqual(list_args, ("mis-1", 1, 20))
        with self.assertRaises(ValueError):
            await self.repository.list_events("mis-1", after_sequence=-1)
        with self.assertRaises(ValueError):
            await self.repository.list_events("mis-1", limit=201)

    async def test_evidence_round_trip_and_mission_list(self) -> None:
        evidence = build_evidence()

        await self.repository.add_evidence(evidence)

        insert_sql, insert_args = self.database.executed[-1]
        self.assertIn("INSERT INTO evidence", insert_sql)
        self.assertEqual(
            insert_args[0:4],
            (
                evidence.id,
                evidence.mission_id,
                evidence.work_unit_id,
                evidence.criterion_id,
            ),
        )
        self.assertEqual(json.loads(insert_args[4]), evidence.verifier.to_public_dict())
        self.assertEqual(insert_args[5], "PASS")
        self.assertEqual(
            json.loads(insert_args[6]),
            [item.to_public_dict() for item in evidence.artifact_refs],
        )

        row = self.build_evidence_row(evidence)
        self.database.one = row
        restored = await self.repository.get_evidence(evidence.id)
        self.assertEqual(restored, evidence)

        self.database.all = [row]
        listed = await self.repository.list_evidence(
            evidence.mission_id,
            limit=20,
            offset=5,
        )
        self.assertEqual(listed, [evidence])
        sql, args = self.database.fetched_all[-1]
        self.assertIn("FROM evidence", sql)
        self.assertIn("ORDER BY generated_at ASC, id ASC", sql)
        self.assertEqual(args, (evidence.mission_id, 20, 5))
        with self.assertRaises(ValueError):
            await self.repository.list_evidence("mis-1", limit=0)
        with self.assertRaises(ValueError):
            await self.repository.list_evidence("mis-1", offset=-1)

    async def test_passed_evidence_criteria_are_not_page_limited(self) -> None:
        self.database.all = [
            {"criterion_id": "tests"},
            {"criterion_id": "security"},
        ]

        restored = await self.repository.list_passed_evidence_criterion_ids("mis-1")

        self.assertEqual(restored, {"tests", "security"})
        sql, args = self.database.fetched_all[-1]
        self.assertIn("SELECT DISTINCT criterion_id", sql)
        self.assertIn("verdict='PASS'", sql)
        self.assertNotIn("LIMIT", sql)
        self.assertEqual(args, ("mis-1",))

    async def test_decision_round_trip_list_lock_and_resolution_update(self) -> None:
        decision = build_decision()

        await self.repository.add_decision(decision)

        insert_sql, insert_args = self.database.executed[-1]
        self.assertIn("INSERT INTO decisions", insert_sql)
        self.assertEqual(insert_args[0:6], (
            decision.id,
            decision.mission_id,
            decision.work_unit_id,
            decision.attempt,
            decision.context_digest,
            decision.reason_code,
        ))
        self.assertEqual(json.loads(insert_args[6]), ["tests"])
        self.assertEqual(
            json.loads(insert_args[7]),
            ["RETRY_WORK_UNIT", "FAIL_MISSION"],
        )

        row = self.build_decision_row(decision)
        self.database.one = row
        self.assertEqual(await self.repository.get_decision(decision.id), decision)
        self.assertEqual(
            await self.repository.get_decision_for_update(decision.id), decision
        )
        lock_sql, lock_args = self.database.fetched_one[-1]
        self.assertIn("FOR UPDATE", lock_sql)
        self.assertEqual(lock_args, (decision.id,))

        self.database.all = [row]
        self.assertEqual(
            await self.repository.list_decisions("mis-1", limit=20, offset=5),
            [decision],
        )
        list_sql, list_args = self.database.fetched_all[-1]
        self.assertIn("WHERE mission_id=$1", list_sql)
        self.assertIn("ORDER BY requested_at ASC, id ASC", list_sql)
        self.assertEqual(list_args, ("mis-1", 20, 5))
        self.database.all = [row]
        self.assertEqual(
            await self.repository.list_pending_decisions_for_update("mis-1"),
            [decision],
        )
        pending_sql, pending_args = self.database.fetched_all[-1]
        self.assertIn("status='PENDING'", pending_sql)
        self.assertIn("FOR UPDATE", pending_sql)
        self.assertEqual(pending_args, ("mis-1",))

        resolved = build_decision(
            status="RESOLVED",
            version=2,
            resolution="FAIL_MISSION",
            rationale="The Contract cannot currently be verified.",
            resolved_by={"type": "human", "id": "user-1"},
            resolved_at=decision.requested_at,
        )
        await self.repository.update_decision(resolved)
        update_sql, update_args = self.database.executed[-1]
        self.assertIn("UPDATE decisions", update_sql)
        self.assertEqual(update_args[0:4], (
            decision.id,
            "RESOLVED",
            2,
            "FAIL_MISSION",
        ))
        with self.assertRaises(ValueError):
            await self.repository.list_decisions("mis-1", limit=0)
        with self.assertRaises(ValueError):
            await self.repository.list_decisions("mis-1", offset=-1)

    async def test_artifact_round_trip_and_mission_list(self) -> None:
        artifact = build_artifact()

        await self.repository.add_artifact(artifact)

        insert_sql, insert_args = self.database.executed[-1]
        self.assertIn("INSERT INTO artifacts", insert_sql)
        self.assertEqual(
            insert_args[0:6],
            (
                artifact.id,
                artifact.mission_id,
                artifact.work_unit_id,
                artifact.attempt,
                artifact.kind.value,
                artifact.digest,
            ),
        )
        self.assertEqual(
            json.loads(insert_args[13]), artifact.created_by.to_public_dict()
        )

        row = self.build_artifact_row(artifact)
        self.database.one = row
        restored = await self.repository.get_artifact(artifact.id)
        self.assertEqual(restored, artifact)

        self.database.all = [row]
        listed = await self.repository.list_artifacts(
            artifact.mission_id,
            limit=20,
            offset=5,
        )
        self.assertEqual(listed, [artifact])
        list_sql, list_args = self.database.fetched_all[-1]
        self.assertIn("WHERE mission_id=$1", list_sql)
        self.assertIn("ORDER BY created_at ASC, id ASC", list_sql)
        self.assertEqual(list_args, (artifact.mission_id, 20, 5))
        with self.assertRaises(ValueError):
            await self.repository.list_artifacts(artifact.mission_id, limit=0)
        with self.assertRaises(ValueError):
            await self.repository.list_artifacts(artifact.mission_id, offset=-1)

    async def test_work_unit_round_trip_and_mission_list(self) -> None:
        work_unit = build_work_unit(parent_work_unit_id="wu-parent")

        await self.repository.add_work_unit(work_unit)

        insert_sql, insert_args = self.database.executed[-1]
        self.assertIn("INSERT INTO work_units", insert_sql)
        self.assertEqual(
            insert_args[0:4],
            (
                work_unit.id,
                work_unit.mission_id,
                work_unit.parent_work_unit_id,
                work_unit.assigned_agent_id,
            ),
        )
        self.assertEqual(insert_args[4], work_unit.kind)
        self.assertEqual(json.loads(insert_args[5]), list(work_unit.dependencies))
        self.assertEqual(
            json.loads(insert_args[7]),
            [item.to_public_dict() for item in work_unit.expected_outputs],
        )
        self.assertEqual(insert_args[10:12], ("PENDING", 0))
        self.assertIsNone(insert_args[12])

        row = self.build_work_unit_row(work_unit)
        self.database.one = row
        restored = await self.repository.get_work_unit(work_unit.id)
        self.assertEqual(restored, work_unit)

        self.database.all = [row]
        listed = await self.repository.list_work_units(
            work_unit.mission_id,
            limit=20,
            offset=5,
        )
        self.assertEqual(listed, [work_unit])
        list_sql, list_args = self.database.fetched_all[-1]
        self.assertIn("WHERE mission_id=$1", list_sql)
        self.assertIn("ORDER BY id ASC", list_sql)
        self.assertEqual(list_args, (work_unit.mission_id, 20, 5))
        with self.assertRaises(ValueError):
            await self.repository.list_work_units(work_unit.mission_id, limit=0)

    async def test_work_unit_lock_and_update_lease_snapshot(self) -> None:
        work_unit = build_work_unit(
            status="LEASED",
            lease=Lease(
                id="lease-1",
                runner_id="runner-1",
                expires_at=build_mission().updated_at,
            ),
        )
        self.database.one = self.build_work_unit_row(work_unit)

        locked = await self.repository.get_work_unit_for_update(work_unit.id)
        await self.repository.update_work_unit(work_unit)

        self.assertEqual(locked, work_unit)
        lock_sql, lock_args = self.database.fetched_one[-1]
        self.assertIn("FOR UPDATE", lock_sql)
        self.assertEqual(lock_args, (work_unit.id,))
        update_sql, update_args = self.database.executed[-1]
        self.assertIn("UPDATE work_units", update_sql)
        self.assertEqual(update_args[:3], (work_unit.id, "LEASED", 0))
        self.assertEqual(json.loads(update_args[3]), work_unit.lease.to_public_dict())

    async def test_list_work_units_for_update_locks_entire_mission_set(self) -> None:
        work_unit = build_work_unit()
        self.database.all = [self.build_work_unit_row(work_unit)]

        restored = await self.repository.list_work_units_for_update(
            work_unit.mission_id
        )

        self.assertEqual(restored, [work_unit])
        sql, args = self.database.fetched_all[-1]
        self.assertIn("WHERE mission_id=$1", sql)
        self.assertIn("ORDER BY id ASC", sql)
        self.assertIn("FOR UPDATE", sql)
        self.assertEqual(args, (work_unit.mission_id,))

    async def test_bound_claim_whitelists_explicit_root_kind_and_uses_skip_locked(
        self,
    ) -> None:
        work_unit = build_work_unit(
            id="wu-child",
            parent_work_unit_id="wu-parent",
            assigned_agent_id="reviewer",
            assigned_adapter="local_codex",
        )
        self.database.one = self.build_work_unit_row(work_unit)

        claimed = await self.repository.get_bound_work_unit_for_claim(
            work_unit.mission_id,
            agent_id="reviewer",
            adapter_type="local_codex",
            allowed_root_kind="a2a.inbound",
        )

        self.assertEqual(claimed, work_unit)
        sql, args = self.database.fetched_one[-1]
        self.assertIn("parent_work_unit_id IS NOT NULL", sql)
        self.assertIn("$4::text IS NOT NULL", sql)
        self.assertIn("candidate.kind = $4", sql)
        self.assertIn("$4 = 'a2a.inbound'", sql)
        self.assertIn("candidate.assigned_adapter <> 'a2a.outbound'", sql)
        self.assertIn("$4 = 'a2a.delegate'", sql)
        self.assertIn("candidate.assigned_adapter = 'a2a.outbound'", sql)
        self.assertIn("assigned_agent_id=$2", sql)
        self.assertIn("assigned_adapter=$3", sql)
        self.assertIn("FOR UPDATE SKIP LOCKED", sql)
        self.assertEqual(
            args,
            (
                work_unit.mission_id,
                "reviewer",
                "local_codex",
                "a2a.inbound",
            ),
        )

    async def test_workspace_claim_is_scoped_fair_and_locks_both_rows(self) -> None:
        mission = build_mission(workspace_id="workspace-1", status="RUNNING")
        work_unit = build_work_unit(
            id="wu-child",
            mission_id=mission.id,
            parent_work_unit_id="wu-parent",
            assigned_agent_id="reviewer",
            assigned_adapter="local_codex",
        )
        row = self.build_work_unit_row(work_unit)
        row.update(
            {
                f"selected_{'mission_id' if name == 'id' else name}": value
                for name, value in self.build_mission_row(mission).items()
            }
        )
        self.database.one = row

        selection = await self.repository.get_workspace_bound_work_unit_for_claim(
            "workspace-1",
            agent_id="reviewer",
            adapter_type="local_codex",
        )

        self.assertEqual(selection, (mission, work_unit))
        sql, args = self.database.fetched_one[-1]
        self.assertIn("mission.workspace_id=$1", sql)
        self.assertIn("mission.status='RUNNING'", sql)
        self.assertIn("mission.source->>'type' = 'a2a.inbound'", sql)
        self.assertIn("mission.source->>'type' = 'a2a'", sql)
        self.assertIn("candidate.kind = 'a2a.delegate'", sql)
        self.assertIn("candidate.assigned_adapter = 'a2a.outbound'", sql)
        self.assertIn("candidate.assigned_agent_id=$2", sql)
        self.assertIn("candidate.assigned_adapter=$3", sql)
        self.assertIn("active_unit.status IN ('LEASED', 'RUNNING', 'VERIFYING')", sql)
        self.assertIn("mission.created_at ASC", sql)
        self.assertIn("FOR UPDATE OF mission, candidate SKIP LOCKED", sql)
        self.assertEqual(args, ("workspace-1", "reviewer", "local_codex"))

    async def test_verification_candidate_is_scoped_ordered_and_short_locked(
        self,
    ) -> None:
        mission = build_mission(workspace_id="workspace-1", status="RUNNING")
        work_unit = build_work_unit(
            mission_id=mission.id,
            status="VERIFYING",
            attempt=2,
        )
        row = self.build_work_unit_row(work_unit)
        row.update(
            {
                f"selected_{'mission_id' if name == 'id' else name}": value
                for name, value in self.build_mission_row(mission).items()
            }
        )
        self.database.one = row

        selection = await self.repository.get_workspace_verification_candidate(
            "workspace-1"
        )

        self.assertEqual(selection, (mission, work_unit))
        sql, args = self.database.fetched_one[-1]
        self.assertIn("mission.workspace_id=$1", sql)
        self.assertIn("mission.status IN ('RUNNING', 'VERIFYING')", sql)
        self.assertIn("candidate.status='VERIFYING'", sql)
        self.assertIn("MAX(verifier_evidence.generated_at)", sql)
        self.assertIn("verifier_evidence.work_unit_id=candidate.id", sql)
        self.assertIn("ASC NULLS FIRST", sql)
        self.assertIn(
            "mission.created_at ASC, mission.id ASC, candidate.id ASC",
            sql,
        )
        self.assertIn("FOR UPDATE OF mission, candidate SKIP LOCKED", sql)
        self.assertEqual(args, ("workspace-1",))

    async def test_work_unit_artifact_read_is_attempt_scoped_and_bounded(
        self,
    ) -> None:
        artifact = build_artifact(attempt=2)
        self.database.all = [self.build_artifact_row(artifact)]

        restored = await self.repository.list_work_unit_artifacts(
            artifact.mission_id,
            artifact.work_unit_id,
            artifact.attempt,
            limit=201,
        )

        self.assertEqual(restored, [artifact])
        sql, args = self.database.fetched_all[-1]
        self.assertIn(
            "WHERE mission_id=$1 AND work_unit_id=$2 AND attempt=$3",
            sql,
        )
        self.assertIn("ORDER BY created_at ASC, id ASC", sql)
        self.assertIn("LIMIT $4", sql)
        self.assertEqual(
            args,
            (artifact.mission_id, artifact.work_unit_id, artifact.attempt, 201),
        )
        with self.assertRaises(ValueError):
            await self.repository.list_work_unit_artifacts(
                artifact.mission_id,
                artifact.work_unit_id,
                artifact.attempt,
                limit=202,
            )

    async def test_tenant_claim_admission_uses_transaction_advisory_lock(self) -> None:
        await self.repository.lock_tenant_claim_admission("tenant-1")

        sql, args = self.database.fetched_one[-1]
        self.assertIn("pg_advisory_xact_lock", sql)
        self.assertIn("hashtextextended($1, 0)", sql)
        self.assertEqual(args, ("mission-claim-admission:tenant-1",))

    async def test_tenant_active_runner_count_excludes_expired_and_verifying(
        self,
    ) -> None:
        self.database.one = {"active_count": 3}

        active_count = await self.repository.count_tenant_active_runner_work_units(
            "tenant-1"
        )

        self.assertEqual(active_count, 3)
        sql, args = self.database.fetched_one[-1]
        self.assertIn("JOIN platform_workspaces AS workspace", sql)
        self.assertIn("workspace.tenant_id = $1", sql)
        self.assertIn("active_unit.status IN ('LEASED', 'RUNNING')", sql)
        self.assertIn("active_unit.lease->>'expiresAt'", sql)
        self.assertIn("> CURRENT_TIMESTAMP", sql)
        self.assertNotIn("VERIFYING", sql)
        self.assertEqual(args, ("tenant-1",))

    @staticmethod
    def build_mission_row(mission: Mission) -> dict[str, Any]:
        return {
            "id": mission.id,
            "workspace_id": mission.workspace_id,
            "title": mission.title,
            "objective": mission.objective,
            "source": mission.source.to_public_dict(),
            "contract_id": mission.contract_id,
            "status": mission.status.value,
            "plan_version": mission.plan_version,
            "created_by": mission.created_by.to_public_dict(),
            "created_at": mission.created_at,
            "updated_at": mission.updated_at,
        }

    @staticmethod
    def build_event_row(event: EventEnvelope) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "aggregate_type": event.aggregate_type.value,
            "aggregate_id": event.aggregate_id,
            "sequence": event.sequence,
            "event_type": event.event_type,
            "actor": json.dumps(event.actor.to_public_dict()),
            "occurred_at": event.occurred_at,
            "correlation_id": event.correlation_id,
            "causation_id": event.causation_id,
            "payload": json.dumps(dict(event.payload)),
            "schema_version": event.schema_version,
        }

    @staticmethod
    def build_artifact_row(artifact: Artifact) -> dict[str, Any]:
        return {
            "id": artifact.id,
            "mission_id": artifact.mission_id,
            "work_unit_id": artifact.work_unit_id,
            "attempt": artifact.attempt,
            "kind": artifact.kind.value,
            "digest": artifact.digest,
            "content_address": artifact.content_address,
            "media_type": artifact.media_type,
            "size_bytes": artifact.size_bytes,
            "source_repository": artifact.source_repository,
            "base_commit": artifact.base_commit,
            "retention": artifact.retention.value,
            "sensitivity": artifact.sensitivity.value,
            "created_by": json.dumps(artifact.created_by.to_public_dict()),
            "created_at": artifact.created_at,
        }

    @staticmethod
    def build_evidence_row(evidence: Evidence) -> dict[str, Any]:
        return {
            "id": evidence.id,
            "mission_id": evidence.mission_id,
            "work_unit_id": evidence.work_unit_id,
            "criterion_id": evidence.criterion_id,
            "verifier": json.dumps(evidence.verifier.to_public_dict()),
            "verdict": evidence.verdict.value,
            "artifact_refs": json.dumps(
                [item.to_public_dict() for item in evidence.artifact_refs]
            ),
            "summary": evidence.summary,
            "generated_at": evidence.generated_at,
            "integrity_hash": evidence.integrity_hash,
        }

    @staticmethod
    def build_decision_row(decision: Decision) -> dict[str, Any]:
        return {
            "id": decision.id,
            "mission_id": decision.mission_id,
            "work_unit_id": decision.work_unit_id,
            "attempt": decision.attempt,
            "context_digest": decision.context_digest,
            "reason_code": decision.reason_code,
            "criterion_ids": json.dumps(list(decision.criterion_ids)),
            "options": json.dumps([option.value for option in decision.options]),
            "recommended_option": decision.recommended_option.value,
            "risk_summary": decision.risk_summary,
            "status": decision.status.value,
            "version": decision.version,
            "requested_by": json.dumps(decision.requested_by.to_public_dict()),
            "requested_at": decision.requested_at,
            "expires_at": decision.expires_at,
            "resolution": (
                decision.resolution.value if decision.resolution is not None else None
            ),
            "rationale": decision.rationale,
            "resolved_by": (
                json.dumps(decision.resolved_by.to_public_dict())
                if decision.resolved_by is not None
                else None
            ),
            "resolved_at": decision.resolved_at,
        }

    @staticmethod
    def build_work_unit_row(work_unit: WorkUnit) -> dict[str, Any]:
        return {
            "id": work_unit.id,
            "mission_id": work_unit.mission_id,
            "parent_work_unit_id": work_unit.parent_work_unit_id,
            "assigned_agent_id": work_unit.assigned_agent_id,
            "kind": work_unit.kind,
            "dependencies": json.dumps(list(work_unit.dependencies)),
            "input_refs": json.dumps(
                [item.to_public_dict() for item in work_unit.input_refs]
            ),
            "expected_outputs": json.dumps(
                [item.to_public_dict() for item in work_unit.expected_outputs]
            ),
            "required_capabilities": json.dumps(list(work_unit.required_capabilities)),
            "assigned_adapter": work_unit.assigned_adapter,
            "status": work_unit.status.value,
            "attempt": work_unit.attempt,
            "lease": (
                json.dumps(work_unit.lease.to_public_dict())
                if work_unit.lease is not None
                else None
            ),
        }


if __name__ == "__main__":
    unittest.main()
