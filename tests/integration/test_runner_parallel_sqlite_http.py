"""Real local boot/HTTP scheduling and actual process recovery exclusion.

Authentication/policy adapters are injected; Mission state, lease selection,
checkpoints, Harness, private images and CAS use their production implementations.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.api.v1.missions import (
    get_runner_workspace_grant_authorizer, get_workspace_claim_admission_policy_resolver,
)
from app.api.v1.router import router
from app.core.config import ArtifactStoreSettings
from app.db.init_db import _ainit_sqlite
from app.db.sqlite_pool import SQLitePool
from app.domain import ActorRef, Lease, MissionSource, WorkUnitStatus
from app.repositories import MissionRepository
from app.services.artifact_store_service import ContentAddressedArtifactPublisher
from app.services.auth_service import get_current_user
from app.services.desktop_local_runner import DesktopLocalRunnerController, DESKTOP_AGENT_ID, DESKTOP_ADAPTER_TYPE
from app.services.mission_service import MissionService
from app.services.model_contract import ModelResponse
from app.services.recovery_lock import RecoveryExecutionLock
from app.services.recovery_store import runner_state_directory
from app.services.runner_service import MissionControlRunnerClient, RunnerControlError
from app.services.workspace_admission_service import WorkspaceClaimAdmissionPolicy
from tests.api.test_missions_api import FakeRunnerWorkspaceGrantAuthorizer, FakeWorkspaceClaimAdmissionPolicyResolver
from tests.domain.factories import build_contract, build_work_unit
from tests.integration.recovery_worker import control_repository
from tests.integration.test_recovery_process import seed, paused_worker, restart
from tests.services.test_desktop_local_runner import desktop_settings, IdleMissionSource


class RecordedControlClient(MissionControlRunnerClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.errors = []
        self.calls = []

    async def _request(self, method, path, **kwargs):
        self.calls.append((method, path))
        try:
            return await super()._request(method, path, **kwargs)
        except RunnerControlError as exc:
            self.errors.append((path, str(exc)))
            raise


@pytest_asyncio.fixture
async def boot_http(tmp_path, monkeypatch):
    pool = SQLitePool(tmp_path / "http-control.sqlite3")
    await pool.initialize()
    monkeypatch.setattr("app.db.session.aget_pool", AsyncMock(return_value=pool))
    monkeypatch.setenv("AGENTHUB_RUNNER_STATE_ROOT", str(tmp_path / "private"))
    try:
        await _ainit_sqlite()
        repository = MissionRepository()
        service = MissionService(repository)
        actor = ActorRef(type="human", id="workspace-1")
        await service.create_mission(mission_id="parallel-mission", workspace_id="workspace-1",
            title="Parallel HTTP", objective="Produce registered results", source=MissionSource(type="manual"),
            contract=build_contract(), actor=actor)
        for index in range(2):
            await repository.add_work_unit(build_work_unit(id=f"parallel-{index}", mission_id="parallel-mission",
                kind="desktop.task", assigned_agent_id=DESKTOP_AGENT_ID, assigned_adapter=DESKTOP_ADAPTER_TYPE,
                required_capabilities=[], expected_outputs=[]))
        await service.start_mission("parallel-mission", actor=actor)
        application = FastAPI()
        application.include_router(router)
        application.dependency_overrides[get_current_user] = lambda: {"id": "runner-1", "role": "runner", "name": "runner-1"}
        application.dependency_overrides[get_runner_workspace_grant_authorizer] = lambda: FakeRunnerWorkspaceGrantAuthorizer({("workspace-1", "runner-1")})
        application.dependency_overrides[get_workspace_claim_admission_policy_resolver] = lambda: FakeWorkspaceClaimAdmissionPolicyResolver()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
            yield repository, RecordedControlClient("http://test", access_token="test-only", http_client=client)
    finally:
        await pool.close()


class ParallelModelFactory:
    recovery_manifest = {"provider": "deterministic-acceptance", "model": "parallel-v1", "messages": []}

    def __init__(self):
        self.entered = set()
        self.both_running = asyncio.Event()

    def build(self, tools):
        return self

    async def complete(self, request, tool_results, **kwargs):
        self.entered.add(request.execution.work_unit_id)
        if len(self.entered) == 2:
            self.both_running.set()
        await asyncio.wait_for(self.both_running.wait(), timeout=5)
        return ModelResponse(content=f"bounded independently verifiable result {request.execution.work_unit_id}")


async def verifying_units(repository):
    deadline = asyncio.get_running_loop().time() + 5
    while True:
        units = await repository.list_work_units("parallel-mission")
        if all(unit.status.value == "VERIFYING" for unit in units):
            return units
        assert asyncio.get_running_loop().time() < deadline, [(unit.id, unit.status) for unit in units]
        await asyncio.sleep(.02)


async def assert_registered_results(repository, units):
    for unit in units:
        assert unit.attempt == 1
        checkpoint = await repository.get_latest_execution_checkpoint(unit.id, 1)
        assert checkpoint.terminal and checkpoint.resume_protocol_version == 2
        artifacts = await repository.list_work_unit_artifacts("parallel-mission", unit.id, 1)
        assert len(artifacts) == 1 and artifacts[0].size_bytes > 0


@pytest.mark.asyncio
async def test_two_real_http_workers_use_authenticated_owner_and_execute_in_parallel(boot_http, tmp_path):
    repository, control = boot_http
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model_factory = ParallelModelFactory()
    controller = DesktopLocalRunnerController(desktop_settings(workers=2, user_id="runner-1", workspace_id="workspace-1"),
        control=control, model_factory=model_factory,
        publisher=ContentAddressedArtifactPublisher(ArtifactStoreSettings(backend="local", local_root=tmp_path / "cas")),
        mission_source=IdleMissionSource(), workspace_root=workspace, tools=[])
    await controller.start()
    try:
        try:
            await asyncio.wait_for(model_factory.both_running.wait(), timeout=5)
        except TimeoutError:
            events = await repository.list_events("parallel-mission")
            pytest.fail(str([(event.event_type, event.payload) for event in events]))
        units = await verifying_units(repository)
        assert model_factory.entered == {"parallel-0", "parallel-1"}
        assert all(worker._runner._runner_id == "runner-1" for worker in controller._workers)
        assert all(worker.snapshot.failed_polls == 0 for worker in controller._workers), control.errors
        await assert_registered_results(repository, units)
    finally:
        await controller.stop()


@pytest.mark.asyncio
async def test_busy_owned_checkpoint_does_not_starve_pending_sibling(tmp_path):
    await seed(tmp_path)
    async with paused_worker(tmp_path, "checkpoint"):
        async with control_repository(tmp_path / "control.sqlite3") as repository:
            assert (await repository.get_latest_execution_checkpoint("wu-1", 1)).tool_calls == 1
            await repository.add_work_unit(build_work_unit(id="wu-2", kind="desktop.task",
                assigned_agent_id="agent-1", assigned_adapter="function-calling"))
        code, stdout, stderr = await restart(tmp_path)
        if code != 0:
            pytest.fail(stderr.decode(errors="replace"))
        result = json.loads((tmp_path / "result.json").read_text())
        assert result["claimStatus"] == "claimed" and result["success"]
        async with control_repository(tmp_path / "control.sqlite3") as repository:
            pending = await repository.get_work_unit("wu-2")
            assert pending.status.value == "VERIFYING" and pending.attempt == 1
            active = await repository.get_work_unit("wu-1")
            assert active.status.value == "RUNNING" and active.lease.id == "lease-1"
            assert (await repository.get_latest_execution_checkpoint("wu-1", 1)).tool_calls == 1


@pytest.mark.asyncio
async def test_targeted_resume_preserves_owned_lease_at_full_quota(tmp_path):
    await seed(tmp_path)
    async with control_repository(tmp_path / "control.sqlite3") as repository:
        await repository.add_work_unit(build_work_unit(id="wu-2", kind="desktop.task",
            assigned_agent_id="agent-1", assigned_adapter="function-calling"))
        outcome = await MissionService(repository).claim_workspace_bound_work_unit("workspace-1", agent_id="agent-1",
            adapter_type="function-calling", supported_work_unit_kinds=("desktop.task",), runner_id="runner-1",
            actor=ActorRef(type="runner", id="runner-1"), lease_seconds=300, resume_mission_id="mis-1",
            admission_policy=WorkspaceClaimAdmissionPolicy("workspace-1", 1))
        assert outcome.status.value == "claimed"
        assert outcome.work_unit.id == "wu-1" and outcome.work_unit.lease.id == "lease-1"
        assert outcome.work_unit.attempt == 1 and outcome.work_unit.status.value == "RUNNING"
        assert (await repository.get_work_unit("wu-2")).status.value == "PENDING"
        normal = await MissionService(repository).claim_workspace_bound_work_unit("workspace-1", agent_id="agent-1",
            adapter_type="function-calling", supported_work_unit_kinds=("desktop.task",), runner_id="runner-1",
            actor=ActorRef(type="runner", id="runner-1"), lease_seconds=300,
            admission_policy=WorkspaceClaimAdmissionPolicy("workspace-1", 1))
        assert normal.status.value == "claimed" and normal.work_unit.lease == outcome.work_unit.lease
        assert (await repository.get_work_unit("wu-2")).attempt == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["other_owner", "expired", "pending_only", "other_mission"])
async def test_targeted_resume_cannot_lease_new_work_or_steal_an_attempt(tmp_path, condition):
    await seed(tmp_path)
    async with control_repository(tmp_path / "control.sqlite3") as repository:
        unit = await repository.get_work_unit("wu-1")
        runner_id, mission_id = "runner-1", "mis-1"
        if condition == "other_owner":
            runner_id = "other"
        elif condition == "expired":
            lease = unit.lease.model_copy(update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
            await repository.update_work_unit(unit.model_copy(update={"lease": lease}))
        elif condition == "pending_only":
            await repository.update_work_unit(unit.model_copy(update={"status": WorkUnitStatus.PENDING, "lease": None, "attempt": 0}))
        else:
            mission_id = "another-mission"
        selected = await repository.get_workspace_bound_work_unit_for_claim("workspace-1", agent_id="agent-1",
            adapter_type="function-calling", supported_work_unit_kinds=("desktop.task",), runner_id=runner_id,
            resume_mission_id=mission_id)
        assert selected is None


@pytest.mark.asyncio
async def test_sqlite_expired_lease_does_not_consume_new_claim_capacity(tmp_path):
    await seed(tmp_path)
    async with control_repository(tmp_path / "control.sqlite3") as repository:
        unit = await repository.get_work_unit("wu-1")
        lease = unit.lease.model_copy(update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
        await repository.update_work_unit(unit.model_copy(update={"lease": lease}))
        await repository.add_work_unit(build_work_unit(id="wu-2", kind="desktop.task",
            assigned_agent_id="agent-1", assigned_adapter="function-calling"))
        assert await repository.count_tenant_active_runner_work_units("workspace-1") == 0
        outcome = await MissionService(repository).claim_workspace_bound_work_unit("workspace-1", agent_id="agent-1",
            adapter_type="function-calling", supported_work_unit_kinds=("desktop.task",), runner_id="runner-1",
            actor=ActorRef(type="runner", id="runner-1"), lease_seconds=300,
            admission_policy=WorkspaceClaimAdmissionPolicy("workspace-1", 1))
        assert outcome.status.value == "claimed" and outcome.work_unit.id == "wu-2"


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["other_owner", "expired", "pending_only", "other_mission"])
async def test_http_resume_target_never_claims_foreign_expired_or_pending_work(boot_http, condition):
    repository, control = boot_http
    target = "parallel-mission"
    arguments = {"runner_id": "runner-1", "agent_id": DESKTOP_AGENT_ID,
        "adapter_type": DESKTOP_ADAPTER_TYPE, "supported_work_unit_kinds": ("desktop.task",), "lease_seconds": 300}
    if condition != "pending_only":
        claim = await control.claim_ready_work_unit("workspace-1", **arguments)
        unit = await repository.get_work_unit(claim["workUnit"]["id"])
        await control.start_work_unit(unit.mission_id, unit.id, runner_id="runner-1", lease_id=unit.lease.id)
        unit = await repository.get_work_unit(unit.id)
        if condition == "other_owner":
            lease = unit.lease.model_copy(update={"runner_id": "foreign-runner"})
            await repository.update_work_unit(unit.model_copy(update={"lease": lease}))
        elif condition == "expired":
            lease = unit.lease.model_copy(update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
            await repository.update_work_unit(unit.model_copy(update={"lease": lease}))
        else:
            target = "different-mission"
            await create_other_workspace_target(repository, target)
    result = await control.claim_ready_work_unit("workspace-1", **arguments, resume_mission_id=target)
    assert result == {"claimStatus": "idle", "workUnit": None}
    assert (await repository.get_work_unit("parallel-1")).status.value == "PENDING"
    assert (await repository.get_work_unit("parallel-1")).attempt == 0


async def create_other_workspace_target(repository, mission_id):
    service = MissionService(repository)
    actor = ActorRef(type="human", id="other-workspace")
    await service.create_mission(mission_id=mission_id, workspace_id="other-workspace",
        title="Foreign target", objective="A scoped target cannot cross workspace",
        source=MissionSource(type="manual"), contract=build_contract(id="other-contract"), actor=actor)
    await repository.add_work_unit(build_work_unit(id="foreign-owned", mission_id=mission_id, kind="desktop.task",
        assigned_agent_id=DESKTOP_AGENT_ID, assigned_adapter=DESKTOP_ADAPTER_TYPE, status="RUNNING", attempt=1,
        lease=Lease(id="foreign-owned-lease", runner_id="runner-1",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=300))))
    await service.start_mission(mission_id, actor=actor)


@pytest.mark.asyncio
async def test_expired_own_leases_are_filtered_before_bounded_resume_window(tmp_path):
    await seed(tmp_path)
    async with control_repository(tmp_path / "control.sqlite3") as repository:
        unit = await repository.get_work_unit("wu-1")
        for index in range(32):
            await repository.add_work_unit(build_work_unit(id=f"expired-{index:02}", kind="desktop.task",
                assigned_agent_id="agent-1", assigned_adapter="function-calling", status="RUNNING", attempt=1,
                lease=Lease(id=f"expired-lease-{index}", runner_id="runner-1",
                    expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))))
        selection = await repository.get_workspace_bound_work_unit_for_claim("workspace-1", agent_id="agent-1",
            adapter_type="function-calling", supported_work_unit_kinds=("desktop.task",), runner_id="runner-1",
            resume_mission_id="mis-1")
        assert selection[1].id == "wu-1" and selection[1].lease == unit.lease


class GatedPublisher:
    def __init__(self, tmp_path):
        self.delegate = ContentAddressedArtifactPublisher(ArtifactStoreSettings(backend="local", local_root=tmp_path / "cas"))
        self.first_entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = []

    async def publish_bytes(self, content):
        self.calls.append(content)
        if content.endswith(b"parallel-0"):
            self.first_entered.set()
            await self.release.wait()
        return await self.delegate.publish_bytes(content)


def parallel_controller(tmp_path, control, factory, publisher):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return DesktopLocalRunnerController(desktop_settings(workers=2, user_id="runner-1", workspace_id="workspace-1"),
        control=control, model_factory=factory, publisher=publisher,
        mission_source=IdleMissionSource(), workspace_root=workspace, tools=[])


async def wait_for_publication_busy(repository, controller, publisher):
    deadline = asyncio.get_running_loop().time() + 5
    while True:
        assert sum(body.endswith(b"parallel-0") for body in publisher.calls) == 1
        finished = await repository.get_work_unit("parallel-1")
        if finished.status.value == "VERIFYING" and any(worker.snapshot.capacity_saturated_polls for worker in controller._workers):
            return
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(.01)


def assert_attempt_lock_released(workspace):
    probe = RecoveryExecutionLock(runner_state_directory(workspace), "parallel-mission/parallel-0/1")
    probe.close()


@pytest.mark.asyncio
async def test_other_http_worker_cannot_republish_while_owner_is_publishing(boot_http, tmp_path):
    repository, control = boot_http
    publisher = GatedPublisher(tmp_path)
    controller = parallel_controller(tmp_path, control, ParallelModelFactory(), publisher)
    await controller.start()
    try:
        await asyncio.wait_for(publisher.first_entered.wait(), timeout=5)
        await wait_for_publication_busy(repository, controller, publisher)
        assert (await repository.get_work_unit("parallel-0")).status.value == "RUNNING"
        assert not any(path.endswith("parallel-0/complete") for _, path in control.calls)
        publisher.release.set()
        await verifying_units(repository)
        assert sum(body.endswith(b"parallel-0") for body in publisher.calls) == 1
        assert all(worker.snapshot.failed_polls == 0 for worker in controller._workers), control.errors
        assert_attempt_lock_released(controller.workspace_root)
    finally:
        publisher.release.set()
        await controller.stop()


class ImmediateModelFactory(ParallelModelFactory):
    async def complete(self, request, tool_results, **kwargs):
        return ModelResponse(content=f"bounded result {request.execution.work_unit_id}")


class FailingFirstPublisher(GatedPublisher):
    async def publish_bytes(self, content):
        if content.endswith(b"parallel-0"):
            raise OSError("controlled CAS outage")
        return await self.delegate.publish_bytes(content)


async def wait_for_publication_failure(repository):
    deadline = asyncio.get_running_loop().time() + 5
    while True:
        first = await repository.get_work_unit("parallel-0")
        if first.status.value == "FAILED":
            return
        assert asyncio.get_running_loop().time() < deadline, first.status
        await asyncio.sleep(.01)


@pytest.mark.asyncio
async def test_real_publication_failure_releases_recovery_lock(boot_http, tmp_path):
    repository, control = boot_http
    controller = parallel_controller(tmp_path, control, ImmediateModelFactory(), FailingFirstPublisher(tmp_path))
    await controller.start()
    try:
        await wait_for_publication_failure(repository)
        assert_attempt_lock_released(controller.workspace_root)
    finally:
        await controller.stop()
