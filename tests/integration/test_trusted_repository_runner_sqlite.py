"""Real repository -> native tool loop -> real external pytest -> CAS -> Evidence.

The deterministic ModelPort is a test fixture, not a live provider. Snapshot,
patch reconstruction, pytest process, Runner reporting, SQLite and verifier
HTTP admission use their real implementations.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException, Request

from app.core.config import ArtifactStoreSettings
from app.db.init_db import _create_mission_control_plane_sqlite
from app.db.sqlite_pool import SQLitePool
from app.domain import ActorRef
from app.repositories import MissionRepository
from app.services.artifact_integrity_service import (
    ArtifactIntegrityError,
    ContentAddressedArtifactByteVerifier,
)
from app.services.artifact_store_service import ContentAddressedArtifactPublisher
from app.services.auth_service import get_current_user
from app.services.harness_service import FunctionCallingHarness, FunctionTool
from app.services.mission_service import MissionService
from app.services.model_contract import ModelResponse, ToolCall
from app.services.runner_client import MissionControlRunnerClient
from app.services.runner_service import WorkUnitRunner
from app.services.trusted_tests.harness import TrustedRepositoryHarness
from app.services.trusted_tests.models import canonical_bytes
from app.services.trusted_tests.policy import (
    TrustedTestParameters,
    policy_configuration_digest,
)
from app.services.trusted_tests.producer import TrustedLocalTestProducer
from app.services.trusted_tests.profile import freeze_test_profile
from app.services.trusted_tests.signing import TRUST_KEYS_FILE_ENV
from app.services.trusted_tests.snapshot import freeze_snapshot
from app.services.verifier_service import (
    ControlledVerifier,
    MissionControlVerifierClient,
)
from tests.api.mission_app_fixture import build_app
from tests.api.test_missions_api import FakeVerifierWorkspaceGrantAuthorizer
from tests.domain.factories import build_contract, build_mission, build_work_unit
from tests.services.trusted_test_fixtures import trusted_criterion


class DeterministicEditingModel:
    """Fixture model proposes one scoped edit, then an untrusted completion claim."""

    def __init__(self, source):
        self.source, self.called = source, False

    async def complete(self, _request):
        if not self.called:
            self.called = True
            return ModelResponse(tool_calls=(ToolCall("fixture-edit", "edit_candidate", {"source": self.source}),))
        return ModelResponse(content="The fixture model claims its task is complete.")


class DirectLeaseBridge:
    """Only initial leasing is direct; reporting and heartbeat use authenticated HTTP."""

    def __init__(self, repository, http_control):
        self.service, self.http_control = MissionService(repository), http_control

    async def lease_work_unit(self, mission_id, unit_id, *, runner_id, lease_seconds):
        unit = await self.service.lease_work_unit(mission_id, unit_id, runner_id=runner_id,
                                                actor=ActorRef(type="runner", id=runner_id), lease_seconds=lease_seconds)
        return unit.to_public_dict()

    def __getattr__(self, name):
        return getattr(self.http_control, name)


class TrustedRepositoryRunnerSQLiteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source, self.storage, self.suite = (self.root / name for name in ("repo", "storage", "suite"))
        for directory in (self.source, self.storage, self.suite):
            directory.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "AgentHub Fixture")
        self.git("config", "user.email", "fixture@example.test")
        (self.source / "calc.py").write_text("def add(a,b): return a-b\n", encoding="utf-8")
        self.git("add", "calc.py")
        self.git("commit", "-qm", "fixture baseline")
        self.dirty_bytes = b"def add(a,b): return a-b  # existing user change\n"
        (self.source / "calc.py").write_bytes(self.dirty_bytes)
        (self.source / "notes.txt").write_bytes(b"untracked user notes\n")
        self.dirty_status = self.git("status", "--porcelain")
        self.snapshot = freeze_snapshot(self.source, self.storage)
        self.addCleanup(self.snapshot.close)
        (self.suite / "test_acceptance.py").write_text("from calc import add\ndef test_add(): assert add(2,1)==3\n", encoding="utf-8")
        self.profile = freeze_test_profile(self.suite, (sys.executable, "-m", "pytest", "-q"), self.storage,
                                           execution_mode="trusted-local", timeout_seconds=15)
        self.addCleanup(self.profile.close)
        await self.initialize_control()
        self.install_trust()

    def git(self, *arguments):
        result = subprocess.run(["git", "-C", str(self.source), *arguments], capture_output=True, text=True, timeout=10, check=True)
        return result.stdout

    async def initialize_control(self):
        pool = SQLitePool(self.root / "control.sqlite3")
        await pool.initialize()
        self.addAsyncCleanup(pool.close)
        async with pool.acquire() as connection:
            self.connection = connection
        await _create_mission_control_plane_sqlite(connection)

        @asynccontextmanager
        async def transaction():
            async with connection.transaction():
                yield connection

        self.repository = MissionRepository(execute=connection.execute, fetch_one=connection.fetchrow,
                                            fetch_all=connection.fetch, transaction_factory=transaction)
        criterion = trusted_criterion(expectedInputSnapshotDigest=self.snapshot.digest, testProfileDigest=self.profile.digest)
        self.contract = build_contract(acceptance_criteria=[criterion])
        await self.repository.add_contract_lineage(self.contract.id, "workspace-1")
        await self.repository.add_contract(self.contract)
        await self.repository.add_mission(build_mission(workspace_id="workspace-1", status="RUNNING"))
        await self.repository.add_work_unit(build_work_unit(kind="desktop.task", status="PENDING", attempt=0))
        self.store = ArtifactStoreSettings(backend="local", local_root=self.root / "cas", verify_max_bytes=2 * 1024 * 1024)
        self.byte_verifier = ContentAddressedArtifactByteVerifier(self.store)

    def install_trust(self):
        self.key = Ed25519PrivateKey.generate()
        public = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        keys = {"schemaVersion": 1, "keys": [{"producerId": "producer-1", "keyId": "key-1",
                "publicKeyBase64": base64.b64encode(public).decode(), "testProfileDigests": [self.profile.digest]}]}
        path = self.root / "operator-keys.json"
        path.write_bytes(canonical_bytes(keys))
        environment = patch.dict(os.environ, {TRUST_KEYS_FILE_ENV: str(path)})
        environment.start()
        self.addCleanup(environment.stop)

    def application(self):
        app = build_app(self.repository, {"id": "runner-1", "role": "runner"}, artifact_byte_verifier=self.byte_verifier,
                        verifier_workspace_grant_authorizer=FakeVerifierWorkspaceGrantAuthorizer({("workspace-1", "verifier-1")}))

        async def authenticate(request: Request):
            token = request.headers.get("authorization")
            if token == "Bearer runner-token":
                return {"id": "runner-1", "name": "Runner", "role": "runner"}
            if token == "Bearer verifier-token":
                return {"id": "verifier-1", "name": "Independent Verifier", "role": "verifier"}
            raise HTTPException(status_code=401, detail="Authentication required")

        app.dependency_overrides[get_current_user] = authenticate
        return app

    def harness(self, *, correct):
        def native(candidate):
            async def edit(arguments):
                (candidate / "calc.py").write_text(arguments["source"], encoding="utf-8")
                return "Candidate copy updated; independent tests remain pending."

            source = "def add(a,b): return a+b\n" if correct else "def add(a,b): return 0\n"
            return FunctionCallingHarness(DeterministicEditingModel(source),
                                          [FunctionTool("edit_candidate", edit, lambda args: args)], max_iterations=3)

        configuration = dict(self.contract.acceptance_criteria[0].configuration)
        parameters = {key: value for key, value in configuration.items() if key not in {"evaluator", "workUnitKinds"}}
        return TrustedRepositoryHarness(snapshot=self.snapshot, profile=self.profile,
            producer=TrustedLocalTestProducer(producer_id="producer-1", key_id="key-1", private_key=self.key, scratch_root=self.storage),
            scratch_root=self.storage, harness_factory=native, policy=TrustedTestParameters.model_validate(parameters),
            contract_id=self.contract.id, contract_version=self.contract.version,
            configuration_digest=policy_configuration_digest(configuration))

    async def run_candidate(self, *, correct):
        app = self.application()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
            runner_http = MissionControlRunnerClient("http://control.test", access_token="runner-token", http_client=http)
            runner = WorkUnitRunner(DirectLeaseBridge(self.repository, runner_http),
                publisher=ContentAddressedArtifactPublisher(self.store), harness=self.harness(correct=correct),
                runner_id="runner-1", heartbeat_interval_seconds=0.05)
            result = await runner.run("mis-1", "wu-1", code="Fix calc.add on a candidate copy", timeout=30, cwd=self.source)
        self.assertTrue(result.success)
        self.assertEqual(result.work_unit["status"], "VERIFYING")
        self.assertEqual(len(result.artifacts), 5)
        self.assertEqual((await self.repository.get_mission("mis-1")).status.value, "RUNNING")
        artifacts = await self.repository.list_artifacts("mis-1")
        self.assertEqual(len(artifacts), 5)
        self.assertFalse(await self.repository.list_evidence("mis-1"))
        self.assert_original_unchanged()
        return artifacts

    async def verify(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.application())) as http:
            verifier = ControlledVerifier(
                MissionControlVerifierClient("http://control.test", access_token="verifier-token", http_client=http),
                byte_verifier=self.byte_verifier, verifier_id="verifier-1", verifier_version="trusted-real-pytest.v1")
            await verifier.discover_and_verify("workspace-1")
        self.assert_original_unchanged()

    def assert_original_unchanged(self):
        self.assertEqual((self.source / "calc.py").read_bytes(), self.dirty_bytes)
        self.assertEqual((self.source / "notes.txt").read_bytes(), b"untracked user notes\n")
        self.assertEqual(self.git("status", "--porcelain"), self.dirty_status)

    async def test_actual_external_pytest_pass_is_required_before_success(self):
        artifacts = await self.run_candidate(correct=True)
        report = next(item for item in artifacts if item.kind.value == "test-result")
        body = json.loads((self.store.local_root / "sha256" / report.digest[7:]).read_bytes())
        self.assertEqual((body["passed"], body["total"], body["exitCode"]), (1, 1, 0))
        await self.verify()
        self.assertEqual((await self.repository.get_mission("mis-1")).status.value, "SUCCEEDED")
        self.assertEqual([item.verdict.value for item in await self.repository.list_evidence("mis-1")], ["PASS"])

    async def test_real_failing_tests_override_model_completion_claim(self):
        await self.run_candidate(correct=False)
        await self.verify()
        self.assertEqual((await self.repository.get_mission("mis-1")).status.value, "FAILED")
        self.assertEqual([item.verdict.value for item in await self.repository.list_evidence("mis-1")], ["FAIL"])

    async def test_tampered_actual_runner_artifact_cannot_create_evidence(self):
        artifacts = await self.run_candidate(correct=True)
        receipt = next(item for item in artifacts if "-receipt-" in item.id)
        (self.store.local_root / "sha256" / receipt.digest[7:]).write_bytes(b"tampered signed receipt")
        with self.assertRaises(ArtifactIntegrityError):
            await self.verify()
        self.assertFalse(await self.repository.list_evidence("mis-1"))
        self.assertEqual((await self.repository.get_mission("mis-1")).status.value, "RUNNING")
        self.assertEqual((await self.repository.get_work_unit("wu-1")).status.value, "VERIFYING")
