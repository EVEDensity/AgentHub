from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.services.artifact_integrity_service import (
    ArtifactBytesUnavailableError,
    ArtifactIntegrityError,
)
from app.services.artifact_preview_service import PREVIEW_MAX_BYTES, read_artifact_text_preview
from tests.api.artifact_preview_fixture import (
    HUMAN,
    PREVIEW_PATH,
    PreviewExporter,
    PreviewRepository,
    preview_app,
    text_artifact,
)


class ArtifactPreviewAPITests(unittest.TestCase):
    def setUp(self):
        self.content = "print('你好 😀')\n".encode()
        self.repository = PreviewRepository(text_artifact(self.content))
        self.exporter = PreviewExporter(self.content)

    def request(self, *, user=HUMAN, path=PREVIEW_PATH):
        with TestClient(preview_app(self.repository, self.exporter, user=user)) as client:
            return client.get(path)

    def assert_private_headers(self, response):
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_verified_unicode_has_only_narrow_registered_projection(self):
        response = self.request(path=PREVIEW_PATH + "?path=C:/secret&url=http://untrusted")
        self.assertEqual(response.status_code, 200)
        artifact = self.repository.artifact
        self.assertEqual(response.json(), {
            "schemaVersion": 1,
            "artifact": {
                "id": artifact.id, "missionId": artifact.mission_id,
                "workUnitId": artifact.work_unit_id, "attempt": artifact.attempt,
                "kind": artifact.kind.value, "mediaType": artifact.media_type,
                "sizeBytes": len(self.content), "digest": artifact.digest,
            },
            "encoding": "utf-8", "integrity": "verified", "text": self.content.decode(),
        })
        self.assertEqual(self.exporter.calls, [(artifact.id, PREVIEW_MAX_BYTES)])
        self.assert_private_headers(response)

    def test_non_human_roles_are_denied_before_repository_and_bytes(self):
        for role in ("runner", "verifier", "agent", "service"):
            with self.subTest(role=role):
                response = self.request(user={**HUMAN, "role": role})
                self.assertEqual(response.status_code, 403)
                self.assert_private_headers(response)
        self.assertEqual(self.repository.reads, [])
        self.assertEqual(self.exporter.calls, [])

    def test_workspace_acl_runs_before_artifact_lookup(self):
        response = self.request(user={**HUMAN, "id": "different-workspace"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.repository.reads, [("mission", "mis-1")])
        self.assertEqual(self.exporter.calls, [])
        self.assert_private_headers(response)

    def test_human_admin_can_use_existing_workspace_authority(self):
        response = self.request(user={"id": "admin-1", "role": "admin"})
        self.assertEqual(response.status_code, 200)

    def test_authentication_is_required(self):
        response = self.request(user=None)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.exporter.calls, [])

    def test_absent_mission_does_not_lookup_artifact(self):
        self.repository.mission = None
        response = self.request()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.repository.reads, [("mission", "mis-1")])
        self.assertEqual(self.exporter.calls, [])

    def test_missing_or_other_mission_artifact_is_identical_404_without_io(self):
        for artifact in (None, text_artifact(self.content, mission_id="mis-other")):
            with self.subTest(artifact=artifact):
                self.repository.artifact = artifact
                response = self.request()
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), {"detail": "Artifact not found"})
                self.assert_private_headers(response)
        self.assertEqual(self.exporter.calls, [])

    def test_sensitive_artifacts_are_denied_before_reading(self):
        for sensitivity in ("confidential", "restricted"):
            with self.subTest(sensitivity=sensitivity):
                self.repository.artifact = text_artifact(self.content, sensitivity=sensitivity)
                response = self.request()
                self.assertEqual(response.status_code, 403)
                self.assert_private_headers(response)
        self.assertEqual(self.exporter.calls, [])

    def test_supported_media_types_and_exportable_sensitivity(self):
        for media_type in (
            "text/x-python", "text/html; charset=utf-8", "text/markdown",
            "application/json", "application/ld+json", "application/javascript",
            "application/x-python",
        ):
            with self.subTest(media_type=media_type):
                self.repository.artifact = text_artifact(
                    self.content, media_type=media_type, sensitivity="public",
                )
                response = self.request()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["text"], self.content.decode())

    def test_unsupported_media_types_are_denied_before_reading(self):
        for media_type in ("application/octet-stream", "image/svg+xml", "text/", "text/a/b"):
            with self.subTest(media_type=media_type):
                self.repository.artifact = text_artifact(self.content, media_type=media_type)
                response = self.request()
                self.assertEqual(response.status_code, 415)
        self.assertEqual(self.exporter.calls, [])

    def test_declared_oversize_is_denied_before_reading(self):
        self.repository.artifact = text_artifact(b"x", size_bytes=PREVIEW_MAX_BYTES + 1)
        response = self.request()
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.exporter.calls, [])

    def test_exact_byte_limit_and_empty_verified_text_are_allowed(self):
        for content in (b"", b"x" * PREVIEW_MAX_BYTES):
            with self.subTest(size=len(content)):
                self.repository.artifact = text_artifact(content)
                self.exporter.content = content
                response = self.request()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["text"], content.decode())

    def test_invalid_utf8_and_nul_are_rejected_after_integrity(self):
        for content in (b"\xff", b"hello\x00there", b"\xc0\xaf"):
            with self.subTest(content=content):
                self.repository.artifact = text_artifact(content)
                self.exporter.content = content
                response = self.request()
                self.assertEqual(response.status_code, 415)
                self.assertNotIn("text", response.json())

    def test_exporter_cannot_bypass_size_or_digest_verification(self):
        for content in (b"wrong", b"x" * len(self.content)):
            with self.subTest(size=len(content)):
                self.exporter.content = content
                response = self.request()
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json(), {"detail": "Artifact preview integrity mismatch"})

    def test_exporter_cannot_return_oversize_or_non_bytes(self):
        for content, expected in ((b"x" * (PREVIEW_MAX_BYTES + 1), 413), ("pretend", 503)):
            with self.subTest(expected=expected):
                self.exporter.content = content
                response = self.request()
                self.assertEqual(response.status_code, expected)

    def test_storage_and_integrity_errors_are_static_and_private(self):
        for error, expected in (
            (ArtifactBytesUnavailableError("secret path local:sha256/private"), 503),
            (RuntimeError("secret credentials or storage address"), 503),
            (ArtifactIntegrityError("secret digest details"), 409),
        ):
            with self.subTest(error=type(error).__name__):
                self.exporter.error = error
                response = self.request()
                self.assertEqual(response.status_code, expected)
                self.assertNotIn("secret", response.text)
                self.assertNotIn("local:", response.text)
                self.assert_private_headers(response)

    def test_read_deadline_cancels_async_export_and_returns_unavailable(self):
        class BlockingExporter:
            cancelled = False

            async def read_verified(self, artifact, *, max_bytes):
                try:
                    await asyncio.sleep(60)
                finally:
                    self.cancelled = True

        self.exporter = BlockingExporter()
        with patch("app.services.artifact_preview_service.PREVIEW_TIMEOUT_SECONDS", 0.01):
            response = self.request()
        self.assertEqual(response.status_code, 503)
        self.assertTrue(self.exporter.cancelled)
        self.assert_private_headers(response)


class ArtifactPreviewCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_caller_cancellation_propagates_without_preview_result(self):
        entered = asyncio.Event()
        exited = asyncio.Event()

        class BlockingExporter:
            async def read_verified(self, artifact, *, max_bytes):
                entered.set()
                try:
                    await asyncio.sleep(60)
                finally:
                    exited.set()

        task = asyncio.create_task(read_artifact_text_preview(
            text_artifact(b"bounded"), exporter=BlockingExporter(),
        ))
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(exited.is_set())
