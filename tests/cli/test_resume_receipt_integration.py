from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from app.cli import runtime
from app.services.tools.receipts import SQLiteToolReceiptStore, ToolReceipt, ToolReceiptStatus


class _Client:
    def __init__(self) -> None:
        self.missions_created = 0

    def heartbeat_work_unit(self, *args, **kwargs):
        del args, kwargs
        return {"lease": {"id": "lease-1", "attempt": 1, "runnerId": "local-admin", "expiresAt": "2999-01-01T00:00:00+00:00"}}

    def decisions(self, mission_id: str):
        del mission_id
        return []

    def execution_context(self, *args, **kwargs):
        del args, kwargs
        return {"executionContext": {"checkpoint": self.checkpoint}}

    def create_and_start_mission(self, **kwargs):
        del kwargs
        self.missions_created += 1
        raise AssertionError("resume must not create a Mission")


class ResumeReceiptIntegrationTests(unittest.TestCase):
    def _gate(self, decision: str) -> dict[str, object]:
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
                "idempotencyKey": "mis-1/wu-1/1/file_write/abc",
            },
            "receiptDecision": decision,
        }

    def test_legacy_receipt_cannot_invent_a_complete_resume_image(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = SQLiteToolReceiptStore(Path(temp_dir) / "receipts.sqlite3")
            key = "mis-1/wu-1/1/file_write/abc"
            store.mark_unknown(key, "file_write", 1.0, error_type="process_crash")
            client = _Client()
            client.checkpoint = self._gate("unknown_outcome")["checkpoint"]
            with mock.patch.object(runtime, "resume_work_unit", return_value=self._gate("unknown_outcome")):
                refused = runtime.prepare_resume_execution(client, "mis-1", Path(temp_dir), receipt_store=store)
            self.assertFalse(refused.can_resume)
            self.assertIn("legacy", str(refused.refusal_reason))
            self.assertIsNone(refused.resume_input)

            store.put(ToolReceipt(key, "file_write", ToolReceiptStatus.SUCCEEDED, 2.0, result_digest="digest"))
            client.checkpoint = self._gate("already_succeeded")["checkpoint"]
            with mock.patch.object(runtime, "resume_work_unit", return_value=self._gate("already_succeeded")):
                resumed = runtime.prepare_resume_execution(client, "mis-1", Path(temp_dir), receipt_store=store)
            self.assertFalse(resumed.can_resume)
            self.assertIn("legacy", str(resumed.refusal_reason))
            self.assertIsNone(resumed.resume_input)
            self.assertEqual(client.missions_created, 0)
