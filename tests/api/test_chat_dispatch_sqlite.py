from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import UTC
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.services.agent_binding_service import AgentBinding
from tests.api.test_chat_mission import _FakeAgentBindingResolver, build_chat_app


class TestChatDispatchSQLite(unittest.IsolatedAsyncioTestCase):
    """Exercise real catalog, durable WorkUnit, claim SQL and lease fencing."""

    async def test_direct_chat_dispatch_failure_leaves_no_partial_mission(self):
        from app.domain import SessionEventType
        from app.services.mission_service import MissionService

        app = self.real_confirmation_app()
        with (
            patch("app.db.session.afetch_all", self.connection.fetch),
            patch.object(MissionService, "create_chat_work_unit", side_effect=RuntimeError("dispatch unavailable")),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/v1/chat/mission", json={
                    "message": "@selected-agent inspect this", "workspaceId": "ws-chat",
                })
        self.assertEqual(response.status_code, 503, response.text)
        for table in ("missions", "mission_contracts", "work_units", "mission_events"):
            self.assertEqual(await self.connection.fetchval(f"SELECT COUNT(*) FROM {table}"), 0)
        self.assertEqual(await self.connection.fetchval(
            "SELECT COUNT(*) FROM session_events WHERE event_type=$1", SessionEventType.MISSION_CREATED.value,
        ), 0)

    async def test_missing_chat_participant_metadata_is_rejected_cleanly(self):
        mission = await self.seed_mission(metadata=None)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            await self.service.create_chat_work_unit(mission.id, workspace_id="ws-chat")
        self.assertFalse(await self.repository.list_work_units(mission.id))

    async def asyncSetUp(self) -> None:
        from app.db.init_db import _create_mission_control_plane_sqlite
        from app.db.sqlite_pool import SQLitePool
        from app.repositories import MissionRepository
        from app.services.agent_binding_service import DatabaseAgentBindingResolver
        from app.services.mission_service import MissionService

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pool = SQLitePool(Path(self.temp.name) / "control.db")
        await self.pool.initialize()
        self.addAsyncCleanup(self.pool.close)
        async with self.pool.acquire() as connection:
            self.connection = connection
        await _create_mission_control_plane_sqlite(self.connection)
        from app.db.migrations.session_workspace import upgrade_session_workspace_sqlite
        await upgrade_session_workspace_sqlite(self.connection)

        @asynccontextmanager
        async def transaction():
            async with self.connection.transaction():
                yield self.connection
        self.transaction = transaction

        self.repository = MissionRepository(
            execute=self.connection.execute, fetch_one=self.connection.fetchrow,
            fetch_all=self.connection.fetch, transaction_factory=transaction,
        )

        async def lookup(scope: str, agent: str):
            return await self.connection.fetchrow(
                "SELECT agent_id, adapter_type, capabilities FROM agent_catalog_bindings "
                "WHERE scope_id=$1 AND agent_id=$2 AND enabled=TRUE", scope, agent,
            )

        self.resolver = DatabaseAgentBindingResolver(lookup=lookup)
        self.service = MissionService(self.repository, agent_binding_resolver=self.resolver)
        await self.connection.execute(
            "INSERT INTO agent_catalog_bindings(scope_id, agent_id, adapter_type, capabilities, enabled) "
            "VALUES($1,$2,$3,$4,TRUE)", "ws-chat", "selected-agent", "function-calling", '["catalog-only"]',
        )

    async def seed_mission(self, identifier: str = "mis-chat-real", **source_updates):
        from app.api.v1.chat_mission import _build_chat_contract
        from app.domain import ActorRef, MissionSource

        mission = await self.service.create_mission(
            mission_id=identifier, workspace_id="ws-chat", title="Chat", objective="inspect",
            source=MissionSource.model_validate({"type": "chat", "metadata": {"participants": [{
                "agentId": "selected-agent", "adapterType": "function-calling",
                "capabilities": ["catalog-only"],
            }]}, **source_updates}), contract=_build_chat_contract(f"contract-{identifier}"),
            actor=ActorRef(type="human", id="human-chat"),
        )
        return await self.service.start_mission(mission.id, actor=ActorRef(type="human", id="human-chat"))

    async def claim(self, *, workspace="ws-chat", agent="selected-agent", adapter="function-calling", kinds=("desktop.task",), runner="runner-chat"):
        from app.domain import ActorRef
        from app.services.workspace_admission_service import (
            WorkspaceClaimAdmissionPolicy,
        )

        return await self.service.claim_workspace_bound_work_unit(
            workspace, agent_id=agent, adapter_type=adapter, supported_work_unit_kinds=kinds,
            runner_id=runner, actor=ActorRef(type="service", id=runner),
            lease_seconds=60, admission_policy=WorkspaceClaimAdmissionPolicy(tenant_id="ws-chat", max_concurrent=0),
        )

    async def test_binding_idempotence_claim_and_lease_fence(self) -> None:
        from app.services.mission_service import LeaseOwnershipError
        from app.services.runner_service import DesktopTaskClaimedWorkResolver

        mission = await self.seed_mission()
        first = await self.service.create_chat_work_unit(mission.id, workspace_id="ws-chat")
        second = await self.service.create_chat_work_unit(mission.id, workspace_id="ws-chat")
        self.assertEqual(first, second)
        self.assertEqual(first.assigned_agent_id, "selected-agent")
        self.assertEqual(first.required_capabilities, ())
        self.assertEqual(first.status.value, "PENDING")
        self.assertIsNone(first.lease)
        self.assertEqual(len(await self.repository.list_work_units(mission.id)), 1)
        created = await self.connection.fetch(
            "SELECT event_id FROM mission_events WHERE correlation_id=$1 AND event_type='work_unit.lifecycle.created'",
            mission.id,
        )
        self.assertEqual(len(created), 1)

        for overrides in ({"workspace": "other"}, {"agent": "local-desktop-agent"},
                          {"adapter": "a2a.outbound"}, {"kinds": ("a2a.inbound",)}):
            self.assertEqual((await self.claim(**overrides)).status.value, "idle")
        outcome = await self.claim()
        leased = outcome.work_unit
        self.assertEqual(outcome.status.value, "claimed")
        self.assertEqual(leased.attempt, 1)
        self.assertEqual(leased.status.value, "LEASED")
        resumed = await self.claim()
        self.assertEqual(resumed.work_unit.lease, leased.lease)
        self.assertEqual(resumed.work_unit.attempt, leased.attempt)
        self.assertEqual((await self.claim(runner="other-runner")).status.value, "idle")
        with self.assertRaises(LeaseOwnershipError):
            await self.service.get_claimed_execution_context(mission.id, leased.id, lease_id="wrong", runner_id="runner-chat")
        context = await self.service.get_claimed_execution_context(
            mission.id, leased.id, lease_id=leased.lease.id, runner_id="runner-chat",
        )

        class Control:
            async def get_execution_context(_, *args, **kwargs):
                return {"executionContext": context.to_public_dict()}

        class Harness:
            async def execute(_, **kwargs):
                raise AssertionError("resolving a claim must not execute it")

        class Factory:
            def build(_, projection):
                return Harness()

        resolver = DesktopTaskClaimedWorkResolver(Control(), runner_id="runner-chat", harness_factory=Factory())
        execution = await resolver.resolve(leased.to_public_dict())
        prompt = json.loads(execution.execution_input.code)
        self.assertEqual(prompt["mission"]["source"]["type"], "chat")
        self.assertEqual(prompt["workUnit"]["id"], leased.id)
        self.assertEqual((await self.repository.get_mission(mission.id)).status.value, "RUNNING")
        self.assertFalse(await self.repository.list_evidence(mission.id))

    async def test_dispatch_rejects_scope_and_disabled_binding_without_work(self) -> None:
        mission = await self.seed_mission()
        with self.assertRaisesRegex(ValueError, "another workspace"):
            await self.service.create_chat_work_unit(mission.id, workspace_id="other")
        await self.connection.execute("UPDATE agent_catalog_bindings SET enabled=FALSE")
        with self.assertRaisesRegex(ValueError, "changed or is disabled"):
            await self.service.create_chat_work_unit(mission.id, workspace_id="ws-chat")
        self.assertFalse(await self.repository.list_work_units(mission.id))

    async def test_creation_rolls_back_when_event_persistence_fails(self) -> None:
        from app.repositories import MissionRepository

        mission = await self.seed_mission()
        with (
            patch.object(MissionRepository, "append_event", side_effect=RuntimeError("event unavailable")),
            self.assertRaisesRegex(RuntimeError, "event unavailable"),
        ):
            await self.service.create_chat_work_unit(mission.id, workspace_id="ws-chat")
        self.assertFalse(await self.repository.list_work_units(mission.id))

    async def test_other_source_and_wrong_chat_adapter_cannot_be_claimed(self) -> None:
        from app.domain import WorkUnit

        for identifier, source, adapter in (("mis-api", "api", "function-calling"),
                                             ("mis-chat-outbound", "chat", "a2a.outbound")):
            mission = await self.seed_mission(identifier, type=source)
            await self.repository.add_work_unit(WorkUnit(
                id=f"wu-{identifier}", mission_id=mission.id, kind="desktop.task",
                assigned_agent_id="selected-agent", assigned_adapter=adapter, dependencies=[],
                input_refs=[], expected_outputs=[], required_capabilities=[], status="PENDING", attempt=0,
            ))
            self.assertEqual((await self.claim(adapter=adapter)).status.value, "idle")

    async def test_claim_compiler_rejects_binding_and_source_changes(self) -> None:
        from copy import deepcopy

        from app.services.runner_service import (
            ClaimedWorkResolutionError,
            DesktopTaskClaimedWorkResolver,
        )

        mission = await self.seed_mission()
        await self.service.create_chat_work_unit(mission.id, workspace_id="ws-chat")
        leased = (await self.claim()).work_unit
        context = await self.service.get_claimed_execution_context(
            mission.id, leased.id, lease_id=leased.lease.id, runner_id="runner-chat",
        )

        class Factory:
            def build(_, projection):
                raise AssertionError("invalid projection must fail before creating a Harness")

        original = context.to_public_dict()
        for field, value in (("assignedAgentId", "other"), ("assignedAdapter", "a2a.outbound"),
                             ("kind", "a2a.inbound")):
            changed = deepcopy(original)
            changed["workUnit"][field] = value

            class Control:
                async def get_execution_context(_, *args, projection=changed, **kwargs):
                    return {"executionContext": projection}

            resolver = DesktopTaskClaimedWorkResolver(Control(), runner_id="runner-chat", harness_factory=Factory())
            with self.assertRaises(ClaimedWorkResolutionError):
                await resolver.resolve(leased.to_public_dict())

    async def test_api_uses_database_catalog_and_returns_durable_pending(self) -> None:
        from app.repositories import SessionEventRepository, SessionRepository

        sessions = SessionRepository(execute=self.connection.execute, fetch_one=self.connection.fetchrow,
                                     fetch_all=self.connection.fetch)
        events = SessionEventRepository(execute=self.connection.execute, fetch_one=self.connection.fetchrow,
                                        fetch_all=self.connection.fetch)
        app, _ = build_chat_app(repository=self.repository, sessions_repo=sessions,
                               session_events_repo=events, resolver=self.resolver)
        with patch("app.db.session.afetch_all", self.connection.fetch):
            response = await asyncio.to_thread(TestClient(app).post, "/api/v1/chat/mission",
                                               json={"message": "@selected-agent inspect", "workspaceId": "ws-chat"})
        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        units = await self.repository.list_work_units(body["missionId"])
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].assigned_agent_id, "selected-agent")
        self.assertEqual(body["dispatch"]["status"], "PENDING")
        self.assertEqual(body["dispatch"]["workUnitId"], units[0].id)
        persisted = await self.repository.get_mission(body["missionId"])
        self.assertEqual(persisted.source.session_id, body["sessionId"])

    def real_confirmation_app(self):
        from app.repositories import (
            PendingConfirmationRepository,
            SessionEventRepository,
            SessionRepository,
        )

        arguments = {"execute": self.connection.execute, "fetch_one": self.connection.fetchrow,
                     "fetch_all": self.connection.fetch}
        app, _ = build_chat_app(repository=self.repository, sessions_repo=SessionRepository(**arguments),
                               session_events_repo=SessionEventRepository(**arguments), resolver=self.resolver,
                               pending_repo=PendingConfirmationRepository(**arguments, transaction_factory=self.transaction))
        return app

    async def create_pending(self, client):
        rules = TestChatDispatchAdmission.RULE_YAML.replace("researcher", "selected-agent").replace(
            "require_confirmation: false", "require_confirmation: true",
        )
        response = await client.post("/api/v1/chat/mission", json={
            "message": "route this", "rulesYaml": rules, "workspaceId": "ws-chat",
        })
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["status"], "pending")
        return response.json()["pendingId"]

    async def test_concurrent_confirmation_creates_one_mission_and_one_work_unit(self) -> None:
        app = self.real_confirmation_app()
        with patch("app.db.session.afetch_all", self.connection.fetch):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                pending_id = await self.create_pending(client)
                responses = await asyncio.gather(*[
                    client.post("/api/v1/chat/confirm", json={"pendingId": pending_id}) for _ in range(2)
                ])
        self.assertEqual(sorted(response.status_code for response in responses), [202, 409])
        missions = await self.repository.list_missions("ws-chat")
        self.assertEqual(len(missions), 1)
        self.assertEqual(len(await self.repository.list_work_units(missions[0].id)), 1)
        pending = await self.connection.fetchrow("SELECT status FROM pending_confirmations WHERE id=$1", pending_id)
        self.assertEqual(pending["status"], "CONFIRMED")

    async def test_confirmation_event_failure_rolls_back_pending_and_dispatch(self) -> None:
        from app.repositories import SessionEventRepository

        app = self.real_confirmation_app()
        with patch("app.db.session.afetch_all", self.connection.fetch):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                pending_id = await self.create_pending(client)
                with patch.object(SessionEventRepository, "add_session_event", side_effect=RuntimeError("event unavailable")):
                    response = await client.post("/api/v1/chat/confirm", json={"pendingId": pending_id})
        self.assertEqual(response.status_code, 503, response.text)
        self.assertFalse(await self.repository.list_missions("ws-chat"))
        pending = await self.connection.fetchrow("SELECT status FROM pending_confirmations WHERE id=$1", pending_id)
        self.assertEqual(pending["status"], "PENDING")
        self.assertFalse(await self.connection.fetch("SELECT id FROM work_units"))

    async def test_expiry_is_committed_before_http_gone(self) -> None:
        app = self.real_confirmation_app()
        with patch("app.db.session.afetch_all", self.connection.fetch):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                pending_id = await self.create_pending(client)
                await self.connection.execute("UPDATE pending_confirmations SET expires_at=$1 WHERE id=$2",
                                              "2000-01-01T00:00:00+00:00", pending_id)
                response = await client.post("/api/v1/chat/confirm", json={"pendingId": pending_id})
        self.assertEqual(response.status_code, 410, response.text)
        pending = await self.connection.fetchrow("SELECT status FROM pending_confirmations WHERE id=$1", pending_id)
        self.assertEqual(pending["status"], "EXPIRED")
        self.assertFalse(await self.repository.list_missions("ws-chat"))



class TestChatDispatchAdmission(unittest.TestCase):
    RULE_YAML = """
rules:
  - id: route-rule
    description: Route request
    trigger:
      kind: keyword
      keywords: [route]
    action:
      kind: create_mission
      require_confirmation: false
      target_agent: researcher
"""

    def test_rule_target_is_bound_and_conflicting_mention_is_rejected(self) -> None:
        app, fakes = build_chat_app()
        client = TestClient(app)
        response = client.post("/api/v1/chat/mission", json={"message": "route this", "rulesYaml": self.RULE_YAML})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["dispatch"]["assignedAgentId"], "researcher")
        before = len(fakes["repo"].missions)
        response = client.post("/api/v1/chat/mission", json={"message": "@dev route this", "rulesYaml": self.RULE_YAML})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(len(fakes["repo"].missions), before)

    def test_required_confirmation_storage_failure_cannot_execute(self) -> None:
        app, fakes = build_chat_app()
        rules = self.RULE_YAML.replace("require_confirmation: false", "require_confirmation: true")
        with patch.object(fakes["pending"], "add_pending", side_effect=RuntimeError("storage unavailable")):
            response = TestClient(app).post("/api/v1/chat/mission", json={"message": "route this", "rulesYaml": rules})
        self.assertEqual(response.status_code, 503, response.text)
        self.assertFalse(fakes["repo"].missions)
        self.assertFalse(fakes["repo"].work_units)

    def test_session_storage_failure_cannot_execute(self) -> None:
        app, fakes = build_chat_app()
        with patch.object(fakes["sessions"], "add_session", side_effect=RuntimeError("storage unavailable")):
            response = TestClient(app).post("/api/v1/chat/mission", json={"message": "inspect"})
        self.assertEqual(response.status_code, 503, response.text)
        self.assertFalse(fakes["repo"].missions)

    def test_foreign_session_is_rejected_before_writing_events(self) -> None:
        from datetime import datetime

        from app.domain import ActorRef, Session

        app, fakes = build_chat_app()
        timestamp = datetime.now(UTC)
        fakes["sessions"].sessions["foreign-session"] = Session(
            id="foreign-session", workspace_id="foreign-workspace", title="Foreign", status="ACTIVE",
            created_by=ActorRef(type="human", id="user-other"), created_at=timestamp, updated_at=timestamp,
        )
        response = TestClient(app).post("/api/v1/chat/mission", json={"message": "inspect", "sessionId": "foreign-session"})
        self.assertEqual(response.status_code, 404, response.text)
        self.assertFalse(fakes["repo"].missions)
        self.assertFalse(fakes["session_events"].events)

    def test_orchestration_cannot_return_a_synthetic_mission(self) -> None:
        app, fakes = build_chat_app()
        response = TestClient(app).post("/api/v1/chat/orchestrate", json={"mode": "intent", "objective": "run parallel agents"})
        self.assertEqual(response.status_code, 501, response.text)
        self.assertNotIn("missionId", response.json())
        self.assertFalse(fakes["repo"].missions)
        self.assertFalse(fakes["session_events"].events)

    def test_selected_agent_is_persisted_on_work_unit(self) -> None:
        app, fakes = build_chat_app()
        response = TestClient(app).post("/api/v1/chat/mission", json={"message": "@researcher inspect"})
        self.assertEqual(response.status_code, 202, response.text)
        work_units = list(fakes["repo"].work_units.values())
        self.assertEqual(len(work_units), 1)
        self.assertEqual(work_units[0].assigned_agent_id, "researcher")
        self.assertEqual(work_units[0].assigned_adapter, "function-calling")
        self.assertEqual(work_units[0].status.value, "PENDING")
        self.assertEqual(work_units[0].required_capabilities, ())

    def test_multiple_executors_are_rejected_before_creation(self) -> None:
        app, fakes = build_chat_app()
        response = TestClient(app).post("/api/v1/chat/mission", json={"message": "@dev @researcher inspect"})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertFalse(fakes["repo"].missions)

    def test_outbound_adapter_is_rejected_before_creation(self) -> None:
        app, fakes = build_chat_app(resolver=_FakeAgentBindingResolver([
            AgentBinding("peer", "a2a.outbound", ("a2a.send",)),
        ]))
        response = TestClient(app).post("/api/v1/chat/mission", json={"message": "@peer inspect"})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertFalse(fakes["repo"].missions)

    def test_empty_catalog_is_rejected_before_creation(self) -> None:
        app, fakes = build_chat_app(resolver=_FakeAgentBindingResolver([]))
        response = TestClient(app).post("/api/v1/chat/mission", json={"message": "inspect"})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertFalse(fakes["repo"].missions)

    def test_casefold_collision_cannot_pick_an_arbitrary_agent(self) -> None:
        app, fakes = build_chat_app(resolver=_FakeAgentBindingResolver([
            AgentBinding("dev", "function-calling", ()),
            AgentBinding("DEV", "function-calling", ()),
        ]))
        response = TestClient(app).post("/api/v1/chat/mission", json={"message": "@dev inspect"})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertFalse(fakes["repo"].missions)
