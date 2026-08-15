from __future__ import annotations

import asyncio
import copy
import unittest
from typing import Any

from app.services.a2a_outbound_supervisor import (
    A2AOutboundSupervisionOutcome,
    A2AOutboundSupervisionResult,
)
from app.services.a2a_outbound_worker import (
    A2AOutboundWorkspacePollResult,
    A2AOutboundWorkspaceRunner,
)
from app.services.runner_service import RunnerControlError
from app.services.runner_worker import RunnerWorker
from app.services.workspace_admission_service import WorkspaceClaimStatus
from tests.services.test_a2a_outbound_runner import outbound_claim, outbound_context


def failed_supervision_result() -> A2AOutboundSupervisionResult:
    return A2AOutboundSupervisionResult(
        outcome=A2AOutboundSupervisionOutcome.REMOTE_FAILED,
        work_unit={
            "id": "wu-1",
            "missionId": "mis-1",
            "status": "FAILED",
            "attempt": 2,
            "lease": None,
        },
        remote_task=None,
        poll_count=0,
        failure_reason="remote A2A task reported failure",
    )


class FakeControl:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.called = asyncio.Event()

    async def claim_ready_work_unit(
        self,
        workspace_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append((workspace_id, kwargs))
        self.called.set()
        return copy.deepcopy(self.payload)


class FakeAttemptRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], int]] = []
        self.result = failed_supervision_result()

    async def run_claimed(
        self,
        claimed_work_unit: dict[str, Any],
        *,
        lease_seconds: int = 300,
    ) -> A2AOutboundSupervisionResult:
        self.calls.append((copy.deepcopy(claimed_work_unit), lease_seconds))
        return self.result


def claim_response(
    status: WorkspaceClaimStatus,
    work_unit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "claimStatus": status.value,
        "workUnit": work_unit,
    }


def workspace_runner(
    control: FakeControl,
    attempt: FakeAttemptRunner,
) -> A2AOutboundWorkspaceRunner:
    return A2AOutboundWorkspaceRunner(
        control,
        attempt,
        runner_id="runner-1",
        assigned_agent_id="outbound-dispatcher",
    )


class A2AOutboundWorkspaceRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_claims_exact_outbound_binding_and_runs_native_attempt(self) -> None:
        claim = outbound_claim(outbound_context())
        control = FakeControl(claim_response(WorkspaceClaimStatus.CLAIMED, claim))
        attempt = FakeAttemptRunner()

        result = await workspace_runner(control, attempt).claim_ready_and_run(
            "workspace-1",
            lease_seconds=120,
        )

        self.assertEqual(result.claim_status, WorkspaceClaimStatus.CLAIMED)
        self.assertIs(result.supervision_result, attempt.result)
        self.assertEqual(
            control.calls,
            [
                (
                    "workspace-1",
                    {
                        "runner_id": "runner-1",
                        "agent_id": "outbound-dispatcher",
                        "adapter_type": "a2a.outbound",
                        "lease_seconds": 120,
                    },
                )
            ],
        )
        self.assertEqual(attempt.calls, [(claim, 120)])

    async def test_non_claim_outcomes_do_not_execute_attempt(self) -> None:
        for status in (
            WorkspaceClaimStatus.IDLE,
            WorkspaceClaimStatus.CAPACITY_SATURATED,
        ):
            with self.subTest(status=status):
                control = FakeControl(claim_response(status))
                attempt = FakeAttemptRunner()

                result = await workspace_runner(
                    control,
                    attempt,
                ).claim_ready_and_run("workspace-1")

                self.assertEqual(result.claim_status, status)
                self.assertIsNone(result.supervision_result)
                self.assertEqual(attempt.calls, [])

    async def test_rejects_inconsistent_claim_before_attempt_execution(self) -> None:
        claim = outbound_claim(outbound_context())
        cases = (
            (
                {"claimStatus": "claimed", "workUnit": None},
                "inconsistent workspace claim response",
            ),
            (
                {"claimStatus": "idle", "workUnit": claim},
                "inconsistent workspace claim response",
            ),
            (
                claim_response(
                    WorkspaceClaimStatus.CLAIMED,
                    {**claim, "assignedAgentId": "another-agent"},
                ),
                "another agent",
            ),
            (
                claim_response(
                    WorkspaceClaimStatus.CLAIMED,
                    {
                        **claim,
                        "lease": {
                            **claim["lease"],
                            "runnerId": "another-runner",
                        },
                    },
                ),
                "another runner",
            ),
        )
        for payload, message in cases:
            with self.subTest(message=message):
                control = FakeControl(payload)
                attempt = FakeAttemptRunner()

                with self.assertRaisesRegex(RunnerControlError, message):
                    await workspace_runner(
                        control,
                        attempt,
                    ).claim_ready_and_run("workspace-1")

                self.assertEqual(attempt.calls, [])

    async def test_rejects_invalid_poll_input_before_control_plane_io(self) -> None:
        control = FakeControl(claim_response(WorkspaceClaimStatus.IDLE))
        runner = workspace_runner(control, FakeAttemptRunner())
        cases = (("", 300, "workspace_id"), ("workspace-1", 0, "lease_seconds"))
        for workspace_id, lease_seconds, message in cases:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(
                    ValueError,
                    message,
                ),
            ):
                await runner.claim_ready_and_run(
                    workspace_id,
                    lease_seconds=lease_seconds,
                )

        self.assertEqual(control.calls, [])

    async def test_generic_worker_accepts_outbound_poll_result(self) -> None:
        control = FakeControl(claim_response(WorkspaceClaimStatus.IDLE))
        worker = RunnerWorker(
            workspace_runner(control, FakeAttemptRunner()),
            workspace_id="workspace-1",
            idle_delay_seconds=1,
        )

        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(control.called.wait(), timeout=1)
        worker.request_stop()
        await asyncio.wait_for(task, timeout=1)

        self.assertEqual(worker.snapshot.polls, 1)
        self.assertEqual(worker.snapshot.idle_polls, 1)
        self.assertEqual(worker.snapshot.claimed, 0)


class A2AOutboundWorkspacePollResultTests(unittest.TestCase):
    def test_requires_result_only_for_claimed_status(self) -> None:
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            A2AOutboundWorkspacePollResult(
                claim_status=WorkspaceClaimStatus.CLAIMED,
                supervision_result=None,
            )
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            A2AOutboundWorkspacePollResult(
                claim_status=WorkspaceClaimStatus.IDLE,
                supervision_result=failed_supervision_result(),
            )


if __name__ == "__main__":
    unittest.main()
