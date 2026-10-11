"""Configured capability declarations and safe RUNNING claim entry."""
from __future__ import annotations

import pytest

from app.services.harness_service import HarnessResumeInput
from app.services.recovery_lock import RecoveryExecutionBusy
from app.services.runner.model import DesktopTaskHarnessFactory
from app.services.runner_composition import build_kind_aware_workspace_runner
from app.services.runner_service import (
    DesktopTaskClaimedWorkResolver, RunnerControlError, RunnerExecutionError, RunnerExecutionInput, WorkUnitRunner,
)
from app.services.workspace_admission_service import WorkspaceClaimStatus
from tests.services.test_desktop_local_runner import desktop_claim_payload
from tests.services.test_runner_composition import RecordingBindingFactory, RecordingModelFactory, read_binding
from tests.services.test_runner_service import (
    FakeControl, FakePublisher, RecordingHarness, StaticClaimedWorkResolver, inbound_claim_payload,
)


def runner(control, *, resolver=None, **kwargs):
    return WorkUnitRunner(control, publisher=FakePublisher(), runner_id="runner-1",
        assigned_agent_id="reviewer", assigned_adapter="local_codex",
        supported_work_unit_kinds=("a2a.inbound",), claimed_work_resolver=resolver, **kwargs)


@pytest.mark.asyncio
async def test_empty_declaration_preserves_legacy_consumer_signature():
    class LegacyControl:
        async def claim_ready_work_unit(self, workspace_id, *, runner_id, agent_id,
                adapter_type, supported_work_unit_kinds, lease_seconds):
            assert workspace_id == "workspace-1"
            return {"claimStatus": "idle", "workUnit": None}

    poll = await runner(LegacyControl()).claim_ready_and_run("workspace-1")
    assert poll.claim_status == WorkspaceClaimStatus.IDLE


@pytest.mark.asyncio
@pytest.mark.parametrize("composed", [False, True])
async def test_only_explicit_capabilities_are_forwarded(composed):
    control = FakeControl()
    capabilities = ("repository.read", "desktop.file.write")
    if composed:
        worker = build_kind_aware_workspace_runner(control, publisher=FakePublisher(),
            model_factory=RecordingModelFactory(), binding_factory=RecordingBindingFactory([read_binding()]),
            runner_id="runner-1", assigned_agent_id="reviewer", assigned_adapter="local_codex",
            supported_capabilities=capabilities)
    else:
        worker = runner(control, supported_capabilities=capabilities)
    await worker.claim_ready_and_run("workspace-1")
    assert control.calls[0][1]["supported_capabilities"] == capabilities


@pytest.mark.asyncio
async def test_composition_does_not_infer_capabilities_from_bindings():
    control = FakeControl()
    worker = build_kind_aware_workspace_runner(control, publisher=FakePublisher(),
        model_factory=RecordingModelFactory(), binding_factory=RecordingBindingFactory([read_binding()]),
        runner_id="runner-1", assigned_agent_id="reviewer", assigned_adapter="local_codex")
    await worker.claim_ready_and_run("workspace-1")
    assert "supported_capabilities" not in control.calls[0][1]


@pytest.mark.parametrize("capabilities", [None, ["read"], ("",), (" read",), ("read ",),
    ("read", "read"), (True,), ("a" * 256,), tuple(str(index) for index in range(257))])
def test_invalid_capability_declarations_are_rejected(capabilities):
    with pytest.raises((ValueError, TypeError), match="supported_capabilities"):
        runner(FakeControl(), supported_capabilities=capabilities)


@pytest.mark.asyncio
async def test_running_claim_resumes_without_restarting_work_unit():
    control = FakeControl()
    control.claim_payload = inbound_claim_payload()
    control.claim_payload["status"] = "RUNNING"
    resume = HarnessResumeInput(checkpoint_id="checkpoint-real-resolver-port", attempt=2)
    resolver = StaticClaimedWorkResolver(RunnerExecutionInput(code="trusted", resume=resume))
    poll = await runner(control, resolver=resolver).claim_ready_and_run("workspace-1")
    assert poll.run_result.success
    assert resolver.harness.requests[0].resume is resume
    assert [name for name, _ in control.calls] == ["claim_ready", "register", "complete"]


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [None, HarnessResumeInput(checkpoint_id="other-attempt", attempt=1)])
async def test_running_claim_without_matching_journal_cannot_execute(resume):
    class RecoverableHarness(RecordingHarness):
        closed = False

        def close_recovery(self):
            self.closed = True

    control = FakeControl()
    control.claim_payload = inbound_claim_payload()
    control.claim_payload["status"] = "RUNNING"
    harness = RecoverableHarness()
    resolver = StaticClaimedWorkResolver(RunnerExecutionInput(code="must not run", resume=resume), harness)
    with pytest.raises(RunnerExecutionError, match="resolution failed"):
        await runner(control, resolver=resolver).claim_ready_and_run("workspace-1")
    assert harness.requests == [] and harness.closed
    assert [name for name, _ in control.calls] == ["claim_ready", "fail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"status": "PENDING"}, {"assignedAgentId": "other"},
    {"assignedAdapter": "other"}, {"attempt": True}, {"attempt": 0},
    {"lease": {"id": "lease-1", "runnerId": "other"}},
    {"lease": {"runnerId": "runner-1"}}])
async def test_running_claim_identity_and_attempt_rejected_before_resolver(change):
    control = FakeControl()
    control.claim_payload = {**inbound_claim_payload(), "status": "RUNNING", **change}
    resolver = StaticClaimedWorkResolver(RunnerExecutionInput(code="must not run"))
    with pytest.raises(RunnerControlError):
        await runner(control, resolver=resolver).claim_ready_and_run("workspace-1")
    assert resolver.received == []
    assert [name for name, _ in control.calls] == ["claim_ready"]


@pytest.mark.asyncio
async def test_live_recovery_owner_reports_busy_without_failing_active_work():
    class BusyResolver:
        async def resolve(self, payload):
            raise RecoveryExecutionBusy("another process owns the execution")

    control = FakeControl()
    control.claim_payload = {**inbound_claim_payload(), "status": "RUNNING"}
    poll = await runner(control, resolver=BusyResolver()).claim_ready_and_run("workspace-1")
    assert poll.claim_status == WorkspaceClaimStatus.CAPACITY_SATURATED
    assert poll.run_result is None
    assert [name for name, _ in control.calls] == ["claim_ready"]


@pytest.mark.asyncio
@pytest.mark.parametrize("checkpoint_present", [False, True])
async def test_desktop_factory_without_manifest_cannot_fresh_execute_running_claim(tmp_path, checkpoint_present):
    control = FakeControl()
    claimed, context = desktop_claim_payload()
    control.claim_payload = claimed["workUnit"]
    control.claim_payload["status"] = context["workUnit"]["status"] = "RUNNING"
    if checkpoint_present:
        context["checkpoint"] = {"id": "incomplete-public-anchor"}
    control.execution_context_payload = context
    model_factory = RecordingModelFactory()
    factory = DesktopTaskHarnessFactory(model_factory, tools=[], workspace_root=tmp_path)
    runner_id = control.claim_payload["lease"]["runnerId"]
    resolver = DesktopTaskClaimedWorkResolver(control, runner_id=runner_id, harness_factory=factory)
    worker = WorkUnitRunner(control, publisher=FakePublisher(), runner_id=runner_id,
        assigned_agent_id=control.claim_payload["assignedAgentId"],
        assigned_adapter=control.claim_payload["assignedAdapter"],
        supported_work_unit_kinds=("desktop.task",), claimed_work_resolver=resolver)
    with pytest.raises(RunnerExecutionError, match="resolution failed"):
        await worker.claim_ready_and_run("local-admin")
    assert model_factory.model.tool_results == []
    assert [name for name, _ in control.calls] == ["claim_ready", "context", "fail"]
