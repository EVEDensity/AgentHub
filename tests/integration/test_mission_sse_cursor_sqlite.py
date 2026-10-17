"""Real SQLite regressions for Mission-scoped, mixed-aggregate SSE cursors."""
from __future__ import annotations

import json
import unittest
import uuid

import httpx

from app.domain import ActorRef, MissionSource
from tests.domain.factories import build_contract
from tests.integration.mission_chat_driver import RUNNER, WORKSPACE_ID
from tests.integration.mission_chat_http_fixture import build_fixture_app

HUMAN = {"Authorization": "Bearer chat-browser-human"}
ACTOR = ActorRef(type="human", id=WORKSPACE_ID)


class MissionSSECursorSQLiteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = build_fixture_app()
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.addAsyncCleanup(self.lifespan.__aexit__, None, None, None)
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://mission-control.test",
        )
        self.addAsyncCleanup(self.http.aclose)
        self.driver = self.app.state.driver
        response = await self.http.post("/api/v1/chat/mission", headers=HUMAN,
                                        json={"message": "Cursor regression", "workspaceId": WORKSPACE_ID})
        self.assertEqual(response.status_code, 202, response.text)
        self.mission_id = response.json()["missionId"]

    async def guidance(self, text="Current Mission guidance"):
        return await self.driver.service.add_mission_guidance(self.mission_id, content=text, actor=ACTOR)

    async def prepare_mixed_aggregates(self):
        await self.driver.advance(self.mission_id, "start", 60)
        for _ in range(3):
            await self.guidance()
        mission_events = await self.driver.repository.list_events(self.mission_id)
        self.assertEqual(mission_events[-1].sequence, 5)
        unit = await self.driver._unit(self.mission_id)
        cursor = None
        for _ in range(17):
            cursor = await self.driver.service.publish_streaming_event(
                self.mission_id, unit.id, event_id="evt-cursor-" + uuid.uuid4().hex,
                event_type="harness.assistant.delta", text="Fixture observation",
                attempt=unit.attempt, lease_id=unit.lease.id, runner_id=RUNNER.id, actor=RUNNER,
            )
        self.assertEqual(cursor["sequence"], 20)
        return mission_events[-1], cursor

    async def stream(self, cursor, *, query=False):
        headers = dict(HUMAN)
        parameters = {"maxSeconds": 0.01}
        if query:
            parameters["after_event_id"] = cursor
        else:
            headers["Last-Event-ID"] = cursor
        response = await self.http.get(f"/api/v1/missions/{self.mission_id}/events/stream",
                                       headers=headers, params=parameters)
        self.assertEqual(response.status_code, 200, response.text)
        return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]

    @staticmethod
    def ids(events):
        return {event["eventId"] for event in events}

    async def assert_read_did_not_change_state(self, before):
        self.assertEqual(await self.driver.snapshot(self.mission_id), before)
        self.assertEqual(before["evidence"], [])

    async def test_work_unit_sequence_twenty_cannot_skip_later_mission_sequence_six(self):
        _, unit_cursor = await self.prepare_mixed_aggregates()
        new_event = await self.guidance("Mission event six must remain visible")
        self.assertEqual(new_event.sequence, 6)
        before = await self.driver.snapshot(self.mission_id)
        for query in (False, True):
            with self.subTest(query=query):
                delivered = self.ids(await self.stream(unit_cursor["event_id"], query=query))
                self.assertIn(new_event.event_id, delivered)
                self.assertIn(unit_cursor["event_id"], delivered)
        await self.assert_read_did_not_change_state(before)

    async def test_normal_current_mission_cursor_excludes_older_mission_events(self):
        mission_cursor, unit_cursor = await self.prepare_mixed_aggregates()
        new_event = await self.guidance("Mission event after the saved Mission cursor")
        for query in (False, True):
            with self.subTest(query=query):
                delivered = self.ids(await self.stream(mission_cursor.event_id, query=query))
                self.assertIn(new_event.event_id, delivered)
                self.assertNotIn(mission_cursor.event_id, delivered)
                self.assertIn(unit_cursor["event_id"], delivered)

    async def test_foreign_mission_cursor_cannot_suppress_or_disclose_current_stream(self):
        foreign_id = "mis-cursor-foreign"
        foreign_actor = ActorRef(type="human", id="workspace-chat-foreign")
        service = self.driver.service
        await service.create_mission(
            mission_id=foreign_id, workspace_id=foreign_actor.id, title="Foreign Mission",
            objective="Private foreign objective", source=MissionSource(type="manual"),
            contract=build_contract(id="contract-cursor-foreign"), actor=foreign_actor,
        )
        await service.start_mission(foreign_id, actor=foreign_actor)
        foreign_event = None
        for _ in range(20):
            foreign_event = await service.add_mission_guidance(
                foreign_id, content="Private foreign guidance", actor=foreign_actor,
            )
        current = await self.driver.repository.list_events(self.mission_id)
        before = await self.driver.snapshot(self.mission_id)
        for query in (False, True):
            with self.subTest(query=query):
                delivered = self.ids(await self.stream(foreign_event.event_id, query=query))
                self.assertTrue({event.event_id for event in current} <= delivered)
                self.assertNotIn(foreign_event.event_id, delivered)
        await self.assert_read_did_not_change_state(before)

    async def test_unknown_event_cursor_safely_replays_authorized_mission(self):
        current = await self.driver.repository.list_events(self.mission_id)
        current += await self.driver.repository.list_work_unit_events(self.mission_id)
        for query in (False, True):
            with self.subTest(query=query):
                self.assertEqual(self.ids(await self.stream("evt-missing", query=query)),
                                 {event.event_id for event in current})

    async def test_repository_optional_scope_preserves_legacy_lookup_and_fences_resume(self):
        mission_cursor, unit_cursor = await self.prepare_mixed_aggregates()
        repository = self.driver.repository
        self.assertEqual(await repository.event_sequence(unit_cursor["event_id"]), 20)
        self.assertEqual(await repository.event_sequence(unit_cursor["event_id"], mission_id=self.mission_id), 0)
        self.assertEqual(await repository.event_sequence(mission_cursor.event_id, mission_id=self.mission_id), 5)
        self.assertEqual(await repository.event_sequence(mission_cursor.event_id, mission_id="mis-other"), 0)
        self.assertEqual(await repository.event_sequence("evt-missing", mission_id=self.mission_id), 0)

    async def test_sse_headers_prevent_proxy_compression_from_buffering_live_frames(self):
        response = await self.http.get(
            f"/api/v1/missions/{self.mission_id}/events/stream", headers=HUMAN,
            params={"maxSeconds": 0.01},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-cache, no-transform")
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
