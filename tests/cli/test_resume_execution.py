from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from app.cli import runtime


class _ResumeClient:
    def __init__(self, decisions: list[dict[str, object]], *, projected_checkpoint: dict[str, object] | None = None) -> None:
        self._decisions = decisions
        self._projected_checkpoint = projected_checkpoint
        self.heartbeats: list[str] = []

    def heartbeat_work_unit(self, mission_id: str, work_unit_id: str, *, lease_id: str, lease_seconds: int) -> dict[str, object]:
        del mission_id, work_unit_id, lease_seconds
        self.heartbeats.append(lease_id)
        return {"lease": {"id": lease_id, "attempt": 1}}

    def decisions(self, mission_id: str) -> list[dict[str, object]]:
        del mission_id
        return self._decisions

    def execution_context(self, mission_id: str, work_unit_id: str, *, lease_id: str) -> dict[str, object]:
        del mission_id, work_unit_id, lease_id
        return {"executionContext": {"checkpoint": self._projected_checkpoint}}


class ResumeExecutionPlanTests(unittest.TestCase):
    def _gate(self) -> dict[str, object]:
        return {
            "workUnits": [{
                "id": "wu-1",
                "status": "RUNNING",
                "attempt": 1,
                "lease": {"id": "lease-1", "expiresAt": "2999-01-01T00:00:00+00:00"},
            }],
            "checkpoint": {
                "id": "checkpoint-1",
                "workUnitId": "wu-1",
                "attempt": 1,
                "iteration": 2,
                "nextAction": {"toolName": "file_write", "callId": "call-1"},
                "idempotencyKey": "mis-1/wu-1/1/call-1",
            },
            "receiptDecision": "not_checked",
        }

    def test_only_exactly_related_pending_decision_is_restored(self) -> None:
        cases = [
            {"workUnitId": "wu-other", "attempt": 1, "callId": "call-1", "idempotencyKey": "mis-1/wu-1/1/call-1"},
            {"workUnitId": "wu-1", "attempt": 2, "callId": "call-1", "idempotencyKey": "mis-1/wu-1/1/call-1"},
            {"workUnitId": "wu-1", "attempt": 1, "callId": "call-other", "idempotencyKey": "mis-1/wu-1/1/call-1"},
            {"workUnitId": "wu-1", "attempt": 1, "callId": "call-1", "idempotencyKey": "mis-1/wu-1/1/call-other"},
        ]
        checkpoint = self._gate()["checkpoint"]
        for decision in cases:
            with self.subTest(decision=decision), mock.patch.object(runtime, "resume_work_unit", return_value=self._gate()):
                plan = runtime.prepare_resume_execution(
                    _ResumeClient([decision], projected_checkpoint=checkpoint),
                    "mis-1",
                    Path("."),
                )
            self.assertTrue(plan.can_resume)
            self.assertIsNone(plan.pending_decision)

    def test_multiple_matching_pending_decisions_are_refused(self) -> None:
        decision = {
            "workUnitId": "wu-1",
            "attempt": 1,
            "callId": "call-1",
            "idempotencyKey": "mis-1/wu-1/1/call-1",
        }
        with mock.patch.object(runtime, "resume_work_unit", return_value=self._gate()):
            plan = runtime.prepare_resume_execution(
                _ResumeClient([decision, dict(decision)], projected_checkpoint=self._gate()["checkpoint"]),
                "mis-1",
                Path("."),
            )
        self.assertFalse(plan.can_resume)
        self.assertEqual(plan.refusal_reason, "multiple pending decisions for work unit")

    def test_checkpoint_change_between_gate_and_execution_projection_is_refused(self) -> None:
        changed = dict(self._gate()["checkpoint"])
        changed["id"] = "checkpoint-new"
        with mock.patch.object(runtime, "resume_work_unit", return_value=self._gate()):
            plan = runtime.prepare_resume_execution(
                _ResumeClient([], projected_checkpoint=changed),
                "mis-1",
                Path("."),
            )
        self.assertFalse(plan.can_resume)
        self.assertIn("checkpoint changed", str(plan.refusal_reason))

    def test_local_runtime_cleanup_removes_only_agenthub_transients(self) -> None:
        root = Path(self.id().replace(".", "_"))
        root.mkdir(exist_ok=True)
        self.addCleanup(lambda: root.rmdir())
        self.addCleanup(lambda: (root / "keep.txt").unlink(missing_ok=True))
        (root / ".agenthub_exec").mkdir()
        (root / ".agenthub_exec" / "script.py").write_text("x", encoding="utf-8")
        (root / "keep.txt").write_text("keep", encoding="utf-8")
        runtime._cleanup_local_runtime_artifacts(root)
        self.assertFalse((root / ".agenthub_exec").exists())
        self.assertTrue((root / "keep.txt").exists())
