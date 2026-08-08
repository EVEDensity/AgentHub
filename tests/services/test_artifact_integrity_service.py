from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import ArtifactStoreSettings
from app.services.artifact_integrity_service import (
    ArtifactBytesUnavailableError,
    ArtifactIntegrityError,
    ContentAddressedArtifactByteVerifier,
)
from tests.domain.factories import build_artifact


class FakeMinioResponse:
    def __init__(
        self,
        chunks: list[bytes],
        *,
        close_error: Exception | None = None,
    ) -> None:
        self._chunks = chunks
        self._close_error = close_error
        self.stream_amount: int | None = None
        self.closed = False
        self.released = False

    def stream(self, *, amt: int):
        self.stream_amount = amt
        yield from self._chunks

    def close(self) -> None:
        self.closed = True
        if self._close_error is not None:
            raise self._close_error

    def release_conn(self) -> None:
        self.released = True


class FakeMinioClient:
    def __init__(
        self,
        response: FakeMinioResponse | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def get_object(self, bucket: str, key: str) -> FakeMinioResponse:
        self.calls.append((bucket, key))
        if self.error is not None:
            raise self.error
        if self.response is None:
            raise AssertionError("fake MinIO response is not configured")
        return self.response


class ArtifactIntegrityServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.local_root = Path(self.temporary_directory.name)

    def build_settings(self, *, verify_max_bytes: int = 1024) -> ArtifactStoreSettings:
        return ArtifactStoreSettings(
            local_root=self.local_root,
            minio_endpoint="minio:9000",
            minio_access_key="test-access",
            minio_secret_key="test-secret",
            minio_bucket="agenthub",
            minio_secure=False,
            verify_max_bytes=verify_max_bytes,
        )

    def write_local_artifact(self, content: bytes) -> tuple[str, Path]:
        digest_hex = hashlib.sha256(content).hexdigest()
        artifact_path = self.local_root / "sha256" / digest_hex
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(content)
        return digest_hex, artifact_path

    async def test_verifies_valid_local_bytes(self) -> None:
        content = b"verified artifact bytes"
        digest_hex, _ = self.write_local_artifact(content)
        artifact = build_artifact(
            digest=f"sha256:{digest_hex}",
            content_address=f"local:sha256/{digest_hex}",
            size_bytes=len(content),
        )
        verifier = ContentAddressedArtifactByteVerifier(self.build_settings())

        result = await verifier.verify(artifact)

        self.assertEqual(result.artifact_id, artifact.id)
        self.assertEqual(result.digest, f"sha256:{digest_hex}")
        self.assertEqual(result.size_bytes, len(content))

    async def test_missing_local_bytes_are_unavailable(self) -> None:
        digest_hex = hashlib.sha256(b"missing").hexdigest()
        artifact = build_artifact(
            digest=f"sha256:{digest_hex}",
            content_address=f"local:sha256/{digest_hex}",
            size_bytes=7,
        )
        verifier = ContentAddressedArtifactByteVerifier(self.build_settings())

        with self.assertRaisesRegex(
            ArtifactBytesUnavailableError,
            "artifact bytes are unavailable",
        ):
            await verifier.verify(artifact)

    async def test_local_size_mismatch_fails_integrity(self) -> None:
        content = b"size mismatch"
        digest_hex, _ = self.write_local_artifact(content)
        artifact = build_artifact(
            digest=f"sha256:{digest_hex}",
            content_address=f"local:sha256/{digest_hex}",
            size_bytes=len(content) + 1,
        )
        verifier = ContentAddressedArtifactByteVerifier(self.build_settings())

        with self.assertRaisesRegex(ArtifactIntegrityError, "byte size"):
            await verifier.verify(artifact)

    async def test_local_digest_mismatch_fails_integrity(self) -> None:
        content = b"actual bytes"
        actual_digest, path = self.write_local_artifact(content)
        expected_digest = hashlib.sha256(b"expected bytes").hexdigest()
        expected_path = path.with_name(expected_digest)
        path.replace(expected_path)
        artifact = build_artifact(
            digest=f"sha256:{expected_digest}",
            content_address=f"local:sha256/{expected_digest}",
            size_bytes=len(content),
        )
        verifier = ContentAddressedArtifactByteVerifier(self.build_settings())

        with self.assertRaisesRegex(ArtifactIntegrityError, "byte digest"):
            await verifier.verify(artifact)
        self.assertNotEqual(actual_digest, expected_digest)

    async def test_local_address_digest_mismatch_fails_before_read(self) -> None:
        digest_hex = hashlib.sha256(b"registered").hexdigest()
        other_digest = hashlib.sha256(b"other").hexdigest()
        artifact = build_artifact(
            digest=f"sha256:{digest_hex}",
            content_address=f"local:sha256/{other_digest}",
            size_bytes=10,
        )
        verifier = ContentAddressedArtifactByteVerifier(self.build_settings())

        with self.assertRaisesRegex(ArtifactIntegrityError, "address digest"):
            await verifier.verify(artifact)

    async def test_declared_size_over_limit_fails_before_read(self) -> None:
        digest_hex = hashlib.sha256(b"oversized").hexdigest()
        artifact = build_artifact(
            digest=f"sha256:{digest_hex}",
            content_address=f"local:sha256/{digest_hex}",
            size_bytes=9,
        )
        verifier = ContentAddressedArtifactByteVerifier(
            self.build_settings(verify_max_bytes=8)
        )

        with self.assertRaisesRegex(ArtifactIntegrityError, "verification limit"):
            await verifier.verify(artifact)

    async def test_streamed_bytes_over_limit_fail_integrity(self) -> None:
        content = b"oversized"
        digest_hex, _ = self.write_local_artifact(content)
        artifact = build_artifact(
            digest=f"sha256:{digest_hex}",
            content_address=f"local:sha256/{digest_hex}",
            size_bytes=8,
        )
        verifier = ContentAddressedArtifactByteVerifier(
            self.build_settings(verify_max_bytes=8)
        )

        with self.assertRaisesRegex(ArtifactIntegrityError, "verification limit"):
            await verifier.verify(artifact)

    async def test_http_content_address_fails_closed(self) -> None:
        digest_hex = hashlib.sha256(b"remote").hexdigest()
        artifact = build_artifact(
            digest=f"sha256:{digest_hex}",
            content_address=f"https://example.com/{digest_hex}",
            size_bytes=6,
        )
        verifier = ContentAddressedArtifactByteVerifier(self.build_settings())

        with self.assertRaisesRegex(
            ArtifactBytesUnavailableError,
            "unsupported artifact content address",
        ):
            await verifier.verify(artifact)

    async def test_verifies_valid_minio_bytes_and_releases_response(self) -> None:
        content = b"minio artifact bytes"
        digest_hex = hashlib.sha256(content).hexdigest()
        key = f"artifacts/{digest_hex}/result.bin"
        response = FakeMinioResponse([content[:5], content[5:]])
        client = FakeMinioClient(response)
        artifact = build_artifact(
            digest=f"sha256:{digest_hex}",
            content_address=f"minio://agenthub/{key}",
            size_bytes=len(content),
        )
        verifier = ContentAddressedArtifactByteVerifier(
            self.build_settings(),
            minio_client_factory=lambda: client,
        )

        result = await verifier.verify(artifact)

        self.assertEqual(result.digest, f"sha256:{digest_hex}")
        self.assertEqual(client.calls, [("agenthub", key)])
        self.assertEqual(response.stream_amount, 1024 * 1024)
        self.assertTrue(response.closed)
        self.assertTrue(response.released)

    async def test_wrong_minio_bucket_is_unavailable(self) -> None:
        content = b"wrong bucket"
        digest_hex = hashlib.sha256(content).hexdigest()
        artifact = build_artifact(
            digest=f"sha256:{digest_hex}",
            content_address=f"minio://other/{digest_hex}/result.bin",
            size_bytes=len(content),
        )
        client = FakeMinioClient(FakeMinioResponse([content]))
        verifier = ContentAddressedArtifactByteVerifier(
            self.build_settings(),
            minio_client_factory=lambda: client,
        )

        with self.assertRaisesRegex(ArtifactBytesUnavailableError, "bucket"):
            await verifier.verify(artifact)
        self.assertEqual(client.calls, [])

    async def test_minio_parent_segment_is_rejected(self) -> None:
        content = b"traversal"
        digest_hex = hashlib.sha256(content).hexdigest()
        artifact = build_artifact(
            digest=f"sha256:{digest_hex}",
            content_address=f"minio://agenthub/artifacts/../{digest_hex}/result.bin",
            size_bytes=len(content),
        )
        client = FakeMinioClient(FakeMinioResponse([content]))
        verifier = ContentAddressedArtifactByteVerifier(
            self.build_settings(),
            minio_client_factory=lambda: client,
        )

        with self.assertRaisesRegex(ArtifactIntegrityError, "address digest"):
            await verifier.verify(artifact)
        self.assertEqual(client.calls, [])

    async def test_missing_minio_object_is_unavailable(self) -> None:
        content = b"missing object"
        digest_hex = hashlib.sha256(content).hexdigest()
        artifact = build_artifact(
            digest=f"sha256:{digest_hex}",
            content_address=f"minio://agenthub/{digest_hex}/result.bin",
            size_bytes=len(content),
        )
        client = FakeMinioClient(error=OSError("not found"))
        verifier = ContentAddressedArtifactByteVerifier(
            self.build_settings(),
            minio_client_factory=lambda: client,
        )

        with self.assertRaisesRegex(
            ArtifactBytesUnavailableError,
            "artifact bytes are unavailable",
        ):
            await verifier.verify(artifact)

    async def test_corrupt_minio_bytes_fail_and_release_response(self) -> None:
        expected = b"expected object"
        corrupt = b"corrupted object"
        digest_hex = hashlib.sha256(expected).hexdigest()
        response = FakeMinioResponse([corrupt])
        client = FakeMinioClient(response)
        artifact = build_artifact(
            digest=f"sha256:{digest_hex}",
            content_address=f"minio://agenthub/{digest_hex}/result.bin",
            size_bytes=len(corrupt),
        )
        verifier = ContentAddressedArtifactByteVerifier(
            self.build_settings(),
            minio_client_factory=lambda: client,
        )

        with self.assertRaisesRegex(ArtifactIntegrityError, "byte digest"):
            await verifier.verify(artifact)
        self.assertTrue(response.closed)
        self.assertTrue(response.released)

    async def test_minio_cleanup_error_does_not_hide_valid_verification(self) -> None:
        content = b"verified despite cleanup error"
        digest_hex = hashlib.sha256(content).hexdigest()
        response = FakeMinioResponse(
            [content],
            close_error=OSError("close failed"),
        )
        client = FakeMinioClient(response)
        artifact = build_artifact(
            digest=f"sha256:{digest_hex}",
            content_address=f"minio://agenthub/{digest_hex}/result.bin",
            size_bytes=len(content),
        )
        verifier = ContentAddressedArtifactByteVerifier(
            self.build_settings(),
            minio_client_factory=lambda: client,
        )

        result = await verifier.verify(artifact)

        self.assertEqual(result.digest, f"sha256:{digest_hex}")
        self.assertTrue(response.closed)
        self.assertTrue(response.released)

    def test_settings_read_artifact_environment_aliases(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AGENTHUB_ARTIFACT_LOCAL_ROOT": str(self.local_root),
                "AGENTHUB_ARTIFACT_STORE_BACKEND": "minio",
                "AGENTHUB_ARTIFACT_PUBLISH_MAX_BYTES": "4096",
                "AGENTHUB_ARTIFACT_VERIFY_MAX_BYTES": "2048",
                "MINIO_ENDPOINT": "storage:9000",
                "MINIO_ACCESS_KEY": "access",
                "MINIO_SECRET_KEY": "secret",
                "MINIO_BUCKET": "verified-artifacts",
                "MINIO_SECURE": "true",
            },
        ):
            settings = ArtifactStoreSettings()

        self.assertEqual(settings.local_root, self.local_root)
        self.assertEqual(settings.backend, "minio")
        self.assertEqual(settings.publish_max_bytes, 4096)
        self.assertEqual(settings.verify_max_bytes, 2048)
        self.assertEqual(settings.minio_endpoint, "storage:9000")
        self.assertEqual(settings.minio_bucket, "verified-artifacts")
        self.assertTrue(settings.minio_secure)
        self.assertEqual(settings.minio_secret_key.get_secret_value(), "secret")
