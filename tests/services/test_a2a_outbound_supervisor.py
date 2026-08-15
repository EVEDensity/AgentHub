from __future__ import annotations

import asyncio
import unittest
from collections.abc import Sequence
from typing import Any

from app.domain import ArtifactKind
from app.services.a2a_outbound_result import (
    A2AImportedArtifact,
    A2AOutboundResultError,
    A2AOutboundResultImport,
)
from app.services.a2a_outbound_runner import (
    A2AOutboundClaimedWork,
    A2AOutboundTaskCommand,
    A2ARemoteTaskReference,
    A2ARemoteTaskSnapshot,
    A2ARemoteTaskState,
)
from app.services.a2a_outbound_supervisor import (
    A2AOutboundSupervisionError,
    A2AOutboundSupervisionOutcome,
    A2AOutboundSupervisor,
)
from app.services.artifact_store_service import PublishedArtifact


class FakeControl:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.heartbeat_received = asyncio.Event()
        self.heartbeat_error: Exception | None = None
        self.start_updates: dict[str, Any] = {}
        self.complete_error: Exception | None = None
        self.complete_updates: dict[str, Any] = {}

    def active_work_unit(self, **updates: Any) -> dict[str, Any]:
        payload = {
            "id": "wu-1",
            "missionId": "mis-1",
            "status": "RUNNING",
            "attempt": 2,
            "lease": {"id": "lease-1", "runnerId": "runner-1"},
        }
        payload.update(updates)
        return payload

    async def start_work_unit(
        self,
        _mission_id: str,
        _work_unit_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(("start", kwargs))
        return self.active_work_unit(**self.start_updates)

    async def heartbeat_work_unit(
        self,
        _mission_id: str,
        _work_unit_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(("heartbeat", kwargs))
        self.heartbeat_received.set()
        if self.heartbeat_error is not None:
            raise self.heartbeat_error
        return self.active_work_unit()

    async def fail_work_unit(
        self,
        _mission_id: str,
        _work_unit_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(("fail", kwargs))
        return self.active_work_unit(status="FAILED", lease=None)

    async def complete_work_unit(
        self,
        _mission_id: str,
        _work_unit_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(("complete", kwargs))
        if self.complete_error is not None:
            raise self.complete_error
        return self.active_work_unit(
            status="VERIFYING",
            lease=None,
            **self.complete_updates,
        )


class FakeTransport:
    def __init__(
        self,
        *,
        send_state: A2ARemoteTaskState = A2ARemoteTaskState.SUBMITTED,
        get_states: Sequence[A2ARemoteTaskState] = (),
        get_gate: asyncio.Event | None = None,
        send_error: Exception | None = None,
    ) -> None:
        self.send_state = send_state
        self.get_states = list(get_states)
        self.get_gate = get_gate
        self.send_error = send_error
        self.calls: list[str] = []
        self.cancelled = asyncio.Event()

    async def send(
        self,
        _command: A2AOutboundTaskCommand,
    ) -> A2ARemoteTaskSnapshot:
        self.calls.append("send")
        if self.send_error is not None:
            raise self.send_error
        return remote_snapshot(self.send_state)

    async def get(
        self,
        _reference: A2ARemoteTaskReference,
    ) -> A2ARemoteTaskSnapshot:
        self.calls.append("get")
        if self.get_gate is not None:
            await self.get_gate.wait()
        state = (
            self.get_states.pop(0)
            if self.get_states
            else A2ARemoteTaskState.WORKING
        )
        return remote_snapshot(state)

    async def cancel(
        self,
        _reference: A2ARemoteTaskReference,
    ) -> A2ARemoteTaskSnapshot:
        self.calls.append("cancel")
        self.cancelled.set()
        return remote_snapshot(A2ARemoteTaskState.CANCELED)

    async def get_result(
        self,
        _reference: A2ARemoteTaskReference,
    ) -> dict[str, Any]:
        self.calls.append("get_result")
        return {"id": "remote-task-1", "status": "completed"}


class FakeResultImporter:
    def __init__(self) -> None:
        self.calls: list[tuple[A2AOutboundClaimedWork, dict[str, Any]]] = []
        self.error: Exception | None = None

    async def import_result(
        self,
        work: A2AOutboundClaimedWork,
        payload: dict[str, Any],
    ) -> A2AOutboundResultImport:
        self.calls.append((work, payload))
        if self.error is not None:
            raise self.error
        return A2AOutboundResultImport(
            artifacts=(
                A2AImportedArtifact(
                    artifact_id="artifact-local-1",
                    remote_artifact_id="artifact-remote-1",
                    kind=ArtifactKind.REPORT,
                    media_type="application/json",
                    published=PublishedArtifact(
                        digest="sha256:" + "a" * 64,
                        size_bytes=10,
                        content_address="local:sha256/" + "a" * 64,
                    ),
                ),
            )
        )


def claimed_work(*, timeout_seconds: float = 1.0) -> A2AOutboundClaimedWork:
    reference = A2ARemoteTaskReference(
        target_agent_url="https://receiver.example.test/a2a",
        source_agent_url="https://sender.example.test",
        workspace_id="workspace-1",
        task_id="remote-task-1",
    )
    return A2AOutboundClaimedWork(
        mission_id="mis-1",
        work_unit_id="wu-1",
        attempt=2,
        lease_id="lease-1",
        timeout_seconds=timeout_seconds,
        command=A2AOutboundTaskCommand(
            reference=reference,
            objective="Build a verified release.",
            required_capabilities=("code_generation",),
        ),
    )


def remote_snapshot(state: A2ARemoteTaskState) -> A2ARemoteTaskSnapshot:
    return A2ARemoteTaskSnapshot(task_id="remote-task-1", state=state)


class A2AOutboundSupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_imports_completed_result_and_moves_local_work_to_verifying(
        self,
    ) -> None:
        control = FakeControl()
        importer = FakeResultImporter()
        transport = FakeTransport(
            get_states=(A2ARemoteTaskState.COMPLETED,),
            get_gate=control.heartbeat_received,
        )
        supervisor = A2AOutboundSupervisor(
            control,
            transport,
            importer,
            runner_id="runner-1",
            poll_interval_seconds=0.001,
            heartbeat_interval_seconds=0.005,
        )

        result = await supervisor.supervise(claimed_work(), lease_seconds=30)

        self.assertEqual(
            result.outcome,
            A2AOutboundSupervisionOutcome.LOCAL_VERIFYING,
        )
        self.assertEqual(result.work_unit["status"], "VERIFYING")
        self.assertEqual(result.remote_task.state, A2ARemoteTaskState.COMPLETED)
        self.assertEqual(result.poll_count, 1)
        self.assertEqual(len(result.imported_artifacts), 1)
        self.assertEqual(transport.calls, ["send", "get", "get_result"])
        self.assertEqual(len(importer.calls), 1)
        self.assertEqual(
            [name for name, _ in control.calls],
            ["start", "heartbeat", "complete"],
        )
        self.assertEqual(
            control.calls[-1][1]["artifact_refs"],
            [{"id": "artifact-local-1", "digest": "sha256:" + "a" * 64}],
        )
        self.assertNotIn("fail", [name for name, _ in control.calls])

    async def test_result_import_failure_is_recorded_without_remote_recancel(
        self,
    ) -> None:
        control = FakeControl()
        transport = FakeTransport(send_state=A2ARemoteTaskState.COMPLETED)
        importer = FakeResultImporter()
        importer.error = A2AOutboundResultError("invalid remote bundle detail")

        with self.assertRaisesRegex(
            A2AOutboundSupervisionError,
            "result import supervision failed",
        ):
            await A2AOutboundSupervisor(
                control,
                transport,
                importer,
                runner_id="runner-1",
            ).supervise(claimed_work())

        self.assertEqual(transport.calls, ["send", "get_result"])
        self.assertEqual([name for name, _ in control.calls], ["start", "fail"])
        self.assertEqual(
            control.calls[-1][1]["reason"],
            "outbound A2A result import failed: A2AOutboundResultError",
        )
        self.assertNotIn("invalid remote bundle detail", str(control.calls))

    async def test_completion_failure_is_recorded_with_original_lease(self) -> None:
        control = FakeControl()
        control.complete_error = RuntimeError("control detail")
        transport = FakeTransport(send_state=A2ARemoteTaskState.COMPLETED)

        with self.assertRaisesRegex(
            A2AOutboundSupervisionError,
            "could not complete outbound WorkUnit",
        ):
            await A2AOutboundSupervisor(
                control,
                transport,
                FakeResultImporter(),
                runner_id="runner-1",
            ).supervise(claimed_work())

        self.assertEqual(
            [name for name, _ in control.calls],
            ["start", "complete", "fail"],
        )
        self.assertEqual(
            control.calls[-1][1]["reason"],
            "outbound A2A result completion failed",
        )
        self.assertNotIn("control detail", str(control.calls))

    async def test_remote_terminal_failures_are_recorded_locally(self) -> None:
        cases = (
            (
                A2ARemoteTaskState.FAILED,
                A2AOutboundSupervisionOutcome.REMOTE_FAILED,
                "reported failure",
            ),
            (
                A2ARemoteTaskState.CANCELED,
                A2AOutboundSupervisionOutcome.REMOTE_CANCELED,
                "was canceled",
            ),
            (
                A2ARemoteTaskState.INPUT_REQUIRED,
                A2AOutboundSupervisionOutcome.INPUT_REQUIRED_UNSUPPORTED,
                "unsupported interactive input",
            ),
        )
        for state, outcome, reason in cases:
            with self.subTest(state=state):
                control = FakeControl()
                transport = FakeTransport(send_state=state)
                result = await A2AOutboundSupervisor(
                    control,
                    transport,
                    FakeResultImporter(),
                    runner_id="runner-1",
                ).supervise(claimed_work())

                self.assertEqual(result.outcome, outcome)
                self.assertIn(reason, result.failure_reason)
                self.assertEqual([name for name, _ in control.calls], ["start", "fail"])
                expected_transport_calls = (
                    ["send", "cancel"]
                    if state == A2ARemoteTaskState.INPUT_REQUIRED
                    else ["send"]
                )
                self.assertEqual(transport.calls, expected_transport_calls)
                self.assertNotIn("complete", [name for name, _ in control.calls])

    async def test_timeout_cancels_remote_and_records_local_failure(self) -> None:
        control = FakeControl()
        transport = FakeTransport()
        result = await A2AOutboundSupervisor(
            control,
            transport,
            FakeResultImporter(),
            runner_id="runner-1",
            poll_interval_seconds=0.01,
        ).supervise(claimed_work(timeout_seconds=0.03))

        self.assertEqual(result.outcome, A2AOutboundSupervisionOutcome.TIMED_OUT)
        self.assertIsNotNone(result.remote_task)
        self.assertGreaterEqual(result.poll_count, 1)
        self.assertTrue(transport.cancelled.is_set())
        self.assertEqual(transport.calls[0], "send")
        self.assertEqual(transport.calls[-1], "cancel")
        self.assertEqual([name for name, _ in control.calls], ["start", "fail"])

    async def test_heartbeat_failure_cancels_remote_and_fails_attempt(self) -> None:
        control = FakeControl()
        control.heartbeat_error = RuntimeError("lease lost")
        transport = FakeTransport(get_gate=asyncio.Event())
        supervisor = A2AOutboundSupervisor(
            control,
            transport,
            FakeResultImporter(),
            runner_id="runner-1",
            poll_interval_seconds=0.001,
            heartbeat_interval_seconds=0.005,
        )

        with self.assertRaisesRegex(
            A2AOutboundSupervisionError,
            "heartbeat supervision failed",
        ):
            await supervisor.supervise(claimed_work())

        self.assertTrue(transport.cancelled.is_set())
        self.assertEqual(
            [name for name, _ in control.calls],
            ["start", "heartbeat", "fail"],
        )

    async def test_caller_cancellation_propagates_to_remote_and_local_attempt(
        self,
    ) -> None:
        control = FakeControl()
        transport = FakeTransport(get_gate=asyncio.Event())
        supervisor = A2AOutboundSupervisor(
            control,
            transport,
            FakeResultImporter(),
            runner_id="runner-1",
            poll_interval_seconds=0.001,
        )
        task = asyncio.create_task(supervisor.supervise(claimed_work()))
        while "get" not in transport.calls:
            await asyncio.sleep(0)
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertTrue(transport.cancelled.is_set())
        self.assertEqual([name for name, _ in control.calls], ["start", "fail"])

    async def test_transport_failure_is_sanitized_and_recorded(self) -> None:
        control = FakeControl()
        transport = FakeTransport(send_error=RuntimeError("peer secret detail"))

        with self.assertRaisesRegex(
            A2AOutboundSupervisionError,
            "transport supervision failed",
        ):
            await A2AOutboundSupervisor(
                control,
                transport,
                FakeResultImporter(),
                runner_id="runner-1",
            ).supervise(claimed_work())

        self.assertEqual([name for name, _ in control.calls], ["start", "fail"])
        self.assertEqual(
            control.calls[-1][1]["reason"],
            "outbound A2A transport failed: RuntimeError",
        )
        self.assertNotIn("peer secret detail", str(control.calls))

    async def test_transport_timeout_is_not_misclassified_as_budget_timeout(
        self,
    ) -> None:
        control = FakeControl()
        transport = FakeTransport(send_error=TimeoutError("peer timed out"))

        with self.assertRaisesRegex(
            A2AOutboundSupervisionError,
            "transport supervision failed",
        ):
            await A2AOutboundSupervisor(
                control,
                transport,
                FakeResultImporter(),
                runner_id="runner-1",
            ).supervise(claimed_work())

        self.assertEqual([name for name, _ in control.calls], ["start", "fail"])
        self.assertEqual(transport.calls, ["send", "cancel"])
        self.assertEqual(
            control.calls[-1][1]["reason"],
            "outbound A2A transport failed: TimeoutError",
        )

    async def test_invalid_transport_snapshot_fails_local_attempt(self) -> None:
        class InvalidSnapshotTransport(FakeTransport):
            async def send(
                self,
                _command: A2AOutboundTaskCommand,
            ) -> A2ARemoteTaskSnapshot:
                self.calls.append("send")
                return A2ARemoteTaskSnapshot(
                    task_id="different-task",
                    state=A2ARemoteTaskState.SUBMITTED,
                )

        control = FakeControl()
        transport = InvalidSnapshotTransport()
        with self.assertRaisesRegex(
            A2AOutboundSupervisionError,
            "transport supervision failed",
        ):
            await A2AOutboundSupervisor(
                control,
                transport,
                FakeResultImporter(),
                runner_id="runner-1",
            ).supervise(claimed_work())

        self.assertEqual([name for name, _ in control.calls], ["start", "fail"])
        self.assertEqual(transport.calls, ["send", "cancel"])

    async def test_start_fence_mismatch_stops_before_remote_send(self) -> None:
        control = FakeControl()
        control.start_updates = {"attempt": 3}
        transport = FakeTransport(send_state=A2ARemoteTaskState.COMPLETED)

        with self.assertRaisesRegex(
            A2AOutboundSupervisionError,
            "rejected outbound WorkUnit start",
        ):
            await A2AOutboundSupervisor(
                control,
                transport,
                FakeResultImporter(),
                runner_id="runner-1",
            ).supervise(claimed_work())

        self.assertEqual(transport.calls, [])
        self.assertEqual([name for name, _ in control.calls], ["start"])


if __name__ == "__main__":
    unittest.main()
