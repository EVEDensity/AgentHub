"""Lease supervision covers the full bundle, including slow publication."""

import asyncio
from dataclasses import replace

import pytest

from app.services.execution_artifacts import HarnessArtifact
from app.services.harness_service import HarnessResult
from app.services.runner_service import WorkUnitRunner, RunnerControlError, RunnerExecutionError
from tests.services.test_runner_service import FakeControl, FakePublisher, FakeSandbox


class BundleHarness:
    def __init__(self, artifacts):
        self.artifacts = artifacts

    async def execute(self, request):
        return HarnessResult((await FakeSandbox().execute(request.code)), artifacts=self.artifacts)


class HeldPublisher(FakePublisher):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def publish_bytes(self, content):
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return await super().publish_bytes(content)


def bundle():
    return (HarnessArtifact("patch", "diff", "text/plain", b"patch bytes"),
            HarnessArtifact("result", "test-result", "application/json", b"{}"),
            HarnessArtifact("manifest", "report", "application/json", b"{\"manifest\":1}"))


def runner(control, publisher, artifacts=None):
    return WorkUnitRunner(control, publisher=publisher, runner_id="runner-1",
                          heartbeat_interval_seconds=0.01, harness=BundleHarness(artifacts or bundle()))


@pytest.mark.asyncio
async def test_bundle_registration_is_ordered_and_complete_has_every_ref():
    control, publisher = FakeControl(), FakePublisher()
    result = await runner(control, publisher).run("mis-1", "wu-1", code="build")
    assert result.success and result.work_unit["status"] == "VERIFYING"
    assert [entry["artifact_id"] for entry in control.registered] == ["patch", "result", "manifest"]
    assert [ref["id"] for ref in control.calls[-1][1]["artifact_refs"]] == ["patch", "result", "manifest"]
    assert publisher.contents == [item.content for item in bundle()]
    assert len(result.artifacts) == 3


@pytest.mark.asyncio
async def test_heartbeat_stays_alive_while_publisher_is_waiting():
    control, publisher = FakeControl(), HeldPublisher()
    task = asyncio.create_task(runner(control, publisher).run("mis-1", "wu-1", code="build"))
    await asyncio.wait_for(publisher.entered.wait(), 1)
    await asyncio.wait_for(control.heartbeat_received.wait(), 1)
    assert not control.registered
    publisher.release.set()
    assert (await task).success


@pytest.mark.asyncio
async def test_lost_lease_cancels_publication_and_never_completes():
    control, publisher = FakeControl(), HeldPublisher()
    control.heartbeat_error = RunnerControlError("lease lost")
    with pytest.raises(RunnerExecutionError, match="heartbeat supervision failed"):
        await runner(control, publisher).run("mis-1", "wu-1", code="build")
    assert publisher.cancelled and not control.registered
    assert not any(name == "complete" for name, _ in control.calls)


@pytest.mark.asyncio
async def test_bundle_validation_happens_before_any_publication():
    control, publisher = FakeControl(), FakePublisher()
    duplicate = (bundle()[0], replace(bundle()[1], id="patch"))
    with pytest.raises(RunnerExecutionError, match="artifact reporting failed"):
        await runner(control, publisher, duplicate).run("mis-1", "wu-1", code="build")
    assert not publisher.contents and not control.registered


@pytest.mark.asyncio
async def test_deadline_covers_publication_and_does_not_complete():
    control, publisher = FakeControl(), HeldPublisher()
    with pytest.raises(RunnerExecutionError):
        await runner(control, publisher).run("mis-1", "wu-1", code="build", timeout=0.04)
    assert publisher.cancelled and not control.registered
    assert not any(name == "complete" for name, _ in control.calls)
