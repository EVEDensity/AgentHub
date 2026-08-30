"""P3-1c/2b: controller-level guidance ledger + in-process guidance source."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import Any

import httpx

from app.domain import ActorRef, EventEnvelope
from app.services.desktop_guidance import (
    GUIDANCE_EVENT_TYPE,
    InProcessGuidanceSource,
    MissionControlGuidanceSource,
)
from app.services.runner.controller import DesktopLocalRunnerController
from app.services.runner.settings import INPROCESS_GUIDANCE_ENV

MISSION_ID = "mis-guidance-inprocess-1"
RUNNER_USER_ID = "user-1"
WORKSPACE_ID = "local-admin"


def _settings(**overrides: Any):
    from app.services.runner.settings import DesktopLocalRunnerSettings

    base: dict[str, Any] = {
        "enabled": True,
        "base_url": "http://127.0.0.1:28000",
        "admin_name": "admin",
        "admin_password": "admin123",
        "token": "token-1",
        "token_file": None,
        "user_id": RUNNER_USER_ID,
        "workspace_id": WORKSPACE_ID,
        "workspace_root": None,
        "model_name": None,
        "max_iterations": 8,
        "max_tool_calls": 32,
        "max_total_tokens": 200_000,
        "timeout_seconds": 300.0,
        "lease_seconds": 300,
        "idle_delay_seconds": 0.01,
        "max_delay_seconds": 0.05,
        "derivation_interval_seconds": 0.01,
        "verify_enabled": False,
        "verify_interval_seconds": 0.01,
    }
    base.update(overrides)
    return DesktopLocalRunnerSettings(**base)


def _guidance_event(
    event_id: str,
    content: str,
    *,
    sequence: int = 1,
) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        aggregate_type="mission",
        aggregate_id=MISSION_ID,
        sequence=sequence,
        event_type=GUIDANCE_EVENT_TYPE,
        actor=ActorRef(type="human", id="user-1"),
        occurred_at=datetime.now(timezone.utc),
        correlation_id=MISSION_ID,
        payload={"content": content},
        schema_version=1,
    )


def _lifecycle_event(event_id: str, *, sequence: int = 1) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        aggregate_type="mission",
        aggregate_id=MISSION_ID,
        sequence=sequence,
        event_type="mission.lifecycle.started",
        actor=ActorRef(type="human", id="user-1"),
        occurred_at=datetime.now(timezone.utc),
        correlation_id=MISSION_ID,
        payload={},
        schema_version=1,
    )


class FakeMissionRepository:
    def __init__(self, events: list[EventEnvelope]) -> None:
        self.events = events

    async def list_events(
        self,
        mission_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[EventEnvelope]:
        del after_sequence, limit
        return list(self.events)


class InProcessGuidanceSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_repository_events_and_consumes_once(self) -> None:
        repository = FakeMissionRepository(
            [
                _lifecycle_event("evt-0", sequence=1),
                _guidance_event("evt-1", "  输出用中文  ", sequence=2),
                _guidance_event("evt-2", "标题改成中文", sequence=3),
            ]
        )
        source = InProcessGuidanceSource(lambda: repository)

        pending = await source.pending_guidance(MISSION_ID)

        self.assertEqual(pending, ("输出用中文", "标题改成中文"))
        # Consumption: the same events are never returned twice.
        again = await source.pending_guidance(MISSION_ID)
        self.assertEqual(again, ())

    async def test_repository_failure_degrades_to_no_guidance(self) -> None:
        class BrokenRepository:
            async def list_events(self, *args: Any, **kwargs: Any) -> list[Any]:
                raise RuntimeError("repository down")

        source = InProcessGuidanceSource(BrokenRepository)
        self.assertEqual(await source.pending_guidance(MISSION_ID), ())

    async def test_shared_ledger_dedupes_across_sources(self) -> None:
        """P3-1c: N sources sharing one ledger never double-inject."""
        ledger: set[str] = set()
        first = InProcessGuidanceSource(
            lambda: FakeMissionRepository([_guidance_event("evt-1", "指导一")]),
            consumed_event_ids=ledger,
        )
        second = InProcessGuidanceSource(
            lambda: FakeMissionRepository([_guidance_event("evt-1", "指导一")]),
            consumed_event_ids=ledger,
        )

        self.assertEqual(await first.pending_guidance(MISSION_ID), ("指导一",))
        self.assertEqual(await second.pending_guidance(MISSION_ID), ())

        # The HTTP source joins the same controller-level ledger.
        def handler(_request: httpx.Request) -> httpx.Response:
            payload = {
                "events": [
                    {
                        "event_id": "evt-1",
                        "event_type": GUIDANCE_EVENT_TYPE,
                        "payload": {"content": "指导一"},
                    }
                ]
            }
            return httpx.Response(200, json=payload)

        http_source = MissionControlGuidanceSource(
            "http://127.0.0.1:28000",
            access_token="token-1",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            consumed_event_ids=ledger,
        )
        self.assertEqual(await http_source.pending_guidance(MISSION_ID), ())


class ControllerDefaultGuidanceSourceTests(unittest.IsolatedAsyncioTestCase):
    def _controller(self) -> DesktopLocalRunnerController:
        return DesktopLocalRunnerController(
            _settings(),
            control=_IdleControl(),
        )

    async def test_default_prefers_in_process_source_with_shared_ledger(self) -> None:
        controller = self._controller()
        source = controller._build_default_guidance_source(access_token="token-1")

        self.assertIsInstance(source, InProcessGuidanceSource)
        # P3-1c: the source consumes through the controller-level ledger.
        self.assertIs(source._consumed_event_ids, controller._guidance_ledger)

    async def test_inprocess_env_zero_falls_back_to_http_source(self) -> None:
        controller = self._controller()
        controller._http_client = httpx.AsyncClient()
        try:
            import os

            previous = os.environ.get(INPROCESS_GUIDANCE_ENV)
            os.environ[INPROCESS_GUIDANCE_ENV] = "0"
            try:
                source = controller._build_default_guidance_source(
                    access_token="token-1"
                )
            finally:
                if previous is None:
                    os.environ.pop(INPROCESS_GUIDANCE_ENV, None)
                else:
                    os.environ[INPROCESS_GUIDANCE_ENV] = previous

            self.assertIsInstance(source, MissionControlGuidanceSource)
            self.assertIs(source._consumed_event_ids, controller._guidance_ledger)
        finally:
            await controller._http_client.aclose()

    async def test_two_default_sources_share_one_ledger(self) -> None:
        """The multi-worker path: each worker sees the same consumed set."""
        controller = self._controller()
        first = controller._build_default_guidance_source(access_token="token-1")
        second = controller._build_default_guidance_source(access_token="token-1")

        self.assertIsInstance(first, InProcessGuidanceSource)
        self.assertIs(first._consumed_event_ids, second._consumed_event_ids)

        # A ledger entry consumed elsewhere suppresses re-injection.
        repository = FakeMissionRepository([_guidance_event("evt-preconsumed", "指导")])
        second._repository_factory = lambda: repository
        controller._guidance_ledger.add("evt-preconsumed")
        self.assertEqual(await second.pending_guidance(MISSION_ID), ())


class _IdleControl:
    async def claim_ready_work_unit(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"claimStatus": "idle", "workUnit": None}


if __name__ == "__main__":
    unittest.main()
