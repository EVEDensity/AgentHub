from __future__ import annotations

import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from app.db.init_db import _create_mission_control_plane_sqlite
from app.db.sqlite_pool import SQLitePool
from app.repositories import MissionRepository
from app.core.config import ArtifactStoreSettings
from app.services.artifact_integrity_service import ContentAddressedArtifactByteVerifier
from app.services.verifier_service import ControlledVerifier, MissionControlVerifierClient
from tests.domain.factories import build_contract, build_mission, build_work_unit
from tests.integration.test_verifier_service_http import _build_mission_control
from tests.integration.test_verification_reports_http import report_criterion
from tests.services.verification_report_fixtures import make_test_report, report_artifact


class VerificationReportsSQLiteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.pool = SQLitePool(self.root / "control.sqlite3")
        await self.pool.initialize()
        self.addAsyncCleanup(self.pool.close)
        async with self.pool.acquire() as connection:
            self.connection = connection
        await _create_mission_control_plane_sqlite(self.connection)

        @asynccontextmanager
        async def transaction():
            async with self.connection.transaction():
                yield self.connection

        self.repository = MissionRepository(execute=self.connection.execute, fetch_one=self.connection.fetchrow,
                                            fetch_all=self.connection.fetch, transaction_factory=transaction)
        self.byte_verifier = ContentAddressedArtifactByteVerifier(ArtifactStoreSettings(
            backend="local", local_root=self.root, verify_max_bytes=2 * 1024 * 1024,
        ))

    async def seed(self, document):
        contract = build_contract(acceptance_criteria=[report_criterion("test-run.v1")])
        await self.repository.add_contract_lineage(contract.id, "workspace-1")
        await self.repository.add_contract(contract)
        await self.repository.add_mission(build_mission(workspace_id="workspace-1", status="RUNNING"))
        await self.repository.add_work_unit(build_work_unit(status="VERIFYING", attempt=1, assigned_agent_id="executor"))
        artifact, observation = report_artifact(document)
        path = self.root / "sha256" / artifact.digest.removeprefix("sha256:")
        path.parent.mkdir()
        path.write_bytes(observation.report_content)
        await self.repository.add_artifact(artifact)

    async def evaluate(self):
        app = _build_mission_control(self.repository, self.byte_verifier)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
            verifier = ControlledVerifier(
                MissionControlVerifierClient("http://mission-control.test", access_token="verifier-token", http_client=http),
                byte_verifier=self.byte_verifier, verifier_id="verifier-1", verifier_version="report.v1",
            )
            return await verifier.discover_and_verify("workspace-1")

    async def test_authenticated_positive_report_commits_evidence_and_success(self):
        await self.seed(make_test_report())
        result = await self.evaluate()
        evidence = await self.repository.list_evidence("mis-1")
        self.assertEqual(evidence[0].id, result.evidence_id)
        self.assertEqual(evidence[0].verdict.value, "PASS")
        self.assertEqual((await self.repository.get_work_unit("wu-1")).status.value, "SUCCEEDED")
        self.assertEqual((await self.repository.get_mission("mis-1")).status.value, "SUCCEEDED")

    async def test_authenticated_failed_report_commits_fail_without_any_pass(self):
        await self.seed(make_test_report(("failed",), conclusion="failed", exitCode=1))
        await self.evaluate()
        evidence = await self.repository.list_evidence("mis-1")
        self.assertEqual([item.verdict.value for item in evidence], ["FAIL"])
        self.assertEqual((await self.repository.get_work_unit("wu-1")).status.value, "FAILED")
        self.assertEqual((await self.repository.get_mission("mis-1")).status.value, "FAILED")
