from __future__ import annotations

import asyncio
import unittest
from typing import Any

from app.services.runner_service import RunnerRunResult
from app.services.runner_worker import RunnerWorker


class SequenceRunner:
    def __init__(self, outcomes: list[RunnerRunResult | None | Exception]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, int]] = []
        self.called = asyncio.Event()

    async def claim_ready_and_run(
        self,
        workspace_id: str,
        *,
        lease_seconds: int = 300,
        **kwargs: Any,
    ) -> RunnerRunResult | None:
        del kwargs
        self.calls.append((workspace_id, lease_seconds))
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if not self.outcomes:
            self.called.set()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class BlockingRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False
        self.calls = 0

    async def claim_ready_and_run(
        self, *args: Any, **kwargs: Any
    ) -> RunnerRunResult:
        del args, kwargs
        self.calls += 1
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return RunnerRunResult(success=True, work_unit={}, artifact=None)


class RunnerWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_polls_with_backoff_and_resets_after_claim(self) -> None:
        claimed = RunnerRunResult(success=True, work_unit={}, artifact=None)
        runner = SequenceRunner([None, RuntimeError("control unavailable"), claimed])
        worker = RunnerWorker(
            runner,
            workspace_id="workspace-1",
            lease_seconds=120,
            idle_delay_seconds=0.001,
            max_delay_seconds=0.004,
        )

        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(runner.called.wait(), timeout=1)
        worker.request_stop()
        await asyncio.wait_for(task, timeout=1)

        snapshot = worker.snapshot
        self.assertFalse(snapshot.running)
        self.assertFalse(snapshot.ready)
        self.assertTrue(snapshot.stop_requested)
        self.assertEqual(snapshot.polls, 3)
        self.assertEqual(snapshot.claimed, 1)
        self.assertEqual(snapshot.idle_polls, 1)
        self.assertEqual(snapshot.failed_polls, 1)
        self.assertEqual(snapshot.consecutive_failures, 0)
        self.assertEqual(snapshot.current_delay_seconds, 0.001)
        self.assertIsNone(snapshot.last_error_type)
        self.assertEqual(runner.calls, [("workspace-1", 120)] * 3)

    async def test_stop_waits_for_active_claim_before_exiting(self) -> None:
        runner = BlockingRunner()
        worker = RunnerWorker(
            runner,
            workspace_id="workspace-1",
            idle_delay_seconds=0.001,
        )

        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(runner.started.wait(), timeout=1)
        worker.request_stop()
        await asyncio.sleep(0)

        self.assertFalse(task.done())
        runner.release.set()
        await asyncio.wait_for(task, timeout=1)
        self.assertEqual(runner.calls, 1)
        self.assertEqual(worker.snapshot.claimed, 1)

    async def test_task_cancellation_propagates_to_active_runner(self) -> None:
        runner = BlockingRunner()
        worker = RunnerWorker(
            runner,
            workspace_id="workspace-1",
            idle_delay_seconds=0.001,
        )
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(runner.started.wait(), timeout=1)

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertTrue(runner.cancelled)
        self.assertFalse(worker.snapshot.running)
        self.assertFalse(worker.snapshot.ready)

    async def test_worker_rejects_a_second_concurrent_run(self) -> None:
        runner = BlockingRunner()
        worker = RunnerWorker(
            runner,
            workspace_id="workspace-1",
            idle_delay_seconds=0.001,
        )
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(runner.started.wait(), timeout=1)

        with self.assertRaisesRegex(RuntimeError, "already running"):
            await worker.run()

        worker.request_stop()
        runner.release.set()
        await asyncio.wait_for(task, timeout=1)

    async def test_failure_snapshot_exposes_type_without_exception_content(self) -> None:
        runner = SequenceRunner([RuntimeError("provider-secret")])
        worker = RunnerWorker(
            runner,
            workspace_id="workspace-1",
            idle_delay_seconds=0.1,
        )
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(runner.called.wait(), timeout=1)
        worker.request_stop()
        await asyncio.wait_for(task, timeout=1)

        public = worker.snapshot.to_public_dict()
        self.assertEqual(public["lastErrorType"], "RuntimeError")
        self.assertNotIn("provider-secret", str(public))
        self.assertEqual(public["failedPolls"], 1)

    def test_worker_rejects_invalid_poll_configuration(self) -> None:
        runner = SequenceRunner([])
        invalid = [
            {"workspace_id": ""},
            {"workspace_id": "workspace-1", "lease_seconds": 0},
            {"workspace_id": "workspace-1", "idle_delay_seconds": 0},
            {
                "workspace_id": "workspace-1",
                "idle_delay_seconds": 2,
                "max_delay_seconds": 1,
            },
        ]
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                RunnerWorker(runner, **kwargs)


if __name__ == "__main__":
    unittest.main()
