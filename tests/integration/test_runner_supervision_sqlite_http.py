"""Real SQLite/HTTP terminal ACK races and stale-lease publication rejection."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.domain import ActorRef, Lease
from app.services.mission_service import MissionService
from app.services.runner_service import (
    RunnerControlError,
    RunnerExecutionError,
    WorkUnitRunner,
)
from app.services.runner_supervision import RunnerTerminalAckUncertainError
from app.services.workspace_admission_service import WorkspaceClaimStatus
from tests.integration.test_runner_parallel_sqlite_http import boot_http as _boot_http
from tests.services.test_runner_service import FakePublisher, FakeSandbox
from tests.services.test_runner_supervision_regressions import (
    CancellationReturningHarness,
    ResumeResolver,
)

boot_http = _boot_http


class DirectLeaseBridge:
    """Only lease setup is direct; start/heartbeat/register/complete are real HTTP."""

    def __init__(self, repository, control):
        self.repository, self.control = repository, control

    def __getattr__(self, name):
        return getattr(self.control, name)

    async def lease_work_unit(self, mission_id, work_unit_id, **kwargs):
        unit = await MissionService(self.repository).lease_work_unit(
            mission_id, work_unit_id, actor=ActorRef(type="runner", id="runner-1"), **kwargs,
        )
        return unit.to_public_dict()


def runner(repository, control, *, publisher=None, harness=None):
    return WorkUnitRunner(DirectLeaseBridge(repository, control), publisher=publisher or FakePublisher(),
                          sandbox=FakeSandbox(), harness=harness, runner_id="runner-1",
                          heartbeat_interval_seconds=0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("delay", [0.05, 5])
async def test_committed_complete_with_delayed_http_response_requires_bounded_real_ack(boot_http, delay):
    repository, control = boot_http
    original = control._request

    async def delayed_response(method, path, **kwargs):
        result = await original(method, path, **kwargs)
        if path.endswith("/complete"):
            await asyncio.sleep(delay)
        return result

    control._request = delayed_response
    execution = runner(repository, control).run("parallel-mission", "parallel-0", code="build", timeout=2)
    if delay == 5:
        with pytest.raises(RunnerTerminalAckUncertainError, match="unknown"):
            await execution
    else:
        assert (await execution).success
    await assert_committed(repository, control)


async def assert_committed(repository, control):
    unit = await repository.get_work_unit("parallel-0")
    assert unit.status.value == "VERIFYING" and unit.attempt == 1 and unit.lease is None
    artifacts = await repository.list_work_unit_artifacts("parallel-mission", unit.id, 1)
    assert len(artifacts) == 1
    events = await repository.list_work_unit_events("parallel-mission")
    completed = [event for event in events if event.event_type == "work_unit.lifecycle.completed"]
    assert len(completed) == 1
    assert completed[0].payload["attempt"] == 1
    assert completed[0].payload["artifactRefs"] == [{"id": artifacts[0].id, "digest": artifacts[0].digest}]
    assert not any(event.event_type == "work_unit.lifecycle.failed" for event in events)
    assert any(path.endswith("/heartbeat") for path, _ in control.errors)


@pytest.mark.asyncio
async def test_expired_attempt_cannot_admit_a_late_publication_under_its_previous_lease(boot_http):
    repository, control = boot_http

    class ReassignedPublisher(FakePublisher):
        async def publish_bytes(self, content):
            unit = await repository.get_work_unit("parallel-0")
            await repository.update_work_unit(unit.model_copy(update={
                "attempt": 2,
                "lease": Lease(id="replacement-lease", runner_id="runner-1",
                               expires_at=datetime.now(UTC) + timedelta(seconds=300)),
            }))
            return await super().publish_bytes(content)

    with pytest.raises(RunnerControlError):
        await runner(repository, control, publisher=ReassignedPublisher()).run(
            "parallel-mission", "parallel-0", code="build",
        )
    unit = await repository.get_work_unit("parallel-0")
    assert unit.status.value == "RUNNING" and unit.attempt == 2 and unit.lease.id == "replacement-lease"
    assert await repository.list_work_unit_artifacts("parallel-mission", unit.id, 1) == []
    assert not any(path.endswith("/complete") for _, path in control.calls)


@pytest.mark.asyncio
async def test_timeout_rejects_cancellation_swallowing_harness_before_http_artifact_admission(boot_http):
    repository, control = boot_http
    with pytest.raises(RunnerExecutionError):
        await runner(repository, control, harness=CancellationReturningHarness()).run(
            "parallel-mission", "parallel-0", code="build", timeout=0.03,
        )
    unit = await repository.get_work_unit("parallel-0")
    assert unit.status.value == "FAILED"
    assert await repository.list_work_unit_artifacts("parallel-mission", unit.id, 1) == []
    assert not any(path.endswith(("/artifacts", "/complete")) for _, path in control.calls)


@pytest.mark.asyncio
async def test_claim_captured_before_owner_completed_cannot_replay_after_recovery_lock_is_released(boot_http):
    repository, control = boot_http
    claimed = {}

    class CapturingPublisher(FakePublisher):
        async def publish_bytes(self, content):
            claimed.update((await repository.get_work_unit("parallel-0")).to_public_dict())
            return await super().publish_bytes(content)

    assert (await runner(repository, control, publisher=CapturingPublisher()).run(
        "parallel-mission", "parallel-0", code="build",
    )).success
    assert claimed["status"] == "RUNNING"

    class ReplayedClaim(DirectLeaseBridge):
        async def claim_ready_work_unit(self, *args, **kwargs):
            return {"claimStatus": "claimed", "workUnit": claimed}

    publisher = FakePublisher()
    stale = WorkUnitRunner(ReplayedClaim(repository, control), publisher=publisher, runner_id="runner-1",
                           assigned_agent_id=claimed["assignedAgentId"], assigned_adapter=claimed["assignedAdapter"],
                           claimed_work_resolver=ResumeResolver(), supported_work_unit_kinds=("desktop.task",))
    poll = await stale.claim_ready_and_run("workspace-1")
    assert poll.claim_status == WorkspaceClaimStatus.IDLE and poll.run_result is None
    assert not publisher.contents
    assert (await repository.get_work_unit("parallel-0")).status.value == "VERIFYING"
