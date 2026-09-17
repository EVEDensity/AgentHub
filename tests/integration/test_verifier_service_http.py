from __future__ import annotations

import hashlib
import tempfile
import time
import unittest
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.api.v1.missions import (
    get_artifact_byte_verifier,
    get_mission_repository,
    get_verifier_workspace_grant_authorizer,
    router,
)
from app.core.config import ArtifactStoreSettings
from app.services.artifact_integrity_service import (
    ContentAddressedArtifactByteVerifier,
)
from app.services.auth_service import get_current_user
from app.services.verifier_service import (
    ControlledVerifier,
    MissionControlVerifierClient,
)
from app.services.verifier_worker import VerifierWorker
from services.python.verifier_service.config import VerifierServiceSettings
from services.python.verifier_service.main import create_app
from services.python.verifier_service.runtime import (
    LocalArtifactByteVerifier,
    VerifierServiceRuntime,
)
from tests.api.test_missions_api import (
    FakeMissionRepository,
    FakeVerifierWorkspaceGrantAuthorizer,
    artifact_set_criterion,
    recompute_evidence_integrity_hash,
)
from tests.domain.factories import (
    build_artifact,
    build_contract,
    build_mission,
    build_work_unit,
)


def _service_settings(local_root: Path) -> VerifierServiceSettings:
    return VerifierServiceSettings(
        verifier_id="verifier-1",
        verifier_version="artifact-set-runtime.v1",
        workspace_id="workspace-1",
        mission_control_url="http://mission-control.test",
        mission_control_token_file=local_root / "control.token",
        artifact_local_root=local_root,
    )


def _build_mission_control(
    repository: FakeMissionRepository,
    byte_verifier: ContentAddressedArtifactByteVerifier,
) -> FastAPI:
    application = FastAPI()
    application.include_router(router, prefix="/api/v1")
    application.dependency_overrides[get_mission_repository] = lambda: repository
    application.dependency_overrides[get_artifact_byte_verifier] = lambda: byte_verifier
    grant_authorizer = FakeVerifierWorkspaceGrantAuthorizer(
        {("workspace-1", "verifier-1")}
    )
    application.dependency_overrides[get_verifier_workspace_grant_authorizer] = lambda: (
        grant_authorizer
    )

    async def authenticated_verifier(request: Request) -> dict[str, str]:
        if request.headers.get("authorization") != "Bearer verifier-token":
            raise HTTPException(status_code=401, detail="Authentication required")
        return {
            "id": "verifier-1",
            "name": "Independent Verifier",
            "role": "verifier",
        }

    application.dependency_overrides[get_current_user] = authenticated_verifier
    return application


class VerifierServiceHttpIntegrationTests(unittest.TestCase):
    def test_service_records_server_owned_evidence_from_real_local_bytes(self) -> None:
        content = b"verifier service integration Artifact"
        digest_hex = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            local_root = Path(directory).resolve()
            artifact_path = local_root / "sha256" / digest_hex
            artifact_path.parent.mkdir(parents=True)
            artifact_path.write_bytes(content)

            repository = FakeMissionRepository()
            repository.contract = build_contract(
                acceptance_criteria=[
                    artifact_set_criterion(required_artifact_kinds=["diff"])
                ]
            )
            repository.mission = build_mission(
                workspace_id="workspace-1",
                status="RUNNING",
            )
            repository.work_units = [build_work_unit(status="VERIFYING", attempt=1)]
            repository.artifacts = [
                build_artifact(
                    digest=f"sha256:{digest_hex}",
                    content_address=f"local:sha256/{digest_hex}",
                    size_bytes=len(content),
                )
            ]

            artifact_settings = ArtifactStoreSettings(
                backend="local",
                local_root=local_root,
                verify_max_bytes=1024,
            )
            mission_control = _build_mission_control(
                repository,
                ContentAddressedArtifactByteVerifier(artifact_settings),
            )
            control_http = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mission_control),
                follow_redirects=False,
            )
            control = MissionControlVerifierClient(
                "http://mission-control.test",
                access_token="verifier-token",
                http_client=control_http,
            )
            verifier = ControlledVerifier(
                control,
                byte_verifier=LocalArtifactByteVerifier(
                    ContentAddressedArtifactByteVerifier(artifact_settings)
                ),
                verifier_id="verifier-1",
                verifier_version="artifact-set-runtime.v1",
            )
            worker = VerifierWorker(
                verifier,
                workspace_id="workspace-1",
                idle_delay_seconds=0.001,
                max_delay_seconds=0.01,
            )
            runtime = VerifierServiceRuntime(
                worker=worker,
                shutdown_timeout_seconds=1,
                closeables=(control_http,),
            )
            application = create_app(
                _service_settings(local_root),
                runtime_factory=lambda _: runtime,
            )

            with TestClient(application) as client:
                deadline = time.monotonic() + 2
                ready = client.get("/readyz")
                while ready.status_code != 200 and time.monotonic() < deadline:
                    time.sleep(0.01)
                    ready = client.get("/readyz")

                self.assertEqual(client.get("/healthz").status_code, 200)
                self.assertEqual(ready.status_code, 200)
                self.assertEqual(ready.json()["worker"]["verified"], 1)

            self.assertTrue(control_http.is_closed)
            self.assertEqual(len(repository.evidence), 1)
            evidence = repository.evidence[0]
            self.assertEqual(evidence.verifier.id, "verifier-1")
            self.assertEqual(evidence.verdict.value, "PASS")
            self.assertEqual(
                evidence.integrity_hash,
                recompute_evidence_integrity_hash(repository, evidence),
            )
            self.assertEqual(repository.work_units[0].status.value, "SUCCEEDED")
            assert repository.mission is not None
            self.assertEqual(repository.mission.status.value, "SUCCEEDED")


if __name__ == "__main__":
    unittest.main()
