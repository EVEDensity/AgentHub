from __future__ import annotations

import asyncio
import unittest
from typing import Any

from app.services.verifier_service import VerifierPollResult, VerifierPollStatus
from app.services.verifier_worker import VerifierWorker


class SequenceVerifier:
    def __init__(
        self,
        outcomes: list[VerifierPollResult | Exception],
    ) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []
        self.called = asyncio.Event()

    async def discover_and_verify(self, workspace_id: str) -> VerifierPollResult:
        self.calls.append(workspace_id)
        outcome = self.outcomes.pop(0)
        if not self.outcomes:
            self.called.set()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class BlockingVerifier:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False
        self.calls = 0

    async def discover_and_verify(self, workspace_id: str) -> VerifierPollResult:
        del workspace_id
        self.calls += 1
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return VerifierPollResult(
            status=VerifierPollStatus.VERIFIED,
            evidence_id="evd-1",
        )


class InvalidStatusVerifier:
    def __init__(self) -> None:
        self.called = asyncio.Event()

    async def discover_and_verify(self, workspace_id: str) -> Any:
        del workspace_id
        self.called.set()
        return type("InvalidPollResult", (), {"status": "verified"})()


class VerifierWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_backs_off_and_resets_after_verification(self) -> None:
        verifier = SequenceVerifier(
            [
                VerifierPollResult(status=VerifierPollStatus.IDLE),
                RuntimeError("control unavailable"),
                VerifierPollResult(
                    status=VerifierPollStatus.VERIFIED,
                    evidence_id="evd-1",
                ),
            ]
        )
        worker = VerifierWorker(
            verifier,
            workspace_id="workspace-1",
            idle_delay_seconds=0.001,
            max_delay_seconds=0.004,
        )

        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(verifier.called.wait(), timeout=1)
        worker.request_stop()
        await asyncio.wait_for(task, timeout=1)

        snapshot = worker.snapshot
        self.assertFalse(snapshot.running)
        self.assertFalse(snapshot.ready)
        self.assertTrue(snapshot.stop_requested)
        self.assertEqual(snapshot.polls, 3)
        self.assertEqual(snapshot.verified, 1)
        self.assertEqual(snapshot.idle_polls, 1)
        self.assertEqual(snapshot.failed_polls, 1)
        self.assertEqual(snapshot.consecutive_failures, 0)
        self.assertEqual(snapshot.current_delay_seconds, 0.001)
        self.assertIsNone(snapshot.last_error_type)
        self.assertEqual(snapshot.last_poll_status, VerifierPollStatus.VERIFIED)
        self.assertEqual(verifier.calls, ["workspace-1"] * 3)

    async def test_stop_waits_for_active_evaluation_before_exiting(self) -> None:
        verifier = BlockingVerifier()
        worker = VerifierWorker(
            verifier,
            workspace_id="workspace-1",
            idle_delay_seconds=0.001,
        )

        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(verifier.started.wait(), timeout=1)
        worker.request_stop()
        await asyncio.sleep(0)

        self.assertFalse(task.done())
        verifier.release.set()
        await asyncio.wait_for(task, timeout=1)
        self.assertEqual(verifier.calls, 1)
        self.assertEqual(worker.snapshot.verified, 1)

    async def test_task_cancellation_propagates_to_active_evaluation(self) -> None:
        verifier = BlockingVerifier()
        worker = VerifierWorker(
            verifier,
            workspace_id="workspace-1",
            idle_delay_seconds=0.001,
        )
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(verifier.started.wait(), timeout=1)

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertTrue(verifier.cancelled)
        self.assertFalse(worker.snapshot.running)
        self.assertFalse(worker.snapshot.ready)

    async def test_invalid_status_is_sanitized_worker_failure(self) -> None:
        verifier = InvalidStatusVerifier()
        worker = VerifierWorker(
            verifier,
            workspace_id="workspace-1",
            idle_delay_seconds=1,
        )

        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(verifier.called.wait(), timeout=1)
        worker.request_stop()
        await asyncio.wait_for(task, timeout=1)

        self.assertEqual(worker.snapshot.failed_polls, 1)
        self.assertEqual(worker.snapshot.last_error_type, "TypeError")

    async def test_worker_rejects_a_second_concurrent_run(self) -> None:
        verifier = BlockingVerifier()
        worker = VerifierWorker(
            verifier,
            workspace_id="workspace-1",
            idle_delay_seconds=0.001,
        )
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(verifier.started.wait(), timeout=1)

        with self.assertRaisesRegex(RuntimeError, "already running"):
            await worker.run()

        worker.request_stop()
        verifier.release.set()
        await asyncio.wait_for(task, timeout=1)

    async def test_snapshot_exposes_error_type_without_exception_content(self) -> None:
        verifier = SequenceVerifier([RuntimeError("provider-secret")])
        worker = VerifierWorker(
            verifier,
            workspace_id="workspace-1",
            idle_delay_seconds=0.1,
        )
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(verifier.called.wait(), timeout=1)
        worker.request_stop()
        await asyncio.wait_for(task, timeout=1)

        public = worker.snapshot.to_public_dict()
        self.assertEqual(public["lastErrorType"], "RuntimeError")
        self.assertNotIn("provider-secret", str(public))
        self.assertEqual(public["failedPolls"], 1)
        self.assertIsNone(public["lastPollStatus"])

    def test_worker_rejects_invalid_poll_configuration(self) -> None:
        verifier = SequenceVerifier([])
        invalid = [
            {"workspace_id": ""},
            {"workspace_id": "workspace-1", "idle_delay_seconds": 0},
            {
                "workspace_id": "workspace-1",
                "idle_delay_seconds": 2,
                "max_delay_seconds": 1,
            },
        ]
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                VerifierWorker(verifier, **kwargs)


if __name__ == "__main__":
    unittest.main()
