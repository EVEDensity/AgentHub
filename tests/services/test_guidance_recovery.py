"""Guidance cursor identity, pagination and strict recovery boundaries."""
from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from app.services.desktop_guidance import (
    GuidanceInjectingModel,
    InProcessGuidanceSource,
    MissionControlGuidanceSource,
)
from app.services.guidance_recovery import MAX_GUIDANCE_EVENTS, GuidanceResumeState
from app.services.harness_checkpoint import HarnessExecutionContext
from app.services.harness_service import FunctionCallingHarness, HarnessRequest
from app.services.model_contract import (
    Message,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
)
from app.services.recovery_lock import RecoveryExecutionLock
from app.services.recovery_store import runner_state_directory

EXECUTION = HarnessExecutionContext("mission", "unit", 1)
REQUEST = ModelRequest(messages=(Message(role="user", content="objective"),))


def _event(sequence, content=None):
    return {"event_id": f"event-{sequence}", "aggregate_type": "mission",
            "aggregate_id": "mission", "sequence": sequence,
            "event_type": "mission.guidance.added" if content else "mission.lifecycle.started",
            "payload": {"content": content} if content else {}}


class _Model:
    def __init__(self):
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        return ModelResponse(content="done")


class _Repository:
    def __init__(self, events):
        self.events = events
        self.after = []

    async def list_events(self, mission_id, *, after_sequence, limit):
        assert mission_id == "mission"
        self.after.append(after_sequence)
        return [event for event in self.events if event["sequence"] > after_sequence][:limit]


def _wrapper(source):
    model = _Model()
    wrapper = GuidanceInjectingModel(model, source, mission_id="mission")
    wrapper.enable_recovery(EXECUTION)
    return wrapper, model


@pytest.mark.asyncio
async def test_pagination_advances_over_other_worker_consumption_and_records_exact_blocks():
    repository = _Repository([_event(1, "another worker"), _event(2), _event(3, "first"), _event(4), _event(5, "second")])
    shared_ids = {"event-1"}
    source = InProcessGuidanceSource(lambda: repository, event_limit=2, consumed_event_ids=shared_ids)
    wrapper, model = _wrapper(source)
    await wrapper.complete(REQUEST)
    saved = wrapper.snapshot_guidance()
    assert repository.after == [0, 2, 4]
    assert saved.after_sequence == 5 and len(saved.consumed_event_ids) == 5
    assert saved.injections[0].event_ids == ["event-3", "event-5"]
    assert saved.injections[0].block == model.requests[0].messages[-1].content
    assert "another worker" not in str(model.requests[0].messages)
    await wrapper.complete(REQUEST)
    assert repository.after[-1] == 5 and model.requests[-1] == REQUEST
    fresh, fresh_model = _wrapper(InProcessGuidanceSource(lambda: repository, event_limit=2))
    fresh.restore_guidance(saved)
    await fresh.complete(REQUEST)
    assert fresh_model.requests == [REQUEST] and fresh.injected_blocks == wrapper.injected_blocks


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"aggregate_id": "another-mission"}, {"aggregate_type": "unknown"},
    {"sequence": "1"}, {"event_id": ""}, {"payload": {"content": " "}},
])
async def test_malformed_or_foreign_events_cannot_consume_or_call_model(changes):
    repository = _Repository([{**_event(1, "instruction"), **changes}])
    # The strict source must see the invalid raw sequence, not a test filter's comparison.
    async def read_events(*args, **kwargs):
        return repository.events
    source = InProcessGuidanceSource(lambda: repository)
    source.read_events = read_events
    wrapper, model = _wrapper(source)
    with pytest.raises(ValueError):
        await wrapper.complete(REQUEST)
    assert model.requests == [] and source._consumed_event_ids == set()
    assert wrapper.snapshot_guidance().after_sequence == 0


@pytest.mark.asyncio
async def test_later_page_failure_does_not_partially_consume_guidance():
    repository = _Repository([_event(1, "first"), _event(2)])
    source = InProcessGuidanceSource(lambda: repository, event_limit=2)
    async def read_events(mission_id, *, after_sequence):
        if after_sequence:
            raise RuntimeError("second page unavailable")
        return repository.events
    source.read_events = read_events
    wrapper, model = _wrapper(source)
    with pytest.raises(RuntimeError, match="second page"):
        await wrapper.complete(REQUEST)
    assert model.requests == [] and source._consumed_event_ids == set()
    assert wrapper.snapshot_guidance().consumed_event_ids == []


@pytest.mark.asyncio
async def test_http_reader_uses_saved_mission_cursor_and_ignores_work_unit_window():
    queries = []
    def handler(request):
        cursor = int(request.url.params["afterSequence"])
        queries.append(cursor)
        unit_event = {"aggregate_type": "work_unit", "sequence": 1000}
        events = [_event(1, "private instruction")] if cursor == 0 else []
        return httpx.Response(200, json={"events": [unit_event, *events]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        wrapper, model = _wrapper(MissionControlGuidanceSource("http://control", http_client=client))
        await wrapper.complete(REQUEST)
        saved = wrapper.snapshot_guidance()
        fresh, fresh_model = _wrapper(MissionControlGuidanceSource("http://control", http_client=client))
        fresh.restore_guidance(saved)
        await fresh.complete(REQUEST)
        assert queries == [0, 1] and fresh_model.requests == [REQUEST]
        assert "private instruction" in model.requests[0].messages[-1].content


def test_missing_foreign_or_inconsistent_private_state_is_refused():
    wrapper, _ = _wrapper(InProcessGuidanceSource(lambda: _Repository([])))
    with pytest.raises(ValueError, match="missing"):
        wrapper.restore_guidance(None)
    with pytest.raises(ValueError, match="another execution"):
        wrapper.restore_guidance(GuidanceResumeState(mission_id="other", work_unit_id="unit", attempt=1))
    with pytest.raises(ValidationError, match="sequence and consumed identities"):
        GuidanceResumeState(mission_id="mission", work_unit_id="unit", attempt=1, after_sequence=1)


@pytest.mark.asyncio
async def test_actual_guidance_bodies_are_private_and_validated():
    content = "private instruction"
    wrapper, _ = _wrapper(InProcessGuidanceSource(lambda: _Repository([_event(1, content)])))
    await wrapper.complete(REQUEST)
    saved = wrapper.snapshot_guidance()
    assert saved.injections[0].contents == [content] and "private instruction" not in repr(saved)
    damaged = saved.model_dump()
    damaged["injections"][0]["block"] = "synthetic guidance"
    with pytest.raises(ValidationError, match="block differs"):
        GuidanceResumeState.model_validate(damaged)


@pytest.mark.asyncio
@pytest.mark.parametrize("events,error", [
    ([_event(index + 1) for index in range(MAX_GUIDANCE_EVENTS + 1)], "4096 events"),
    ([_event(1, "private oversize " + "x" * (256 * 1024))], "512 KiB"),
])
async def test_guidance_history_and_body_limits_refuse_before_claiming_or_model_call(events, error):
    source = InProcessGuidanceSource(lambda: _Repository(events))
    wrapper, model = _wrapper(source)
    with pytest.raises(ValueError, match=error):
        await wrapper.complete(REQUEST)
    assert model.requests == [] and source._consumed_event_ids == set()
    assert wrapper.snapshot_guidance().consumed_event_ids == []


def _transient_failure():
    response = httpx.Response(503, request=httpx.Request("POST", "http://model"))
    return httpx.HTTPStatusError("transient model failure", request=response.request, response=response)


class _RetryModel:
    def __init__(self):
        self.prompts = []

    async def complete(self, request):
        self.prompts.append(str(request.messages))
        if len(self.prompts) == 1:
            raise _transient_failure()
        return ModelResponse(content="done")


class _LegacyRetryModel(_RetryModel):
    async def complete(self, request, tool_results):
        self.prompts.append(request.code)
        if len(self.prompts) == 1:
            raise _transient_failure()
        return ModelResponse(content="done")


@pytest.mark.asyncio
@pytest.mark.parametrize("model_type", [_RetryModel, _LegacyRetryModel])
async def test_same_model_round_retry_retains_guidance_in_canonical_and_legacy_adapters(monkeypatch, model_type):
    monkeypatch.setattr("app.services.harness_service.MODEL_RETRY_BACKOFF_SECONDS", 0)
    repository = _Repository([_event(1, "retry instruction")])
    model = model_type()
    wrapper = GuidanceInjectingModel(model, InProcessGuidanceSource(lambda: repository), mission_id="mission")
    wrapper.enable_recovery(EXECUTION)
    harness = FunctionCallingHarness(wrapper, [])
    request = HarnessRequest("objective", "text", 60)
    response = await harness._complete_with_retry(request, ())
    assert response.content == "done" and model.prompts[0] == model.prompts[1]
    assert "retry instruction" in model.prompts[1] and repository.after == [0]
    assert len(wrapper.snapshot_guidance().injections) == 1
    await harness._complete_with_retry(request, ())
    assert "retry instruction" not in model.prompts[-1] and repository.after == [0, 1]


class _StreamRetryModel(_RetryModel):
    async def stream(self, request):
        self.prompts.append(str(request.messages))
        if len(self.prompts) == 1:
            raise _transient_failure()
        yield ModelStreamEvent(kind="text_delta", text="done")
        yield ModelStreamEvent(kind="completed")


@pytest.mark.asyncio
async def test_pre_stream_retry_retains_same_guidance_without_an_extra_consumption(monkeypatch):
    monkeypatch.setattr("app.services.harness_service.MODEL_RETRY_BACKOFF_SECONDS", 0)
    repository = _Repository([_event(1, "stream retry instruction")])
    model = _StreamRetryModel()
    wrapper = GuidanceInjectingModel(model, InProcessGuidanceSource(lambda: repository), mission_id="mission")
    wrapper.enable_recovery(EXECUTION)
    harness = FunctionCallingHarness(wrapper, [])
    request = HarnessRequest("objective", "text", 60)
    response = await harness._stream_with_retry(request, ())
    assert response.content == "done" and model.prompts[0] == model.prompts[1]
    assert "stream retry instruction" in model.prompts[1] and repository.after == [0]
    await harness._stream_with_retry(request, ())
    assert "stream retry instruction" not in model.prompts[-1] and repository.after == [0, 1]


@pytest.mark.parametrize("component", ["ResumeImageStore", "SQLiteToolReceiptStore"])
def test_actual_factory_constructor_error_releases_attempt_lock_with_traceback_retained(tmp_path, monkeypatch, component):
    from app.services.runner.model import DesktopTaskHarnessFactory
    from app.services.tools.policy import ToolExecutionPolicy
    from tests.integration.recovery_worker import ProcessModelFactory
    from tests.services.test_desktop_local_runner import desktop_claim_payload
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    factory = DesktopTaskHarnessFactory(ProcessModelFactory(tmp_path), tools=[], workspace_root=workspace,
        recovery_state_root=tmp_path / "private", tool_policy=ToolExecutionPolicy.for_mode("edit", workspace))
    _, context = desktop_claim_payload()
    def failed_store(*args, **kwargs):
        raise OSError("controlled private store outage")
    monkeypatch.setattr(f"app.services.runner.recovery.{component}", failed_store)
    with pytest.raises(OSError, match="controlled private store outage") as retained:
        factory.build(context)
    assert retained.value.__traceback__ is not None
    directory = runner_state_directory(workspace, tmp_path / "private")
    lock = RecoveryExecutionLock(directory, "mis-desktop-1/wu-desktop-1/1")
    try:
        assert retained.value.__traceback__ is not None and not lock.handle.closed
    finally:
        lock.close()
