"""Real Mission ledger guidance survives exact Desktop receipt-gap recovery."""
from __future__ import annotations

from contextlib import asynccontextmanager, closing
from dataclasses import replace
from datetime import UTC, datetime
from typing import ClassVar

import httpx
import pytest
from fastapi import FastAPI

from app.domain import ActorRef, EventEnvelope, MissionSource
from app.services.desktop_guidance import (
    InProcessGuidanceSource,
    MissionControlGuidanceSource,
)
from app.services.harness_checkpoint import HarnessEventType, HarnessExecutionContext
from app.services.harness_types import HarnessRequest
from app.services.mission_service import MissionService
from app.services.model_contract import ModelResponse, ToolCall
from app.services.recovery_image import ResumeImageError
from app.services.recovery_store import ResumeImageStore
from app.services.runner.model import DesktopTaskHarnessFactory
from app.services.tools.policy import ToolExecutionPolicy
from tests.domain.factories import build_contract, build_mission
from tests.integration.recovery_worker import control_repository
from tests.integration.test_desktop_feedback_recovery import (
    _CheckpointFactory,
    _GapCapture,
    _public_anchor,
)
from tests.services.test_desktop_local_runner import desktop_claim_payload

OLD_GUIDANCE = "old private guidance must occur once"
NEW_GUIDANCE = "new private guidance added after the saved cursor"
MISSION = "mis-desktop-1"
EXECUTION = HarnessExecutionContext(MISSION, "wu-desktop-1", 1)


class _Model:
    def __init__(self):
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        if not any(message.role == "tool" for message in request.messages):
            return ModelResponse(tool_calls=tuple(
                ToolCall(f"call-{index}", "file_read", {"path": f"read-{index}.txt"})
                for index in range(8)))
        return ModelResponse(content="complete")


class _ModelFactory:
    recovery_manifest: ClassVar = {"provider": "guidance-recovery-test", "model": "stable-v1"}

    def __init__(self):
        self.model = _Model()

    def build(self, tools):
        return self.model


class _GuidanceCapture(_GapCapture):
    def __init__(self, gap_store, on_gap):
        super().__init__(gap_store)
        self.on_gap = on_gap
        self.model_started = None
        self.model_completed = None

    async def record(self, checkpoint, event):
        await super().record(checkpoint, event)
        if checkpoint.iteration == 1 and checkpoint.phase in {
            HarnessEventType.MODEL_STARTED, HarnessEventType.MODEL_COMPLETED,
        }:
            digest = checkpoint.next_action["resumeImageDigest"]
            with closing(self.source_store._connect()) as connection:
                row = connection.execute("SELECT checkpoint_id FROM resume_images WHERE digest=?", (digest,)).fetchone()
            image = self.source_store.load(row["checkpoint_id"], digest)
            self.gap_store.save(image)
            if checkpoint.phase == HarnessEventType.MODEL_STARTED:
                self.model_started = (image, _public_anchor(image, checkpoint))
            else:
                self.model_completed = image
        if checkpoint.phase == HarnessEventType.TOOL_STARTED and checkpoint.tool_calls == 8:
            await self.on_gap()


def _factory(root, workspace, tool, source, *, port=None):
    model_factory = _ModelFactory()
    factory = DesktopTaskHarnessFactory(model_factory, tools=[tool], workspace_root=workspace,
        recovery_state_root=root / "private", guidance_source=source,
        tool_policy=ToolExecutionPolicy.for_mode("edit", workspace),
        checkpoint_factory=_CheckpointFactory(port) if port is not None else None)
    _, context = desktop_claim_payload()
    return factory.build(context), model_factory.model


@asynccontextmanager
async def _normal_case(root, *, add_new=False, preconsumed=False):
    from app.services.desktop_runner_tools import build_desktop_runner_tools
    workspace = root / "workspace"
    workspace.mkdir()
    for index in range(8):
        (workspace / f"read-{index}.txt").write_text(f"real file {index}")
    async with control_repository(root / "control.sqlite3") as repository:
        await repository.add_contract_lineage("contract-1", "workspace-1")
        await repository.add_contract(build_contract())
        await repository.add_mission(build_mission(id=MISSION, status="RUNNING", source=MissionSource(type="manual")))
        service = MissionService(repository)
        old = await service.add_mission_guidance(MISSION, content=OLD_GUIDANCE, actor=ActorRef(type="human", id="user-1"))
        shared_ids = {old.event_id} if preconsumed else set()
        source = InProcessGuidanceSource(lambda: repository, consumed_event_ids=shared_ids, event_limit=2)

        async def on_gap():
            if add_new:
                await service.add_mission_guidance(MISSION, content=NEW_GUIDANCE, actor=ActorRef(type="human", id="user-1"))

        gap_store = ResumeImageStore(root / "captured.sqlite3")
        port = _GuidanceCapture(gap_store, on_gap)
        real_read = next(tool for tool in build_desktop_runner_tools(workspace) if tool.name == "file_read")
        calls = []

        async def read(arguments):
            calls.append(arguments["path"])
            return await real_read.handler(arguments)

        tool = replace(real_read, handler=read)
        harness, model = _factory(root, workspace, tool, source, port=port)
        port.source_store = harness._recovery.store
        result = await harness.execute(HarnessRequest("objective", "text", 60.0, cwd=workspace, execution=EXECUTION))
        assert result.sandbox.success and port.anchor is not None
        yield repository, workspace, tool, harness, model, port, calls, shared_ids


def _restore(root, workspace, tool, source, port):
    harness, model = _factory(root, workspace, tool, source)
    harness._recovery.store = port.gap_store
    resume = harness.restore_resume(port.anchor, code="objective", timeout=60.0, language="text")
    request = HarnessRequest("objective", "text", 60.0, cwd=workspace, execution=EXECUTION, resume=resume)
    return harness, model, request


def _assert_private_state(port, harness, restored_ids):
    saved = port.model_completed.guidance_state
    assert saved.after_sequence > 0 and saved.consumed_event_ids
    assert saved.injections[0].block in harness._model.injected_blocks
    assert set(saved.consumed_event_ids).issubset(restored_ids)
    assert OLD_GUIDANCE not in repr(saved) and OLD_GUIDANCE not in str(port.anchor)
    assert port.model_started[0].guidance_state.after_sequence == 0
    assert port.model_started[0].guidance_state.injections == []


@pytest.mark.asyncio
@pytest.mark.parametrize("add_new", [False, True])
async def test_actual_ledger_receipt_gap_preserves_exact_next_model_guidance(tmp_path, add_new):
    async with _normal_case(tmp_path, add_new=add_new) as case:
        repository, workspace, tool, old_harness, normal_model, port, calls, _ = case
        fresh_ids = set()
        source = InProcessGuidanceSource(lambda: repository, consumed_event_ids=fresh_ids, event_limit=2)
        fresh, model, request = _restore(tmp_path, workspace, tool, source, port)
        assert old_harness._recovery.material == fresh._recovery.material
        result = await fresh.execute(request)
        assert result.sandbox.success and len(calls) == 8
        assert model.requests[0].messages == normal_model.requests[1].messages
        assert OLD_GUIDANCE not in str(model.requests[0].messages)
        assert (NEW_GUIDANCE in str(model.requests[0].messages)) == add_new
        _assert_private_state(port, old_harness, fresh_ids)


@pytest.mark.asyncio
async def test_consumption_by_another_worker_is_still_suppressed_and_cursor_restored(tmp_path):
    async with _normal_case(tmp_path, preconsumed=True) as case:
        repository, workspace, tool, _, normal, port, calls, _ = case
        source = InProcessGuidanceSource(lambda: repository, event_limit=2)
        fresh, model, request = _restore(tmp_path, workspace, tool, source, port)
        result = await fresh.execute(request)
        assert result.sandbox.success and len(calls) == 8
        assert model.requests[0].messages == normal.requests[1].messages
        assert all(OLD_GUIDANCE not in str(message) for message in normal.requests)
        assert port.model_completed.guidance_state.injections == []
        assert port.model_completed.guidance_state.consumed_event_ids


@pytest.mark.asyncio
async def test_durable_guidance_read_failure_does_not_call_model_or_report_success(tmp_path):
    async with _normal_case(tmp_path) as case:
        _, workspace, tool, _, _, port, calls, _ = case

        class BrokenRepository:
            async def list_events(self, *args, **kwargs):
                raise RuntimeError("ledger unavailable")

        source = InProcessGuidanceSource(BrokenRepository)
        fresh, model, request = _restore(tmp_path, workspace, tool, source, port)
        result = await fresh.execute(request)
        assert not result.sandbox.success and model.requests == [] and len(calls) == 8


@pytest.mark.asyncio
async def test_indeterminate_model_checkpoint_with_unconsumed_guidance_is_refused(tmp_path):
    async with _normal_case(tmp_path) as case:
        repository, workspace, tool, _, _, port, _, _ = case
        fresh, model = _factory(tmp_path, workspace, tool, InProcessGuidanceSource(lambda: repository))
        fresh._recovery.store = port.gap_store
        with pytest.raises(ResumeImageError, match="indeterminate model call"):
            fresh.restore_resume(port.model_started[1], code="objective", timeout=60.0, language="text")
        assert model.requests == [] and fresh._model.snapshot_guidance().consumed_event_ids == []


@pytest.mark.asyncio
async def test_guidance_enabled_image_without_private_cursor_refuses_restore(tmp_path):
    async with _normal_case(tmp_path) as case:
        repository, workspace, tool, _, _, port, calls, _ = case
        saved = port.gap_store.load(port.anchor["id"], port.anchor["nextAction"]["resumeImageDigest"])
        missing = saved.model_copy(update={"guidance_state": None})
        store = ResumeImageStore(tmp_path / "missing-guidance.sqlite3")
        digest = store.save(missing)
        anchor = {**port.anchor, "nextAction": {**port.anchor["nextAction"], "resumeImageDigest": digest}}
        fresh, model = _factory(tmp_path, workspace, tool, InProcessGuidanceSource(lambda: repository))
        fresh._recovery.store = store
        with pytest.raises(ValueError, match="guidance state is missing"):
            fresh.restore_resume(anchor, code="objective", timeout=60.0, language="text")
        assert model.requests == [] and len(calls) == 8


async def _append_pagination_events(repository):
    now = datetime.now(UTC)
    actor = ActorRef(type="human", id="user-1")
    for index in range(200):
        await repository.append_event(EventEnvelope(event_id=f"mission-page-{index}", aggregate_type="mission",
            aggregate_id=MISSION, sequence=index + 2, event_type="mission.lifecycle.started",
            actor=actor, occurred_at=now, correlation_id=MISSION))
        await repository.append_event(EventEnvelope(event_id=f"unit-page-{index}", aggregate_type="work_unit",
            aggregate_id="wu-desktop-1", sequence=index + 1, event_type="work_unit.lifecycle.started",
            actor=actor, occurred_at=now, correlation_id=MISSION))
    await MissionService(repository).add_mission_guidance(MISSION, content=NEW_GUIDANCE, actor=actor)


def _events_app(repository):
    from app.api.v1.missions import get_mission_repository, router
    from app.services.auth_service import get_current_user
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_mission_repository] = lambda: repository
    app.dependency_overrides[get_current_user] = lambda: {"id": "workspace-1", "role": "admin"}
    return app


@pytest.mark.asyncio
async def test_real_http_feed_drains_mission_pages_with_full_repeated_work_unit_window(tmp_path):
    async with _normal_case(tmp_path) as case:
        repository, _, _, normal, _, _, _, _ = case
        await _append_pagination_events(repository)
        queries = []
        async def record(request):
            queries.append(int(request.url.params["afterSequence"]))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_events_app(repository)),
                                     base_url="http://control", event_hooks={"request": [record]}) as client:
            source = MissionControlGuidanceSource("http://control", http_client=client,
                consumed_event_ids=set(normal._model.snapshot_guidance().consumed_event_ids))
            model = _Model()
            from app.services.desktop_guidance import GuidanceInjectingModel
            from app.services.model_contract import Message, ModelRequest
            wrapper = GuidanceInjectingModel(model, source, mission_id=MISSION)
            wrapper.enable_recovery(EXECUTION)
            request = ModelRequest(messages=(Message(role="user", content="objective"),))
            await wrapper.complete(request)
            assert NEW_GUIDANCE in model.requests[0].messages[-1].content
            assert OLD_GUIDANCE not in str(model.requests[0].messages)
            assert wrapper.snapshot_guidance().after_sequence == 202
            assert len(wrapper.snapshot_guidance().consumed_event_ids) == 202
            await wrapper.complete(request)
            assert model.requests[-1] == request and queries == [0, 200, 202]


async def _seed_process_guidance(root):
    from tests.integration.test_recovery_process import seed
    await seed(root)
    async with control_repository(root / "control.sqlite3") as repository:
        await MissionService(repository).add_mission_guidance("mis-1", content=OLD_GUIDANCE,
            actor=ActorRef(type="human", id="user-1"))


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["guidance_checkpoint", "guidance_receipt"])
async def test_real_process_kill_with_default_guidance_source_preserves_next_request(tmp_path, boundary):
    from tests.integration.test_recovery_process import (
        kill_at_boundary,
        restart,
        verify_completed_control_state,
    )
    baseline, recovered = tmp_path / "baseline", tmp_path / "recovered"
    baseline.mkdir()
    recovered.mkdir()
    await _seed_process_guidance(baseline)
    await _seed_process_guidance(recovered)
    code, stdout, stderr = await restart(baseline, "guidance_baseline")
    assert code == 0, (stdout, stderr)
    await kill_at_boundary(recovered, boundary)
    code, stdout, stderr = await restart(recovered, "guidance_resume")
    assert code == 0, (stdout, stderr)
    normal = (baseline / "guidance-messages.jsonl").read_text()
    restored = (recovered / "guidance-messages.jsonl").read_text()
    assert normal == restored and normal.count(OLD_GUIDANCE) == 1
    assert normal.count("new guidance after saved cursor") == 1
    assert (recovered / "model-calls").read_text().splitlines() == ["0", "2"]
    assert (recovered / "workspace" / "output").read_text() == "one\ntwo\n"
    await verify_completed_control_state(recovered)
