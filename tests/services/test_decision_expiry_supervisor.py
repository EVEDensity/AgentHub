from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass
from typing import Any

from app.services.decision_expiry_supervisor import (
    DecisionExpiryPollStatus,
    DecisionExpirySupervisor,
)


@dataclass(frozen=True)
class ExpiryOutcome:
    expired: bool


class SequenceExpiryCommand:
    def __init__(self, outcomes: list[ExpiryOutcome | Exception]) -> None:
        self.outcomes = outcomes
        self.calls = 0
        self.called = asyncio.Event()

    async def expire_next_decision(self) -> ExpiryOutcome:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if not self.outcomes:
            self.called.set()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class BlockingExpiryCommand:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False
        self.calls = 0

    async def expire_next_decision(self) -> ExpiryOutcome:
        self.calls += 1
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return ExpiryOutcome(expired=False)


class InvalidExpiryCommand:
    def __init__(self) -> None:
        self.called = asyncio.Event()

    async def expire_next_decision(self) -> Any:
        self.called.set()
        return type("InvalidExpiryOutcome", (), {"expired": "yes"})()


class DecisionExpirySupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_drains_expired_decisions_without_waiting_for_idle(self) -> None:
        command = SequenceExpiryCommand(
            [
                ExpiryOutcome(expired=True),
                ExpiryOutcome(expired=True),
                ExpiryOutcome(expired=False),
            ]
        )
        supervisor = DecisionExpirySupervisor(
            command,
            idle_delay_seconds=10,
            max_delay_seconds=20,
        )

        task = asyncio.create_task(supervisor.run())
        await asyncio.wait_for(command.called.wait(), timeout=1)
        supervisor.request_stop()
        await asyncio.wait_for(task, timeout=1)

        self.assertEqual(command.calls, 3)
        self.assertEqual(supervisor.snapshot.expired, 2)
        self.assertEqual(supervisor.snapshot.idle_polls, 1)
        self.assertEqual(supervisor.snapshot.current_delay_seconds, 10)
        self.assertEqual(
            supervisor.snapshot.last_poll_status,
            DecisionExpiryPollStatus.IDLE,
        )

    async def test_idle_and_transient_errors_use_bounded_backoff(self) -> None:
        command = SequenceExpiryCommand(
            [
                ExpiryOutcome(expired=False),
                RuntimeError("database-secret"),
                RuntimeError("database-secret"),
                ExpiryOutcome(expired=True),
                ExpiryOutcome(expired=False),
            ]
        )
        supervisor = DecisionExpirySupervisor(
            command,
            idle_delay_seconds=0.001,
            max_delay_seconds=0.004,
        )

        task = asyncio.create_task(supervisor.run())
        await asyncio.wait_for(command.called.wait(), timeout=1)
        supervisor.request_stop()
        await asyncio.wait_for(task, timeout=1)

        snapshot = supervisor.snapshot
        self.assertEqual(snapshot.polls, 5)
        self.assertEqual(snapshot.expired, 1)
        self.assertEqual(snapshot.idle_polls, 2)
        self.assertEqual(snapshot.failed_polls, 2)
        self.assertEqual(snapshot.consecutive_failures, 0)
        self.assertIsNone(snapshot.last_error_type)
        self.assertEqual(snapshot.current_delay_seconds, 0.001)

    async def test_stop_interrupts_an_idle_wait_without_another_poll(self) -> None:
        command = SequenceExpiryCommand([ExpiryOutcome(expired=False)])
        supervisor = DecisionExpirySupervisor(
            command,
            idle_delay_seconds=30,
            max_delay_seconds=30,
        )

        task = asyncio.create_task(supervisor.run())
        await asyncio.wait_for(command.called.wait(), timeout=1)
        supervisor.request_stop()
        await asyncio.wait_for(task, timeout=1)

        self.assertEqual(command.calls, 1)
        self.assertTrue(supervisor.snapshot.stop_requested)
        self.assertFalse(supervisor.snapshot.running)

    async def test_stop_waits_for_active_expiry_command(self) -> None:
        command = BlockingExpiryCommand()
        supervisor = DecisionExpirySupervisor(command, idle_delay_seconds=0.001)

        task = asyncio.create_task(supervisor.run())
        await asyncio.wait_for(command.started.wait(), timeout=1)
        supervisor.request_stop()
        await asyncio.sleep(0)

        self.assertFalse(task.done())
        command.release.set()
        await asyncio.wait_for(task, timeout=1)
        self.assertEqual(command.calls, 1)

    async def test_task_cancellation_propagates_to_expiry_command(self) -> None:
        command = BlockingExpiryCommand()
        supervisor = DecisionExpirySupervisor(command, idle_delay_seconds=0.001)
        task = asyncio.create_task(supervisor.run())
        await asyncio.wait_for(command.started.wait(), timeout=1)

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertTrue(command.cancelled)
        self.assertFalse(supervisor.snapshot.running)
        self.assertFalse(supervisor.snapshot.ready)

    async def test_invalid_outcome_is_a_sanitized_failure(self) -> None:
        command = InvalidExpiryCommand()
        supervisor = DecisionExpirySupervisor(
            command,
            idle_delay_seconds=30,
            max_delay_seconds=30,
        )

        task = asyncio.create_task(supervisor.run())
        await asyncio.wait_for(command.called.wait(), timeout=1)
        supervisor.request_stop()
        await asyncio.wait_for(task, timeout=1)

        public = supervisor.snapshot.to_public_dict()
        self.assertEqual(public["failedPolls"], 1)
        self.assertEqual(public["lastErrorType"], "TypeError")
        self.assertNotIn("database-secret", str(public))
        self.assertIsNone(public["lastPollStatus"])

    async def test_rejects_a_second_concurrent_run(self) -> None:
        command = BlockingExpiryCommand()
        supervisor = DecisionExpirySupervisor(command, idle_delay_seconds=0.001)
        task = asyncio.create_task(supervisor.run())
        await asyncio.wait_for(command.started.wait(), timeout=1)

        with self.assertRaisesRegex(RuntimeError, "already running"):
            await supervisor.run()

        supervisor.request_stop()
        command.release.set()
        await asyncio.wait_for(task, timeout=1)

    def test_rejects_invalid_poll_configuration(self) -> None:
        command = SequenceExpiryCommand([])
        invalid = (
            {"idle_delay_seconds": 0},
            {"idle_delay_seconds": 2, "max_delay_seconds": 1},
        )
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                DecisionExpirySupervisor(command, **kwargs)


if __name__ == "__main__":
    unittest.main()
