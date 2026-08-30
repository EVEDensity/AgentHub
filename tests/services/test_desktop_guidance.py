"""P1-1 run-time guidance: service append + runner prompt injection."""

from __future__ import annotations

import asyncio
import unittest
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.domain import ActorRef
from app.services.desktop_guidance import (
    GUIDANCE_EVENT_TYPE,
    GuidanceInjectingModel,
    InMemoryGuidanceSource,
    MissionControlGuidanceSource,
    format_guidance_block,
)
from app.services.desktop_local_runner import DesktopTaskHarnessFactory
from app.services.harness_service import (
    FunctionCall,
    FunctionTool,
    HarnessRequest,
    ModelResponse,
    ModelUsage,
)
from app.services.mission_service import MissionService

MISSION_ID = "mis-guidance-1"


class _FakeRepository:
    """Minimal mission aggregate for the guidance event append."""

    def __init__(self, mission: Any) -> None:
        self.mission = mission
        self.events: list[Any] = []

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def get_mission_for_update(self, mission_id: str) -> Any:
        if self.mission is not None and self.mission.id == mission_id:
            return self.mission
        return None

    async def get_last_event_sequence(
        self,
        aggregate_id: str,
        *,
        aggregate_type: str = "mission",
    ) -> int:
        del aggregate_id, aggregate_type
        return len(self.events)

    async def append_event(self, event: Any) -> None:
        self.events.append(event)


class _FakeMission:
    id = MISSION_ID


class _RecordingModel:
    """Records every prompt and returns a final answer on the last turn."""

    def __init__(self, tool_call_turns: int = 1) -> None:
        self._tool_call_turns = tool_call_turns
        self.prompts: list[str] = []

    async def complete(
        self,
        request: Any,
        tool_results: tuple[Any, ...],
        *,
        tools_enabled: bool = True,
    ) -> ModelResponse:
        del tools_enabled
        self.prompts.append(request.code)
        if len(self.prompts) <= self._tool_call_turns:
            return ModelResponse(
                tool_calls=(
                    FunctionCall(
                        id="call-1",
                        name="noop",
                        arguments={},
                    ),
                ),
                usage=ModelUsage(prompt_tokens=1, completion_tokens=1),
            )
        return ModelResponse(
            content="完成",
            usage=ModelUsage(prompt_tokens=1, completion_tokens=1),
        )


def _noop_tool() -> FunctionTool:
    async def handler(_arguments: Any) -> str:
        return "ok"

    return FunctionTool(
        name="noop",
        description="no-op",
        parameters={"type": "object", "properties": {}, "required": []},
        validate_arguments=lambda arguments: dict(arguments),
        handler=handler,
    )


class _DelayedGuidanceSource:
    """Returns guidance only after *reveal_after* fetches (mid-run add)."""

    def __init__(self, mission_id: str, guidance: str, *, reveal_after: int) -> None:
        self._mission_id = mission_id
        self._guidance = guidance
        self._reveal_after = reveal_after
        self._calls = 0
        self.consumed = False

    async def pending_guidance(self, mission_id: str) -> tuple[str, ...]:
        assert mission_id == self._mission_id
        self._calls += 1
        if self.consumed or self._calls < self._reveal_after:
            return ()
        self.consumed = True
        return (self._guidance,)


def _actor() -> ActorRef:
    return ActorRef(type="human", id="user-1")


class GuidanceServiceTests(unittest.TestCase):
    def test_add_mission_guidance_appends_ledger_event(self) -> None:
        repository = _FakeRepository(_FakeMission())
        service = MissionService(repository)

        event = asyncio.run(
            service.add_mission_guidance(
                MISSION_ID,
                content="  折扣改为 8 折  ",
                actor=_actor(),
            )
        )

        self.assertEqual(len(repository.events), 1)
        self.assertIs(repository.events[0], event)
        self.assertEqual(event.event_type, GUIDANCE_EVENT_TYPE)
        self.assertEqual(event.aggregate_id, MISSION_ID)
        self.assertEqual(event.sequence, 1)
        self.assertEqual(event.payload["content"], "折扣改为 8 折")

    def test_add_mission_guidance_rejects_blank_content(self) -> None:
        repository = _FakeRepository(_FakeMission())
        service = MissionService(repository)

        with self.assertRaises(ValueError):
            asyncio.run(
                service.add_mission_guidance(
                    MISSION_ID,
                    content="   ",
                    actor=_actor(),
                )
            )
        self.assertEqual(repository.events, [])

    def test_add_mission_guidance_unknown_mission_fails(self) -> None:
        repository = _FakeRepository(None)
        service = MissionService(repository)

        with self.assertRaises(LookupError):
            asyncio.run(
                service.add_mission_guidance(
                    "mis-missing",
                    content="hello",
                    actor=_actor(),
                )
            )


class GuidanceInjectingModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_guidance_added_before_run_is_injected_exactly_once(self) -> None:
        model = _RecordingModel(tool_call_turns=1)
        source = InMemoryGuidanceSource({MISSION_ID: ("输出用中文",)})
        wrapper = GuidanceInjectingModel(
            model,
            source,
            mission_id=MISSION_ID,
        )
        request = HarnessRequest(code="原始 objective", language="text", timeout=30)
        first = await wrapper.complete(request, ())
        second = await wrapper.complete(request, ("tool",))

        self.assertEqual(len(model.prompts), 2)
        # First model call already carries the guidance block.
        self.assertIn("[用户补充指导 · 运行中注入]", model.prompts[0])
        self.assertIn("- 输出用中文", model.prompts[0])
        self.assertIn("原始 objective", model.prompts[0])
        # One-time consumption: the second call never repeats it.
        self.assertNotIn("输出用中文", model.prompts[1])
        self.assertEqual(
            wrapper.injected_blocks,
            [format_guidance_block(("输出用中文",))],
        )
        self.assertTrue(first.tool_calls)
        self.assertEqual(second.content, "完成")

    async def test_mid_run_guidance_lands_before_next_model_call(self) -> None:
        model = _RecordingModel(tool_call_turns=2)
        source = _DelayedGuidanceSource(
            MISSION_ID, "第 2 轮补充：文件名改为 report.txt", reveal_after=2
        )
        wrapper = GuidanceInjectingModel(model, source, mission_id=MISSION_ID)
        request = HarnessRequest(code="原始 objective", language="text", timeout=30)

        await wrapper.complete(request, ())
        await wrapper.complete(request, ())
        await wrapper.complete(request, ())

        self.assertEqual(len(model.prompts), 3)
        self.assertNotIn("report.txt", model.prompts[0])
        # Guidance surfaced on the 2nd fetch lands before the 2nd model call.
        self.assertIn("第 2 轮补充：文件名改为 report.txt", model.prompts[1])
        # Injected once, never replayed.
        self.assertNotIn("report.txt", model.prompts[2])
        self.assertEqual(
            [prompt.count("report.txt") for prompt in model.prompts],
            [0, 1, 0],
        )

    async def test_full_function_calling_harness_receives_injected_prompt(self) -> None:
        """End-to-end inside FunctionCallingHarness: guidance reaches the model."""
        from app.services.harness_service import FunctionCallingHarness

        model = _RecordingModel(tool_call_turns=1)
        source = InMemoryGuidanceSource({MISSION_ID: ("总结不超过 10 个字",)})
        wrapper = GuidanceInjectingModel(model, source, mission_id=MISSION_ID)
        harness = FunctionCallingHarness(wrapper, [_noop_tool()], max_iterations=4)
        result = await harness.execute(
            HarnessRequest(code="原始 objective", language="text", timeout=30)
        )

        self.assertTrue(result.sandbox.success)
        self.assertIn("总结不超过 10 个字", model.prompts[0])
        self.assertNotIn("总结不超过 10 个字", model.prompts[1])


class DesktopTaskHarnessFactoryGuidanceTests(unittest.TestCase):
    def test_harness_factory_wraps_model_with_guidance_injection(self) -> None:
        class _Factory:
            def build(self, tools: Any) -> Any:
                del tools
                return _RecordingModel()

        source = InMemoryGuidanceSource({})
        factory = DesktopTaskHarnessFactory(
            _Factory(),
            tools=[_noop_tool()],
            guidance_source=source,
        )
        context = {
            "mission": {"id": MISSION_ID},
            "workUnit": {"id": "wu-1", "attempt": 1, "lease": {"id": "lease-1"}},
        }
        harness = factory.build(context)
        self.assertIsInstance(harness._model, GuidanceInjectingModel)
        self.assertEqual(harness._model._mission_id, MISSION_ID)

    def test_harness_factory_without_source_keeps_plain_model(self) -> None:
        class _Factory:
            def build(self, tools: Any) -> Any:
                del tools
                return _RecordingModel()

        factory = DesktopTaskHarnessFactory(_Factory(), tools=[_noop_tool()])
        context = {
            "mission": {"id": MISSION_ID},
            "workUnit": {"id": "wu-1", "attempt": 1, "lease": {"id": "lease-1"}},
        }
        harness = factory.build(context)
        self.assertNotIsInstance(harness._model, GuidanceInjectingModel)


class MissionControlGuidanceSourceTests(unittest.IsolatedAsyncioTestCase):
    captured_url: str = ""

    def _client(self, events: list[dict[str, Any]]) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.captured_url = str(request.url)
            return httpx.Response(200, json={"events": events})

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def test_filters_guidance_events_and_consumes_once(self) -> None:
        events = [
            {
                "event_id": "evt-1",
                "event_type": "mission.lifecycle.started",
                "payload": {},
            },
            {
                "event_id": "evt-2",
                "event_type": GUIDANCE_EVENT_TYPE,
                "payload": {"content": "把标题改成中文"},
            },
        ]
        source = MissionControlGuidanceSource(
            "http://127.0.0.1:28000",
            access_token="token-1",
            http_client=self._client(events),
        )
        pending = await source.pending_guidance(MISSION_ID)
        self.assertEqual(pending, ("把标题改成中文",))
        self.assertIn("/api/v1/missions/mis-guidance-1/events", self.captured_url)
        self.assertIn("afterSequence=0", self.captured_url)
        # Consumption: the same event is never returned twice.
        again = await source.pending_guidance(MISSION_ID)
        self.assertEqual(again, ())

    async def test_fetch_failure_degrades_to_no_guidance(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"detail": "boom"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        source = MissionControlGuidanceSource(
            "http://127.0.0.1:28000",
            access_token="token-1",
            http_client=client,
        )
        pending = await source.pending_guidance(MISSION_ID)
        self.assertEqual(pending, ())


if __name__ == "__main__":
    unittest.main()
