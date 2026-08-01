from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.domain import (
    InvalidStateTransition,
    Lease,
    MissionStatus,
    WorkUnitStatus,
    transition_mission,
    transition_work_unit,
)
from test_models import NOW, build_mission, build_work_unit


class MissionTransitionTests(unittest.TestCase):
    def test_valid_transition_returns_new_mission(self) -> None:
        mission = build_mission(status="READY")
        transitioned = transition_mission(
            mission,
            MissionStatus.RUNNING,
            occurred_at=NOW + timedelta(seconds=1),
        )
        self.assertEqual(mission.status, MissionStatus.READY)
        self.assertEqual(transitioned.status, MissionStatus.RUNNING)
        self.assertGreater(transitioned.updated_at, mission.updated_at)

    def test_terminal_and_skipped_transitions_are_rejected(self) -> None:
        with self.assertRaisesRegex(InvalidStateTransition, "READY -> SUCCEEDED"):
            transition_mission(
                build_mission(status="READY"),
                MissionStatus.SUCCEEDED,
                occurred_at=NOW + timedelta(seconds=1),
            )
        with self.assertRaises(InvalidStateTransition):
            transition_mission(
                build_mission(status="SUCCEEDED"),
                MissionStatus.RUNNING,
                occurred_at=NOW + timedelta(seconds=1),
            )

    def test_transition_time_cannot_move_backwards(self) -> None:
        with self.assertRaisesRegex(ValueError, "transition time"):
            transition_mission(
                build_mission(status="READY"),
                MissionStatus.RUNNING,
                occurred_at=NOW - timedelta(seconds=1),
            )


class WorkUnitTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lease = Lease(
            id="lease-1",
            runner_id="runner-1",
            expires_at=datetime(2026, 8, 1, 1, tzinfo=timezone.utc),
        )

    def test_lease_starts_a_new_attempt(self) -> None:
        leased = transition_work_unit(
            build_work_unit(), WorkUnitStatus.LEASED, lease=self.lease
        )
        self.assertEqual(leased.status, WorkUnitStatus.LEASED)
        self.assertEqual(leased.attempt, 1)
        self.assertEqual(leased.lease, self.lease)

        running = transition_work_unit(leased, WorkUnitStatus.RUNNING)
        self.assertEqual(running.attempt, 1)
        self.assertEqual(running.lease, self.lease)

    def test_retry_releases_lease_and_next_lease_increments_attempt(self) -> None:
        leased = transition_work_unit(
            build_work_unit(), WorkUnitStatus.LEASED, lease=self.lease
        )
        retrying = transition_work_unit(leased, WorkUnitStatus.RETRYING)
        self.assertIsNone(retrying.lease)

        next_lease = self.lease.model_copy(update={"id": "lease-2"})
        leased_again = transition_work_unit(
            retrying, WorkUnitStatus.LEASED, lease=next_lease
        )
        self.assertEqual(leased_again.attempt, 2)
        self.assertEqual(leased_again.lease.id, "lease-2")

    def test_leasing_requires_lease_and_terminal_states_do_not_restart(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires a new lease"):
            transition_work_unit(build_work_unit(), WorkUnitStatus.LEASED)
        with self.assertRaises(InvalidStateTransition):
            transition_work_unit(
                build_work_unit(status="SUCCEEDED"), WorkUnitStatus.LEASED, lease=self.lease
            )


if __name__ == "__main__":
    unittest.main()
