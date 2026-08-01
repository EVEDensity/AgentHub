from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from app.compat import (
    LegacyTaskMappingError,
    LegacyTaskSnapshot,
    map_legacy_task_to_mission,
)
from app.domain import ActorRef, MissionStatus


class LegacyTaskMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.actor = ActorRef(type="human", id="user-1")

    def build_task(self, **updates: object) -> LegacyTaskSnapshot:
        values: dict[str, object] = {
            "id": "task-1",
            "session_id": "session-1",
            "status": "PENDING",
            "created_at": datetime(2026, 8, 1, 8),
            "updated_at": datetime(2026, 8, 1, 9),
        }
        values.update(updates)
        return LegacyTaskSnapshot.model_validate(values)

    def map_task(self, task: LegacyTaskSnapshot, **updates: object):
        values: dict[str, object] = {
            "workspace_id": "workspace-1",
            "title": "Fix issue 42",
            "objective": "Produce a tested pull request.",
            "contract_id": "contract-1",
            "created_by": self.actor,
            "legacy_timezone": timezone(timedelta(hours=8)),
        }
        values.update(updates)
        return map_legacy_task_to_mission(task, **values)

    def test_statuses_map_without_claiming_unverified_success(self) -> None:
        expected = {
            "PENDING": MissionStatus.READY,
            "RUNNING": MissionStatus.RUNNING,
            "SUCCESS": MissionStatus.VERIFYING,
            "FAILED": MissionStatus.FAILED,
        }
        for legacy_status, mission_status in expected.items():
            with self.subTest(status=legacy_status):
                mission = self.map_task(self.build_task(status=legacy_status))
                self.assertEqual(mission.status, mission_status)

    def test_mapping_preserves_identity_provenance_and_normalizes_time(self) -> None:
        mission = self.map_task(self.build_task())

        self.assertEqual(mission.id, "task-1")
        self.assertEqual(mission.workspace_id, "workspace-1")
        self.assertEqual(mission.source.type.value, "import")
        self.assertEqual(mission.source.reference, "legacy-session:session-1")
        self.assertEqual(mission.source.external_id, "task-1")
        self.assertEqual(mission.created_at, datetime(2026, 8, 1, tzinfo=timezone.utc))
        self.assertEqual(
            mission.updated_at, datetime(2026, 8, 1, 1, tzinfo=timezone.utc)
        )

    def test_naive_timestamps_require_an_explicit_source_timezone(self) -> None:
        with self.assertRaisesRegex(LegacyTaskMappingError, "legacy_timezone"):
            self.map_task(self.build_task(), legacy_timezone=None)

    def test_mapping_rejects_reversed_timestamps(self) -> None:
        with self.assertRaisesRegex(LegacyTaskMappingError, "cannot be earlier"):
            self.map_task(
                self.build_task(
                    created_at=datetime(2026, 8, 1, 9),
                    updated_at=datetime(2026, 8, 1, 8),
                )
            )

    def test_boundary_rejects_dag_json_and_unknown_statuses(self) -> None:
        with self.assertRaises(ValidationError):
            self.build_task(dag_progress={"nodes": []})
        with self.assertRaises(ValidationError):
            self.build_task(status="CANCELLED")


if __name__ == "__main__":
    unittest.main()
