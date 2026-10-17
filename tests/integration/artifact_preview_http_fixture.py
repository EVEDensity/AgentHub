"""Disposable loopback-only SQLite/CAS server for the browser acceptance gate.

Run with ``python -m tests.integration.artifact_preview_http_fixture``. The
fixed test credentials below apply only to this executable test fixture; this
module is never imported by the production application or auth service.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.v1.missions import (
    get_mission_artifact_byte_exporter,
    get_mission_repository,
    router,
)
from app.core.config import ArtifactStoreSettings
from app.db.init_db import _create_mission_control_plane_sqlite
from app.db.sqlite_pool import SQLitePool
from app.repositories import MissionRepository
from app.services.artifact_integrity_service import ContentAddressedArtifactByteVerifier
from app.services.auth_service import get_current_user
from tests.api.artifact_preview_fixture import text_artifact
from tests.domain.factories import build_contract, build_mission, build_work_unit

HOST = "127.0.0.1"
PORT = 38100
WORKSPACE_ID = "workspace-preview"
MISSION_ID = "mis-preview-browser"
WORK_UNIT_ID = "wu-preview-browser"
CODE_TEXT = "print('产物 中文')\n# <script>window.PREVIEW_EXECUTED=true</script>\n"
JSON_TEXT = '{"note":"产物 中文","html":"<img src=x onerror=window.PREVIEW_EXECUTED=true>"}'
HTML_TEXT = (
    '<h1>产物 中文</h1><script>window.PREVIEW_EXECUTED=true</script>'
    '<img src="/fixture/should-never-load.png" onerror="window.PREVIEW_EXECUTED=true">'
)
_SECURITY = HTTPBearer(auto_error=False)


async def fixture_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_SECURITY)],
) -> dict:
    if credentials is not None and credentials.scheme.lower() == "bearer":
        if credentials.credentials == "artifact-browser-human":
            return {"id": WORKSPACE_ID, "role": "developer", "name": "Fixture human"}
        if credentials.credentials == "artifact-browser-foreign":
            return {"id": "workspace-foreign", "role": "developer", "name": "Foreign human"}
    raise HTTPException(401, "Fixture authentication required")


class CountingExporter:
    """Observe calls while preserving the real bounded CAS byte read."""

    def __init__(self, delegate: ContentAddressedArtifactByteVerifier):
        self.delegate = delegate
        self.reads: Counter[str] = Counter()

    async def read_verified(self, artifact, *, max_bytes: int) -> bytes:
        self.reads[artifact.id] += 1
        return await self.delegate.read_verified(artifact, max_bytes=max_bytes)


async def seed(repository: MissionRepository, cas_root: Path) -> dict:
    contract = build_contract(id="contract-preview-browser")
    await repository.add_contract_lineage(contract.id, WORKSPACE_ID)
    await repository.add_contract(contract)
    await repository.add_mission(build_mission(
        id=MISSION_ID, workspace_id=WORKSPACE_ID, contract_id=contract.id,
        title="Artifact preview browser fixture", status="RUNNING",
        objective="Inspect registered text without creating Evidence or executing it.",
    ))
    await repository.add_work_unit(build_work_unit(
        id=WORK_UNIT_ID, mission_id=MISSION_ID, status="VERIFYING", attempt=1,
    ))
    metadata = {}
    values = (
        ("code", CODE_TEXT.encode("utf-8"), "text/x-python", "internal"),
        ("json", JSON_TEXT.encode("utf-8"), "application/json", "internal"),
        ("html", HTML_TEXT.encode("utf-8"), "text/html", "internal"),
        ("empty", b"", "text/plain", "internal"),
        ("large", b"x" * 262145, "text/plain", "internal"),
        ("denied", b"Confidential fixture text", "text/plain", "confidential"),
    )
    directory = cas_root / "sha256"
    directory.mkdir(parents=True)
    for name, content, media_type, sensitivity in values:
        artifact = text_artifact(
            content, id=f"artifact-browser-{name}", mission_id=MISSION_ID,
            work_unit_id=WORK_UNIT_ID, kind="file", media_type=media_type,
            sensitivity=sensitivity,
        )
        (directory / artifact.digest.removeprefix("sha256:")).write_bytes(content)
        await repository.add_artifact(artifact)
        metadata[name] = {"id": artifact.id, "digest": artifact.digest}
    return {"port": PORT, "missionId": MISSION_ID, "workspaceId": WORKSPACE_ID,
            "artifacts": metadata}


def build_fixture_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with tempfile.TemporaryDirectory(prefix="agenthub-artifact-browser-") as temporary:
            root = Path(temporary)
            pool = SQLitePool(root / "missions.sqlite3")
            try:
                await pool.initialize()
                async with pool.acquire() as connection:
                    await _create_mission_control_plane_sqlite(connection)
                    repository = MissionRepository(
                        execute=connection.execute, fetch_one=connection.fetchrow,
                        fetch_all=connection.fetch,
                    )
                    metadata = await seed(repository, root / "cas")
                    exporter = CountingExporter(ContentAddressedArtifactByteVerifier(
                        ArtifactStoreSettings(backend="local", local_root=root / "cas",
                                              verify_max_bytes=2 * 1024 * 1024),
                    ))
                    app.state.exporter = exporter
                    app.dependency_overrides[get_mission_repository] = lambda: repository
                    app.dependency_overrides[get_mission_artifact_byte_exporter] = lambda: exporter
                    print(json.dumps(metadata, ensure_ascii=True), flush=True)
                    yield
            finally:
                app.dependency_overrides.clear()
                await pool.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = fixture_user

    @app.get("/fixture/health")
    async def health() -> dict:
        return {"ready": True, "fixturePid": os.getpid(), "workspaceId": WORKSPACE_ID,
                "reads": dict(app.state.exporter.reads)}

    return app


if __name__ == "__main__":
    uvicorn.run(build_fixture_app(), host=HOST, port=PORT, access_log=False,
                log_level="warning", timeout_graceful_shutdown=10)
