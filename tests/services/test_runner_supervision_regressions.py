"""Cancellation fences reject late results; only terminal RPC ACKs prove completion."""

import asyncio

import pytest

from app.services.harness_service import HarnessResult, HarnessResumeInput
from app.services.runner_protocols import ClaimedWorkExecution, RunnerExecutionInput
from app.services.runner_service import (
    RunnerControlError,
    RunnerExecutionError,
    WorkUnitRunner,
)
from app.services.runner_supervision import (
    AttemptFence,
    RunnerTerminalAckUncertainError,
    supervise,
)
from tests.services.test_runner_artifact_bundle import HeldPublisher
from tests.services.test_runner_service import (
    FakeControl,
    FakePublisher,
    FakeSandbox,
    inbound_claim_payload,
)


class CancellationReturningHarness:
    async def execute(self, request):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return HarnessResult(await FakeSandbox().execute(""))


class CancellationReturningPublisher(FakePublisher):
    async def publish_bytes(self, content):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return await super().publish_bytes(content)


class CancellationReturningRegister(FakeControl):
    async def register_artifact(self, *args, **kwargs):
        result = await super().register_artifact(*args, **kwargs)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return result


def build_runner(control, *, publisher=None, harness=None):
    return WorkUnitRunner(control, publisher=publisher or FakePublisher(), runner_id="runner-1",
                          sandbox=FakeSandbox(), harness=harness, heartbeat_interval_seconds=0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["timeout", "heartbeat"])
@pytest.mark.parametrize("stage", ["harness", "publication", "registration"])
async def test_abort_fence_refuses_late_commands_when_port_swallows_cancellation(stage, trigger):
    control = CancellationReturningRegister() if stage == "registration" else FakeControl()
    publisher = CancellationReturningPublisher() if stage == "publication" else FakePublisher()
    harness = CancellationReturningHarness() if stage == "harness" else None
    if trigger == "heartbeat":
        control.heartbeat_error = RunnerControlError("lease lost")
    with pytest.raises(RunnerExecutionError):
        await build_runner(control, publisher=publisher, harness=harness).run(
            "mis-1", "wu-1", code="build", timeout=0.03 if trigger == "timeout" else 1,
        )
    names = [name for name, _ in control.calls]
    assert "complete" not in names
    assert len(control.registered) == (1 if stage == "registration" else 0)


@pytest.mark.asyncio
async def test_supervisor_drain_is_bounded_for_a_cancellation_resistant_port():
    release, entered = asyncio.Event(), asyncio.Event()
    fence = AttemptFence(asyncio.get_running_loop().time() + 0.02)
    late_commands = []

    async def operation():
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        fence.ensure_active()
        late_commands.append("complete")

    async def heartbeat():
        await asyncio.Event().wait()

    child = asyncio.create_task(operation())
    task = asyncio.create_task(supervise(child, heartbeat(), timeout=0.02, fence=fence, drain_timeout=0.02))
    try:
        await entered.wait()
        done, _ = await asyncio.wait((task,), timeout=0.3)
        assert task in done
        with pytest.raises(TimeoutError):
            task.result()
    finally:
        release.set()
        await asyncio.gather(child, task, return_exceptions=True)
    assert not late_commands and fence.aborted


class DelayedTerminalControl(FakeControl):
    def __init__(self, *, response="success", delay=0.04):
        super().__init__()
        self.committed = False
        self.response = response
        self.delay = delay

    async def complete_work_unit(self, mission_id, work_unit_id, **kwargs):
        result = await super().complete_work_unit(mission_id, work_unit_id, **kwargs)
        self.committed = True
        await asyncio.sleep(self.delay)
        if self.response == "rejected":
            raise RunnerControlError("completion rejected")
        return result

    async def heartbeat_work_unit(self, *args, **kwargs):
        if self.committed:
            raise RunnerControlError("work unit has no active lease")
        return await super().heartbeat_work_unit(*args, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0.02, 1])
async def test_already_dispatched_completion_must_receive_real_ack_within_grace(timeout):
    control = DelayedTerminalControl()
    result = await build_runner(control).run("mis-1", "wu-1", code="build", timeout=timeout)
    assert result.success and control.committed
    assert not any(name == "fail" for name, _ in control.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("response,delay", [("rejected", 0.04), ("success", 5)])
async def test_missing_or_rejected_terminal_ack_is_unknown_and_never_fabricates_failure(response, delay):
    control = DelayedTerminalControl(response=response, delay=delay)
    with pytest.raises(RunnerTerminalAckUncertainError, match="unknown"):
        await build_runner(control).run("mis-1", "wu-1", code="build", timeout=1)
    assert not any(name == "fail" for name, _ in control.calls)


@pytest.mark.asyncio
async def test_success_flush_runs_before_publication_and_complete():
    order = []

    class FlushHarness:
        async def execute(self, request):
            return HarnessResult(await FakeSandbox().execute(""))

        async def flush_checkpoints(self):
            order.append("flush")

    class Publisher(FakePublisher):
        async def publish_bytes(self, content):
            order.append("publish")
            return await super().publish_bytes(content)

    result = await build_runner(FakeControl(), publisher=Publisher(), harness=FlushHarness()).run(
        "mis-1", "wu-1", code="build",
    )
    assert result.success and order == ["flush", "publish"]


@pytest.mark.asyncio
async def test_caller_cancellation_fences_a_harness_that_returns_during_cleanup():
    control = FakeControl()
    task = asyncio.create_task(build_runner(control, harness=CancellationReturningHarness()).run(
        "mis-1", "wu-1", code="build",
    ))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not control.registered and not any(name == "complete" for name, _ in control.calls)


@pytest.mark.asyncio
async def test_cancellation_of_an_inflight_completion_propagates_without_second_terminal_command(caplog):
    control = DelayedTerminalControl(delay=5)
    task = asyncio.create_task(build_runner(control).run("mis-1", "wu-1", code="build"))
    while not control.committed:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "state may be committed" in caplog.text
    assert not any(name == "fail" for name, _ in control.calls)


@pytest.mark.asyncio
async def test_cancelled_heartbeat_cannot_keep_renewing_after_its_port_returns_late():
    class HeldHeartbeat(FakeControl):
        def __init__(self):
            super().__init__()
            self.entered, self.release, self.finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
            self.renewals = 0

        async def heartbeat_work_unit(self, *args, **kwargs):
            self.renewals += 1
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                await self.release.wait()
            result = await super().heartbeat_work_unit(*args, **kwargs)
            self.finished.set()
            return result

    control = HeldHeartbeat()
    task = asyncio.create_task(build_runner(control, publisher=HeldPublisher()).run("mis-1", "wu-1", code="build"))
    try:
        await control.entered.wait()
        task.cancel()
        done, _ = await asyncio.wait((task,), timeout=0.5)
        assert task in done
        with pytest.raises(asyncio.CancelledError):
            task.result()
    finally:
        control.release.set()
        await control.finished.wait()
        await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0.03)
    assert control.renewals == 1
    assert not any(name == "fail" for name, _ in control.calls)


class NeverExecuteHarness:
    async def execute(self, request):
        pytest.fail("a stale or unauthorized claim must not execute")


class ResumeResolver:
    async def resolve(self, payload):
        return ClaimedWorkExecution(
            RunnerExecutionInput(code="must not execute", resume=HarnessResumeInput("checkpoint", payload["attempt"])),
            NeverExecuteHarness(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status,detail", [(403, "Forbidden"), (404, "WorkUnit not found"),
                                         (503, "Active Runner lease ownership required")])
async def test_running_preflight_never_hides_general_authorization_or_transport_errors(status, detail):
    class RejectedPreflight(FakeControl):
        async def heartbeat_work_unit(self, *args, **kwargs):
            raise RunnerControlError("preflight rejected", http_status=status, detail=detail)

    control = RejectedPreflight()
    control.claim_payload = {**inbound_claim_payload(), "status": "RUNNING"}
    execution = WorkUnitRunner(control, publisher=FakePublisher(), runner_id="runner-1",
                               assigned_agent_id="reviewer", assigned_adapter="local_codex",
                               claimed_work_resolver=ResumeResolver(), supported_work_unit_kinds=("a2a.inbound",))
    with pytest.raises(RunnerControlError, match="preflight rejected"):
        await execution.claim_ready_and_run("workspace-1")
    assert not control.registered and not any(name == "fail" for name, _ in control.calls)
