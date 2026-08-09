from __future__ import annotations

import json
import unittest
from typing import Any

from app.domain import Artifact, EventEnvelope, Evidence, Lease, Mission, WorkUnit
from app.repositories import MissionRepository
from tests.domain.factories import (
    build_artifact,
    build_contract,
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
            (work_unit.id, work_unit.mission_id, work_unit.parent_work_unit_id, work_unit.kind),
        )
        self.assertEqual(json.loads(insert_args[4]), list(work_unit.dependencies))
        self.assertEqual(
            json.loads(insert_args[6]),
            [item.to_public_dict() for item in work_unit.expected_outputs],
        )
        self.assertEqual(insert_args[9:11], ("PENDING", 0))
        self.assertIsNone(insert_args[11])

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
    def build_work_unit_row(work_unit: WorkUnit) -> dict[str, Any]:
        return {
            "id": work_unit.id,
            "mission_id": work_unit.mission_id,
            "parent_work_unit_id": work_unit.parent_work_unit_id,
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
