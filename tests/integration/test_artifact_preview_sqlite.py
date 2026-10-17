from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from app.core.config import ArtifactStoreSettings
from app.db.init_db import _create_mission_control_plane_sqlite
from app.db.sqlite_pool import SQLitePool
from app.repositories import MissionRepository
from app.services.artifact_integrity_service import ContentAddressedArtifactByteVerifier
from tests.api.artifact_preview_fixture import PREVIEW_PATH, preview_app, text_artifact
from tests.domain.factories import build_contract, build_mission, build_work_unit


class ArtifactPreviewSQLiteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.pool = SQLitePool(self.root / "missions.sqlite3")
        await self.pool.initialize()
        self.addAsyncCleanup(self.pool.close)
        async with self.pool.acquire() as connection:
            self.connection = connection
        await _create_mission_control_plane_sqlite(self.connection)
        self.repository = MissionRepository(
            execute=self.connection.execute, fetch_one=self.connection.fetchrow,
            fetch_all=self.connection.fetch,
        )
        await self.repository.add_contract_lineage("contract-1", "workspace-1")
        await self.repository.add_contract(build_contract())
        await self.repository.add_mission(build_mission(status="RUNNING"))
        await self.repository.add_work_unit(build_work_unit(status="VERIFYING", attempt=1))
        self.content = "真实 CAS 文本\n<script>window.neverRuns = true</script>".encode()
        self.artifact = text_artifact(self.content, media_type="text/html")
        await self.repository.add_artifact(self.artifact)
        self.path = self.root / "sha256" / self.artifact.digest.removeprefix("sha256:")
        self.path.parent.mkdir()
        self.path.write_bytes(self.content)
        self.exporter = ContentAddressedArtifactByteVerifier(ArtifactStoreSettings(
            backend="local", local_root=self.root, verify_max_bytes=2 * 1024 * 1024,
        ))
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=preview_app(self.repository, self.exporter)),
            base_url="http://mission-control.test",
        )
        self.addAsyncCleanup(self.http.aclose)

    async def snapshot(self):
        return {
            "mission": (await self.repository.get_mission("mis-1")).to_public_dict(),
            "unit": (await self.repository.get_work_unit("wu-1")).to_public_dict(),
            "artifacts": [a.to_public_dict() for a in await self.repository.list_artifacts("mis-1")],
            "evidence": await self.repository.list_evidence("mis-1"),
            "events": await self.repository.list_events("mis-1"),
        }

    async def test_real_registered_cas_preview_does_not_create_evidence_or_transition(self):
        before = await self.snapshot()
        for _ in range(2):
            response = await self.http.get(PREVIEW_PATH)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["text"], self.content.decode())
            self.assertEqual(response.json()["integrity"], "verified")
            self.assertNotIn("contentAddress", response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(await self.snapshot(), before)
        self.assertEqual(before["unit"]["status"], "VERIFYING")
        self.assertEqual(before["evidence"], [])

    async def test_real_missing_and_modified_cas_bytes_fail_without_state_changes(self):
        before = await self.snapshot()
        self.path.write_bytes(b"corrupted registered bytes")
        response = await self.http.get(PREVIEW_PATH)
        self.assertEqual(response.status_code, 409)
        self.path.unlink()
        response = await self.http.get(PREVIEW_PATH)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(await self.snapshot(), before)
