from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.services.artifact_integrity_service import (
    ArtifactBytesUnavailableError,
    ContentAddressedArtifactByteVerifier,
)
from app.services.verifier_service import VerifierPollStatus
from app.services.verifier_worker import VerifierWorkerSnapshot
from services.python.verifier_service.config import (
    VerifierServiceSettings,
    read_secret_file,
)
from services.python.verifier_service.main import create_app
from services.python.verifier_service.runtime import (
    LocalArtifactByteVerifier,
    LocalArtifactVerificationSettings,
    VerifierServiceRuntime,
    build_verifier_runtime,
)


def _settings(**updates: Any) -> VerifierServiceSettings:
    root = Path.cwd().resolve()
    values: dict[str, Any] = {
        "verifier_id": "verifier-1",
        "verifier_version": "artifact-set-runtime.v1",
        "workspace_id": "workspace-1",
        "mission_control_url": "https://control.example.test",
        "mission_control_token_file": root / "control.token",
        "artifact_local_root": root / "artifacts",
    }
    values.update(updates)
    return VerifierServiceSettings(**values)


class FakeWorker:
    def __init__(self, *, stop_on_request: bool = True) -> None:
        self._snapshot = VerifierWorkerSnapshot()
        self._release = asyncio.Event()
        self.stop_on_request = stop_on_request
        self.stop_requests = 0
        self.cancelled = False

    @property
    def snapshot(self) -> VerifierWorkerSnapshot:
        return self._snapshot

    async def run(self) -> None:
        self._snapshot = replace(self._snapshot, running=True, ready=True)
        try:
            await self._release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self._snapshot = replace(self._snapshot, running=False, ready=False)

    def request_stop(self) -> None:
        self.stop_requests += 1
        self._snapshot = replace(self._snapshot, stop_requested=True)
        if self.stop_on_request:
            self._release.set()


class CloseRecorder:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


@dataclass(frozen=True)
class ArtifactDescriptor:
    id: str
    digest: str
    content_address: str
    size_bytes: int


class RecordingByteVerifier:
    def __init__(self) -> None:
        self.calls = 0

    async def verify_all(self, artifacts: Any) -> list[Any]:
        del artifacts
        self.calls += 1
        return []


class VerifierServiceConfigurationTests(unittest.TestCase):
    def test_requires_explicit_identity_control_and_storage_configuration(self) -> None:
        required_fields = (
            "verifier_id",
            "verifier_version",
            "workspace_id",
            "mission_control_url",
            "mission_control_token_file",
            "artifact_local_root",
        )
        base = _settings().model_dump()
        for field in required_fields:
            values = dict(base)
            values.pop(field)
            with self.subTest(field=field), self.assertRaises(ValidationError):
                VerifierServiceSettings(**values)

    def test_rejects_unsafe_urls_paths_and_poll_policy(self) -> None:
        invalid = (
            {"mission_control_url": "control.example.test"},
            {"mission_control_url": "https://user:secret@control.example.test"},
            {"mission_control_url": "https://control.example.test?token=secret"},
            {"mission_control_token_file": Path("control.token")},
            {"artifact_local_root": Path("artifacts")},
            {"idle_delay_seconds": 2, "max_delay_seconds": 1},
        )
        for updates in invalid:
            with self.subTest(updates=updates), self.assertRaises(ValidationError):
                _settings(**updates)

    def test_plaintext_token_is_not_a_supported_configuration_field(self) -> None:
        values = _settings().model_dump()
        values.pop("mission_control_token_file")
        values["mission_control_token"] = "plaintext-secret"
        with self.assertRaises(ValidationError):
            VerifierServiceSettings(**values)

    def test_secret_file_is_bounded_and_single_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "valid.token"
            valid.write_text("verifier-token\n", encoding="utf-8")
            self.assertEqual(read_secret_file(valid), "verifier-token")

            multiline = root / "multiline.token"
            multiline.write_text("first\nsecond", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "one non-empty value"):
                read_secret_file(multiline)

            oversized = root / "oversized.token"
            oversized.write_bytes(b"x" * (64 * 1024 + 1))
            with self.assertRaisesRegex(ValueError, "size is invalid"):
                read_secret_file(oversized)


class LocalArtifactByteVerifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_non_local_content_before_delegate_io(self) -> None:
        delegate = RecordingByteVerifier()
        verifier = LocalArtifactByteVerifier(delegate)
        artifact = ArtifactDescriptor(
            id="artifact-1",
            digest="sha256:" + "a" * 64,
            content_address="minio://agenthub/sha256/" + "a" * 64,
            size_bytes=10,
        )

        with self.assertRaisesRegex(
            ArtifactBytesUnavailableError,
            "only mounted local",
        ):
            await verifier.verify_all((artifact,))

        self.assertEqual(delegate.calls, 0)

    async def test_local_settings_cannot_activate_implicit_minio_access(self) -> None:
        verifier = ContentAddressedArtifactByteVerifier(
            LocalArtifactVerificationSettings(
                local_root=Path.cwd().resolve(),
                verify_max_bytes=1024,
            )
        )
        artifact = ArtifactDescriptor(
            id="artifact-1",
            digest="sha256:" + "a" * 64,
            content_address="minio://agenthub/sha256/" + "a" * 64,
            size_bytes=10,
        )

        with self.assertRaisesRegex(
            ArtifactBytesUnavailableError,
            "MinIO Artifact verification is not configured",
        ):
            await verifier.verify_all((artifact,))


class VerifierServiceRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_strict_composition_loads_file_backed_control_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            token_file = root / "control.token"
            token_file.write_text("verifier-token\n", encoding="utf-8")
            settings = _settings(
                mission_control_token_file=token_file,
                artifact_local_root=root / "artifacts",
            )

            runtime = build_verifier_runtime(settings)
            self.assertFalse(runtime.healthy)
            self.assertFalse(runtime.ready)
            self.assertEqual(len(runtime.closeables), 1)
            await runtime.stop()
            self.assertTrue(runtime.closeables[0].is_closed)  # type: ignore[attr-defined]

    async def test_graceful_stop_waits_then_closes_resources(self) -> None:
        worker = FakeWorker()
        closeable = CloseRecorder()
        runtime = VerifierServiceRuntime(
            worker=worker,
            shutdown_timeout_seconds=1,
            closeables=(closeable,),
        )

        await runtime.start()
        self.assertTrue(runtime.healthy)
        self.assertTrue(runtime.ready)
        await runtime.stop()

        self.assertEqual(worker.stop_requests, 1)
        self.assertFalse(worker.cancelled)
        self.assertTrue(closeable.closed)
        self.assertFalse(runtime.healthy)

    async def test_shutdown_deadline_cancels_stuck_evaluation(self) -> None:
        worker = FakeWorker(stop_on_request=False)
        runtime = VerifierServiceRuntime(
            worker=worker,
            shutdown_timeout_seconds=0.01,
        )

        await runtime.start()
        await runtime.stop()

        self.assertEqual(worker.stop_requests, 1)
        self.assertTrue(worker.cancelled)
        self.assertFalse(runtime.healthy)


class VerifierServiceEndpointTests(unittest.TestCase):
    def test_health_and_readiness_expose_only_operational_state(self) -> None:
        worker = FakeWorker()
        worker._snapshot = replace(
            worker.snapshot,
            verified=3,
            last_poll_status=VerifierPollStatus.VERIFIED,
        )
        runtime = VerifierServiceRuntime(
            worker=worker,
            shutdown_timeout_seconds=1,
        )
        application = create_app(
            _settings(),
            runtime_factory=lambda _: runtime,
        )

        with TestClient(application) as client:
            health = client.get("/healthz")
            ready = client.get("/readyz")

            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["status"], "ok")
            self.assertEqual(ready.status_code, 200)
            self.assertEqual(ready.json()["status"], "ready")
            self.assertEqual(ready.json()["worker"]["lastPollStatus"], "verified")
            self.assertEqual(ready.json()["worker"]["verified"], 3)
            rendered = ready.text
            self.assertNotIn("workspace-1", rendered)
            self.assertNotIn("verifier-1", rendered)
            self.assertNotIn("control.example", rendered)

        self.assertEqual(worker.stop_requests, 1)


if __name__ == "__main__":
    unittest.main()
