"""Real local boot, claim observations, durable leases, and workspace isolation."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import httpx
from fastapi import FastAPI, Header

from app.api.v1.missions import (
    get_runner_workspace_grant_authorizer,
    get_workspace_claim_admission_policy_resolver,
)
from app.api.v1.router import router
from app.db.init_db import _ainit_sqlite
from app.db.sqlite_pool import SQLitePool
from app.domain import ActorRef, MissionSource
from app.repositories import MissionRepository
from app.repositories.runner_presence_repository import RunnerPresenceRepository
from app.services.auth_service import get_current_user
from app.services.mission_service import MissionService
from tests.api.test_missions_api import (
    FakeRunnerWorkspaceGrantAuthorizer,
    FakeWorkspaceClaimAdmissionPolicyResolver,
)
from tests.domain.factories import build_contract, build_work_unit


class RunnerAvailabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.pool = SQLitePool(Path(temporary.name) / "availability.sqlite3")
        await self.pool.initialize()
        self.addAsyncCleanup(self.pool.close)
        patcher = mock.patch("app.db.session.aget_pool", new=mock.AsyncMock(return_value=self.pool))
        patcher.start()
        self.addCleanup(patcher.stop)
        await _ainit_sqlite()
        async with self.pool.acquire() as connection:
            self.connection = connection
        self.repository = MissionRepository()
        self.service = MissionService(self.repository)
        await self.service.create_mission(
            mission_id="mission-alice", workspace_id="alice", title="Wait for selected executor",
            objective="Run selected work", source=MissionSource(type="chat"),
            contract=build_contract(id="contract-alice"), actor=ActorRef(type="human", id="alice"),
        )
        await self.repository.add_work_unit(build_work_unit(
            id="unit-alice", mission_id="mission-alice", kind="desktop.task",
            assigned_agent_id="executor", assigned_adapter="function-calling", required_capabilities=[],
        ))
        app = FastAPI()
        app.include_router(router)

        def authenticated_user(x_test_user: str = Header(default="alice")):
            return {"id": x_test_user, "role": "runner" if x_test_user.startswith("runner") else "user", "name": x_test_user}

        app.dependency_overrides[get_current_user] = authenticated_user
        app.dependency_overrides[get_runner_workspace_grant_authorizer] = lambda: FakeRunnerWorkspaceGrantAuthorizer({("alice", "runner-alice")})
        app.dependency_overrides[get_workspace_claim_admission_policy_resolver] = lambda: FakeWorkspaceClaimAdmissionPolicyResolver()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def status(self, user="alice"):
        return await self.client.get("/api/v1/missions/mission-alice/execution-status", headers={"X-Test-User": user})

    async def poll(self, *, user="runner-alice", **updates):
        body = {"workspaceId": "alice", "agentId": "executor", "adapterType": "function-calling", "supportedWorkUnitKinds": ["desktop.task"], "supportedCapabilities": []}
        body.update(updates)
        return await self.client.post("/api/v1/missions/work-unit-claims", headers={"X-Test-User": user}, json=body)

    async def test_pending_without_contact_waits_and_cannot_report_execution(self):
        response = await self.status()
        self.assertEqual(response.status_code, 200, response.text)
        unit = response.json()["workUnits"][0]
        self.assertEqual((unit["status"], unit["reason"], unit["matchingRunnerAvailable"]), ("PENDING", "waiting_runner", False))
        self.assertEqual(unit["availabilitySource"], "none")

    async def test_authorized_idle_poll_is_contact_but_pending_stays_waiting(self):
        polled = await self.poll()
        self.assertEqual(polled.status_code, 200, polled.text)
        self.assertEqual(polled.json()["claimStatus"], "idle")
        unit = (await self.status()).json()["workUnits"][0]
        self.assertEqual((unit["status"], unit["reason"], unit["matchingRunnerAvailable"]), ("PENDING", "waiting_claim", True))
        self.assertEqual(unit["availabilitySource"], "poll")

    async def test_ordinary_user_cannot_read_other_workspace(self):
        response = await self.status("bob")
        self.assertEqual(response.status_code, 403)

    async def test_unauthorized_or_invalid_polls_do_not_persist_contact(self):
        self.assertEqual((await self.poll(user="runner-bob")).status_code, 403)
        self.assertEqual((await self.poll(supportedCapabilities=["duplicate", "duplicate"])).status_code, 422)
        self.assertEqual(await self.connection.fetchval("SELECT COUNT(*) FROM runner_presence"), 0)

    async def test_expired_and_corrupt_observations_fail_closed(self):
        await self.poll()
        old = datetime.now(UTC) - timedelta(minutes=1)
        await self.connection.execute("UPDATE runner_presence SET last_seen_at=$1,expires_at=$2", old, old + timedelta(seconds=30))
        self.assertFalse((await self.status()).json()["workUnits"][0]["matchingRunnerAvailable"])
        await self.poll()
        await self.connection.execute("UPDATE runner_presence SET supported_capabilities='corrupt-json'")
        self.assertFalse((await self.status()).json()["workUnits"][0]["matchingRunnerAvailable"])

    async def test_agent_adapter_kind_workspace_and_capabilities_must_match(self):
        presence = RunnerPresenceRepository()
        for update in ({"agent_id": "other"}, {"adapter_type": "other"}, {"supported_work_unit_kinds": ("other",)}, {"workspace_id": "bob"}):
            values = {"workspace_id": "alice", "runner_id": "runner-alice", "agent_id": "executor", "adapter_type": "function-calling", "supported_work_unit_kinds": ("desktop.task",)}
            values.update(update)
            await presence.observe_poll(**values)
        self.assertFalse((await self.status()).json()["workUnits"][0]["matchingRunnerAvailable"])
        # The ordinary update command intentionally preserves immutable
        # capability snapshots. Arrange this pre-execution fixture explicitly.
        await self.connection.execute("UPDATE work_units SET required_capabilities=$1 WHERE id='unit-alice'", '["repository.write"]')
        self.assertEqual((await self.repository.get_work_unit("unit-alice")).required_capabilities, ("repository.write",))
        await self.poll(supportedCapabilities=["repository.read"])
        self.assertFalse((await self.status()).json()["workUnits"][0]["matchingRunnerAvailable"])
        await self.poll(supportedCapabilities=["repository.write"])
        self.assertTrue((await self.status()).json()["workUnits"][0]["matchingRunnerAvailable"])

    async def test_running_requires_live_lease_even_after_contact_expires(self):
        await self.service.start_mission("mission-alice", actor=ActorRef(type="human", id="alice"))
        claim = await self.poll()
        self.assertEqual(claim.status_code, 200, claim.text)
        unit = claim.json()["workUnit"]
        lease_id = unit["lease"]["id"]
        started = await self.client.post(
            "/api/v1/missions/mission-alice/work-units/unit-alice/start",
            headers={"X-Test-User": "runner-alice"}, json={"leaseId": lease_id},
        )
        self.assertEqual(started.status_code, 200, started.text)
        await self.connection.execute("DELETE FROM runner_presence")
        row = (await self.status()).json()["workUnits"][0]
        self.assertEqual((row["status"], row["reason"], row["availabilitySource"]), ("RUNNING", "executing", "lease"))
        persisted = await self.repository.get_work_unit("unit-alice")
        expired = persisted.lease.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
        await self.repository.update_work_unit(persisted.model_copy(update={"lease": expired}))
        row = (await self.status()).json()["workUnits"][0]
        self.assertEqual((row["status"], row["reason"], row["matchingRunnerAvailable"]), ("RUNNING", "lease_expired", False))

    async def test_database_outage_is_error_instead_of_runner_offline(self):
        with mock.patch.object(RunnerPresenceRepository, "matching_observation", side_effect=OSError("database offline")):
            response = await self.status()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "Execution status is unavailable"})

    async def test_observation_failure_does_not_lose_a_committed_claim(self):
        await self.service.start_mission("mission-alice", actor=ActorRef(type="human", id="alice"))
        with mock.patch.object(RunnerPresenceRepository, "observe_poll", side_effect=OSError("telemetry offline")):
            response = await self.poll()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["claimStatus"], "claimed")
        self.assertEqual((await self.repository.get_work_unit("unit-alice")).lease.id, response.json()["workUnit"]["lease"]["id"])
        self.assertEqual(await self.connection.fetchval("SELECT COUNT(*) FROM runner_presence"), 0)

    async def test_projection_matches_the_versioned_schema(self):
        from jsonschema import Draft202012Validator
        root = Path(__file__).parents[2]
        schema = json.loads((root / "platform/contracts/v1/mission-execution-status.schema.json").read_text())
        Draft202012Validator(schema).validate((await self.status()).json())
