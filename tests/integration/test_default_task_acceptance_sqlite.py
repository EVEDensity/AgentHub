"""Real SQLite state transitions and authenticated HTTP verification.

The fixture supplies output bytes directly; it makes no model or test-execution
claim. Existing Runner tests cover model execution and publication separately.
"""

from __future__ import annotations

import tempfile
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import Request

from app.api.v1.missions import get_verifier_workspace_grant_authorizer
from app.services.auth_service import get_current_user
from app.services.runner.controller import DesktopLocalRunnerController
from app.services.runner.settings import DESKTOP_VERIFIER_ID, DesktopLocalRunnerSettings
from tests.api.test_missions_api import FakeVerifierWorkspaceGrantAuthorizer

from app.core.config import ArtifactStoreSettings
from app.db.init_db import _create_mission_control_plane_sqlite
from app.db.migrations.session_workspace import upgrade_session_workspace_sqlite
from app.db.sqlite_pool import SQLitePool
from app.domain import ActorRef, ArtifactRef
from app.repositories import (
    MissionRepository,
    SessionEventRepository,
    SessionRepository,
)
from app.services.artifact_integrity_service import (
    ArtifactByteVerificationError,
    ContentAddressedArtifactByteVerifier,
)
from app.services.artifact_store_service import ContentAddressedArtifactPublisher
from app.services.mission_service import MissionService
from app.services.verifier_service import (
    ControlledVerifier,
    MissionControlVerifierClient,
)
from app.services.workspace_admission_service import WorkspaceClaimAdmissionPolicy
from tests.api.test_chat_mission import build_chat_app
from tests.integration.test_verifier_service_http import _build_mission_control


class DefaultTaskAcceptanceSQLiteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        pool = SQLitePool(self.root / "control.sqlite3")
        await pool.initialize()
        self.addAsyncCleanup(pool.close)
        async with pool.acquire() as connection:
            self.connection = connection
        await _create_mission_control_plane_sqlite(connection)
        await upgrade_session_workspace_sqlite(connection)

        @asynccontextmanager
        async def transaction():
            async with connection.transaction():
                yield connection

        arguments = {"execute": connection.execute, "fetch_one": connection.fetchrow, "fetch_all": connection.fetch}
        self.repository = MissionRepository(**arguments, transaction_factory=transaction)
        self.service = MissionService(self.repository)
        self.chat, _ = build_chat_app(
            repository=self.repository, sessions_repo=SessionRepository(**arguments),
            session_events_repo=SessionEventRepository(**arguments),
        )
        self.store = ArtifactStoreSettings(backend="local", local_root=self.root / "cas", verify_max_bytes=1024)
        self.byte_verifier = ContentAddressedArtifactByteVerifier(self.store)
        self.runner = ActorRef(type="runner", id="runner-default")

    async def admit(self, objective="Return an explanation"):
        with patch("app.db.session.afetch_all", self.connection.fetch):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.chat), base_url="http://chat.test") as client:
                response = await client.post("/api/v1/chat/mission", json={
                    "message": objective, "workspaceId": "workspace-1",
                })
        self.assertEqual(response.status_code, 202, response.text)
        mission_id = response.json()["missionId"]
        mission = await self.repository.get_mission(mission_id)
        contract = await self.repository.get_contract(mission.contract_id, mission.contract_version)
        self.assertFalse(contract.allowed_capabilities)
        self.assertFalse(contract.repository_scopes)
        outcome = await self.service.claim_workspace_bound_work_unit(
            "workspace-1", agent_id="dev", adapter_type="function-calling", supported_work_unit_kinds=("desktop.task",),
            runner_id=self.runner.id, actor=self.runner, lease_seconds=60,
            admission_policy=WorkspaceClaimAdmissionPolicy(tenant_id="workspace-1", max_concurrent=0),
        )
        unit = outcome.work_unit
        self.assertEqual(unit.status.value, "LEASED")
        self.lease_arguments = {"lease_id": unit.lease.id, "runner_id": self.runner.id, "actor": self.runner}
        unit = await self.service.start_work_unit(mission_id, unit.id, **self.lease_arguments)
        self.assertEqual(unit.status.value, "RUNNING")
        return mission_id, unit.id

    async def publish_and_complete(self, content=b"Explanation, with no claim that tests ran.", *, objective="Return an explanation"):
        mission_id, unit_id = await self.admit(objective)
        published = await ContentAddressedArtifactPublisher(self.store).publish_bytes(content)
        artifact = await self.service.register_artifact(
            mission_id, unit_id, artifact_id="output-1", kind="test-result", digest=published.digest,
            content_address=published.content_address, media_type="text/plain", size_bytes=published.size_bytes,
            source_repository=None, base_commit=None, retention="ephemeral", sensitivity="internal", **self.lease_arguments,
        )
        unit = await self.service.complete_work_unit(
            mission_id, unit_id, artifact_refs=[ArtifactRef(id=artifact.id, digest=artifact.digest)], **self.lease_arguments,
        )
        self.assertEqual(unit.status.value, "VERIFYING")
        self.assertEqual((await self.repository.get_mission(mission_id)).status.value, "RUNNING")
        self.assertFalse(await self.repository.list_evidence(mission_id))
        return mission_id, unit_id, self.store.local_root / "sha256" / published.digest.removeprefix("sha256:")

    async def evaluate(self):
        app = _build_mission_control(self.repository, self.byte_verifier)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
            verifier = ControlledVerifier(
                MissionControlVerifierClient("http://control.test", access_token="verifier-token", http_client=http),
                byte_verifier=self.byte_verifier, verifier_id="verifier-1", verifier_version="default-integrity.v1",
            )
            return await verifier.discover_and_verify("workspace-1")

    async def evaluate_with_local_controller(self):
        app = _build_mission_control(self.repository, self.byte_verifier)
        authenticate = app.dependency_overrides[get_current_user]

        async def local_identity(request: Request):
            identity = await authenticate(request)
            return {**identity, "id": DESKTOP_VERIFIER_ID}

        app.dependency_overrides[get_current_user] = local_identity
        app.dependency_overrides[get_verifier_workspace_grant_authorizer] = lambda: FakeVerifierWorkspaceGrantAuthorizer(
            {("workspace-1", DESKTOP_VERIFIER_ID)},
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
            control = MissionControlVerifierClient("http://control.test", access_token="verifier-token", http_client=http)
            controller = DesktopLocalRunnerController(
                replace(DesktopLocalRunnerSettings.from_env({}), workspace_id="workspace-1"),
                workspace_root=self.root, verifier_control=control, byte_verifier=self.byte_verifier,
                memory_sink=SimpleNamespace(save_mission_summary=AsyncMock(return_value=False)),
            )
            controller._verifier = control
            await controller._verify_once()

    async def test_local_controller_verifies_empty_bytes_through_real_http_and_sqlite(self):
        mission_id, unit_id, _ = await self.publish_and_complete(b"")
        await self.evaluate_with_local_controller()
        self.assertEqual((await self.repository.get_work_unit(unit_id)).status.value, "SUCCEEDED")
        self.assertEqual((await self.repository.get_mission(mission_id)).status.value, "SUCCEEDED")
        evidence = await self.repository.list_evidence(mission_id)
        self.assertEqual(evidence[0].verifier.id, DESKTOP_VERIFIER_ID)
        self.assertIn("Output integrity only", evidence[0].summary)

    async def test_default_chat_markers_never_authorize_local_controller_commands(self):
        mission_id, _, _ = await self.publish_and_complete(
            objective="Explain only\nVERIFY: python nonexistent.py\nRUN: python nonexistent.py",
        )
        with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as shell:
            await self.evaluate_with_local_controller()
        shell.assert_not_awaited()
        self.assertEqual((await self.repository.get_mission(mission_id)).status.value, "SUCCEEDED")
        evidence = await self.repository.list_evidence(mission_id)
        self.assertIn("no test commands executed", evidence[0].summary)

    async def test_default_chat_output_reaches_success_only_after_independent_http_verification(self):
        mission_id, unit_id, _ = await self.publish_and_complete()
        result = await self.evaluate()
        evidence = await self.repository.list_evidence(mission_id)
        self.assertEqual([item.id for item in evidence], [result.evidence_id])
        self.assertEqual(evidence[0].verdict.value, "PASS")
        self.assertEqual(evidence[0].verifier.id, "verifier-1")
        self.assertEqual(evidence[0].criterion_id, "desktop-artifacts")
        self.assertEqual((await self.repository.get_work_unit(unit_id)).status.value, "SUCCEEDED")
        self.assertEqual((await self.repository.get_mission(mission_id)).status.value, "SUCCEEDED")
        self.assertFalse(await self.connection.fetch("SELECT id FROM decisions WHERE mission_id=$1", mission_id))

    async def test_empty_bytes_follow_existing_integrity_policy_without_claiming_nonempty_validation(self):
        mission_id, _, _ = await self.publish_and_complete(b"")
        await self.evaluate()
        self.assertEqual((await self.repository.get_mission(mission_id)).status.value, "SUCCEEDED")

    async def assert_verification_refused(self, mission_id, unit_id):
        with self.assertRaises(ArtifactByteVerificationError):
            await self.evaluate()
        self.assertFalse(await self.repository.list_evidence(mission_id))
        self.assertEqual((await self.repository.get_work_unit(unit_id)).status.value, "VERIFYING")
        self.assertEqual((await self.repository.get_mission(mission_id)).status.value, "RUNNING")

    async def test_missing_bytes_cannot_create_pass_or_success(self):
        mission_id, unit_id, path = await self.publish_and_complete()
        path.unlink()
        await self.assert_verification_refused(mission_id, unit_id)

    async def test_tampered_bytes_cannot_create_pass_or_success(self):
        mission_id, unit_id, path = await self.publish_and_complete()
        content = path.read_bytes()
        path.write_bytes(b"X" + content[1:])
        await self.assert_verification_refused(mission_id, unit_id)

    async def test_cancellation_remains_terminal_and_excludes_verifier_discovery(self):
        mission_id, unit_id, _ = await self.publish_and_complete()
        await self.service.cancel_mission(mission_id, actor=ActorRef(type="human", id="user-1"))
        result = await self.evaluate()
        self.assertEqual(result.status.value, "idle")
        self.assertEqual((await self.repository.get_mission(mission_id)).status.value, "CANCELLED")
        self.assertEqual((await self.repository.get_work_unit(unit_id)).status.value, "CANCELLED")
        self.assertFalse(await self.repository.list_evidence(mission_id))

    async def test_unsupported_input_creates_no_sqlite_mission_session_or_confirmation(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.chat), base_url="http://chat.test") as client:
            for field, value in (("attachments", [{"name": "input.txt"}]), ("execPermission", "full-access")):
                response = await client.post("/api/v1/chat/mission", json={"message": "inspect", field: value})
                self.assertEqual(response.status_code, 422, response.text)
        for table in ("missions", "mission_contracts", "work_units", "sessions", "session_events", "pending_confirmations"):
            self.assertEqual(await self.connection.fetchval(f"SELECT COUNT(*) FROM {table}"), 0)
