"""Owned loopback fixture: actual pytest log producer, SQLite and verified CAS.

Run as ``python -m tests.integration.artifact_log_http_fixture``. This module
is test-only: fixed commands and identities never enter production routing.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import uvicorn
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.v1.missions import get_mission_artifact_byte_exporter, get_mission_repository, router
from app.core.config import ArtifactStoreSettings
from app.db.init_db import _create_mission_control_plane_sqlite
from app.db.sqlite_pool import SQLitePool
from app.domain import ActorRef, ArtifactRef, MissionSource
from app.repositories import MissionRepository
from app.services.artifact_integrity_service import ContentAddressedArtifactByteVerifier
from app.services.artifact_store_service import ContentAddressedArtifactPublisher
from app.services.auth_service import get_current_user
from app.services.mission_service import MissionService
from app.services.trusted_tests.producer import ProductionContext, TrustedLocalTestProducer
from app.services.trusted_tests.profile import freeze_test_profile
from app.services.trusted_tests.snapshot import capture_candidate, freeze_snapshot, materialize_candidate
from tests.domain.factories import build_contract, build_work_unit
from tests.integration.artifact_preview_http_fixture import CountingExporter

HOST, PORT = "127.0.0.1", 38100
WORKSPACE_ID, MISSION_ID, WORK_UNIT_ID = "workspace-log", "mis-log-browser", "wu-log-browser"
RUNNER = ActorRef(type="runner", id="log-fixture-runner")
BOUNDED_TAIL = "fixture-log-bounded-tail-2001"
_BOUNDED_LINES = [f"fixture-line-{index:04d}" for index in range(2002)]
_BOUNDED_LINES[2000], _BOUNDED_LINES[2001] = "界" * 2500, BOUNDED_TAIL
BOUNDED_TEXT = "\n".join(_BOUNDED_LINES)
_SECURITY = HTTPBearer(auto_error=False)
_ACCEPTANCE = r'''import sys

def test_deliberate_log_fixture_failure():
    print("\x1b[32m真实 stdout 中文\x1b[0m", flush=True)
    print('<script>window.LOG_PREVIEW_EXECUTED=true</script><img src="/fixture/should-never-load.png" onerror="window.LOG_PREVIEW_EXECUTED=true">', flush=True)
    print("\x1b]52;c;Zml4dHVyZS1vbmx5\x07\x1b]8;;https://invalid.example/log-link\x07inert link\x1b]8;;\x07", flush=True)
    print("\x1b]0;fixture-title-must-not-change-host\x07progress 10%\rprogress 100%", flush=True)
    sys.stderr.write("真实 stderr 中文\n")
    sys.stderr.flush()
    assert False, "Intentional fixture failure: captured output is not PASS evidence"
'''


async def fixture_user(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_SECURITY)]) -> dict:
    if credentials and credentials.scheme.lower() == "bearer":
        if credentials.credentials == "artifact-log-human":
            return {"id": WORKSPACE_ID, "role": "developer", "name": "Log fixture human"}
        if credentials.credentials == "artifact-log-foreign":
            return {"id": "workspace-log-foreign", "role": "developer", "name": "Foreign log human"}
    raise HTTPException(401, "Fixture authentication required")


async def produce_log(root: Path) -> bytes:
    """Run frozen external pytest through the production producer, without a mock backend."""
    source, suite, storage = (root / name for name in ("source", "suite", "producer"))
    for directory in (source, suite, storage):
        directory.mkdir()
    (source / "candidate.py").write_text("value = 1\n", encoding="utf-8")
    (suite / "test_acceptance.py").write_text(_ACCEPTANCE, encoding="utf-8")
    with freeze_snapshot(source, storage) as snapshot, freeze_test_profile(
        suite, (sys.executable, "-m", "pytest", "-q"), storage,
        execution_mode="trusted-local", timeout_seconds=15, max_output_bytes=65536,
    ) as profile:
        candidate = materialize_candidate(snapshot, root / "candidate")
        context = ProductionContext(
            MISSION_ID, WORK_UNIT_ID, 1, snapshot, capture_candidate(snapshot, candidate),
            profile, "contract-log-browser", 1, "sha256:" + "a" * 64,
        )
        bundle = await TrustedLocalTestProducer(
            producer_id="log-fixture-producer", key_id="log-fixture-ephemeral",
            private_key=Ed25519PrivateKey.generate(), scratch_root=storage,
        ).produce(context)
    if bundle.conclusion != "failed":
        raise RuntimeError("Expected the deliberate external fixture test failure")
    content = next(item.content for item in bundle.artifacts if item.kind == "log")
    # A successful fixture must prove capture, not substitute canned terminal text.
    text = content.decode("utf-8", errors="strict")
    if not all(marker in text for marker in ("真实 stdout 中文", "真实 stderr 中文", "\x1b[32m", "<script>")):
        raise RuntimeError("Actual pytest output did not preserve the fixture markers")
    return content


async def seed(repository: MissionRepository, root: Path, store: ArtifactStoreSettings) -> dict:
    service = MissionService(repository)
    await service.create_mission(
        mission_id=MISSION_ID, workspace_id=WORKSPACE_ID, title="Registered pytest log fixture",
        objective="Read registered process output; the deliberate pytest failure establishes no acceptance.",
        source=MissionSource(type="manual"), contract=build_contract(id="contract-log-browser"),
        actor=ActorRef(type="human", id=WORKSPACE_ID),
    )
    await service.start_mission(MISSION_ID, actor=ActorRef(type="human", id=WORKSPACE_ID))
    await repository.add_work_unit(build_work_unit(
        id=WORK_UNIT_ID, mission_id=MISSION_ID, expected_outputs=[], required_capabilities=[],
    ))
    # Produce first so slow process startup cannot consume the registration lease.
    content = await produce_log(root)
    unit = await service.lease_work_unit(MISSION_ID, WORK_UNIT_ID, runner_id=RUNNER.id, actor=RUNNER, lease_seconds=60)
    await service.start_work_unit(MISSION_ID, WORK_UNIT_ID, lease_id=unit.lease.id, runner_id=RUNNER.id, actor=RUNNER)
    values = (
        ("real", content, "internal", "production-trusted-test-producer"),
        ("empty", b"", "internal", "artificial-boundary-fixture"),
        ("large", b"L" * 262145, "internal", "artificial-boundary-fixture"),
        ("denied", b"Confidential artificial boundary fixture", "confidential", "artificial-boundary-fixture"),
        ("tampered", b"Registered before intentional corruption", "internal", "artificial-boundary-fixture"),
        ("missing", b"Registered before intentional removal", "internal", "artificial-boundary-fixture"),
        ("invalid", b"Invalid UTF-8: \xff", "internal", "artificial-boundary-fixture"),
        ("bounded", BOUNDED_TEXT.encode("utf-8"), "internal", "artificial-boundary-fixture"),
    )
    publisher = ContentAddressedArtifactPublisher(store)
    metadata, references = {}, []
    for name, value, sensitivity, origin in values:
        published = await publisher.publish_bytes(value)
        artifact = await service.register_artifact(
            MISSION_ID, WORK_UNIT_ID, artifact_id=f"artifact-log-{name}", lease_id=unit.lease.id,
            runner_id=RUNNER.id, kind="log", digest=published.digest,
            content_address=published.content_address, media_type="text/plain", size_bytes=published.size_bytes,
            source_repository=None, base_commit=None, retention="ephemeral", sensitivity=sensitivity, actor=RUNNER,
        )
        metadata[name] = {"id": artifact.id, "digest": artifact.digest, "sizeBytes": artifact.size_bytes, "source": origin}
        references.append(ArtifactRef(id=artifact.id, digest=artifact.digest))
        path = store.local_root / "sha256" / artifact.digest[7:]
        if name == "tampered":
            path.write_bytes(b"Intentionally corrupted fixture bytes")
        elif name == "missing":
            path.unlink()
    await service.complete_work_unit(MISSION_ID, WORK_UNIT_ID, lease_id=unit.lease.id,
                                    runner_id=RUNNER.id, actor=RUNNER, artifact_refs=references)
    return metadata


def build_fixture_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        with tempfile.TemporaryDirectory(prefix="agenthub-artifact-log-") as temporary:
            root = Path(temporary).resolve()
            pool = SQLitePool(root / "missions.sqlite3")
            try:
                await pool.initialize()
                async with pool.acquire() as connection:
                    await _create_mission_control_plane_sqlite(connection)

                    @asynccontextmanager
                    async def transaction():
                        async with connection.transaction():
                            yield connection

                    repository = MissionRepository(execute=connection.execute, fetch_one=connection.fetchrow,
                                                   fetch_all=connection.fetch, transaction_factory=transaction)
                    store = ArtifactStoreSettings(backend="local", local_root=root / "cas", verify_max_bytes=2 * 1024 * 1024)
                    metadata = await seed(repository, root, store)
                    app.state.root, app.state.repository, app.state.artifacts = root, repository, metadata
                    app.state.exporter = CountingExporter(ContentAddressedArtifactByteVerifier(store))
                    app.dependency_overrides[get_mission_repository] = lambda: repository
                    app.dependency_overrides[get_mission_artifact_byte_exporter] = lambda: app.state.exporter
                    print(json.dumps({"port": PORT, "missionId": MISSION_ID, "workspaceId": WORKSPACE_ID,
                                      "workUnitId": WORK_UNIT_ID, "artifacts": metadata}), flush=True)
                    yield
            finally:
                app.dependency_overrides.clear()
                await pool.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = fixture_user

    @app.get("/fixture/health")
    async def health():
        return {"ready": True, "fixturePid": os.getpid(), "workspaceId": WORKSPACE_ID,
                "missionId": MISSION_ID, "workUnitId": WORK_UNIT_ID,
                "reads": dict(app.state.exporter.reads), "artifacts": app.state.artifacts}

    return app


if __name__ == "__main__":
    uvicorn.run(build_fixture_app(), host=HOST, port=PORT, access_log=False,
                log_level="warning", timeout_graceful_shutdown=10)
