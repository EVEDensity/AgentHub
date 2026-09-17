from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from app.core.config import ArtifactStoreSettings
from app.services.artifact_integrity_service import (
    ContentAddressedArtifactByteVerifier,
)
from app.services.artifact_store_service import (
    ArtifactStoreIntegrityError,
    ArtifactStoreUnavailableError,
    ContentAddressedArtifactPublisher,
)
from tests.domain.factories import build_artifact


class FakeMinioStat:
    def __init__(self, size: int) -> None:
        self.size = size


class FakeMinioObjectResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.closed = False
        self.released = False

    def stream(self, *, amt: int):
        del amt
        yield self.content

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released = True


class FakeMinioPublisherClient:
    def __init__(
        self,
        *,
        existing_size: int | None = None,
        existing_content: bytes | None = None,
    ) -> None:
        self.existing_size = existing_size
        self.existing_content = existing_content
        self.stat_calls: list[tuple[str, str]] = []
        self.put_calls: list[tuple[str, str, int, dict[str, str]]] = []
        self.put_error: Exception | None = None

    def stat_object(self, bucket: str, key: str) -> FakeMinioStat:
        self.stat_calls.append((bucket, key))
        if self.existing_size is None:
            raise OSError("object not found")
        return FakeMinioStat(self.existing_size)

    def put_object(
        self,
        bucket: str,
        key: str,
        stream,
        *,
        length: int,
        metadata: dict[str, str],
    ) -> None:
        if self.put_error is not None:
            raise self.put_error
        self.put_calls.append((bucket, key, length, metadata))
        self.uploaded_bytes = stream.read()

    def get_object(self, bucket: str, key: str) -> FakeMinioObjectResponse:
        del bucket, key
        if self.existing_content is None:
            raise OSError("existing object body unavailable")
        return FakeMinioObjectResponse(self.existing_content)


class ArtifactStoreServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.local_root = Path(self.temporary_directory.name) / "artifacts"

    def settings(
        self,
        *,
        backend: str = "local",
        publish_max_bytes: int = 1024,
    ) -> ArtifactStoreSettings:
        return ArtifactStoreSettings(
            backend=backend,
            local_root=self.local_root,
            minio_endpoint="minio:9000",
            minio_access_key="access",
            minio_secret_key="secret",
            minio_bucket="agenthub",
            minio_secure=False,
            publish_max_bytes=publish_max_bytes,
        )

    async def test_publish_bytes_returns_content_address_and_verifier_accepts_it(self) -> None:
        content = b"runner output"
        publisher = ContentAddressedArtifactPublisher(self.settings())

        published = await publisher.publish_bytes(content)

        digest_hex = hashlib.sha256(content).hexdigest()
        self.assertEqual(published.digest, f"sha256:{digest_hex}")
        self.assertEqual(published.size_bytes, len(content))
        self.assertEqual(
            published.content_address,
            f"local:sha256/{digest_hex}",
        )
        verifier = ContentAddressedArtifactByteVerifier(self.settings())
        verified = await verifier.verify(
            _artifact_for(published.digest, published.content_address, len(content))
        )
        self.assertEqual(verified.digest, published.digest)

    async def test_publish_file_is_streamed_and_idempotent(self) -> None:
        source = Path(self.temporary_directory.name) / "result.diff"
        source.write_bytes(b"diff --git a/a b/a\n")
        publisher = ContentAddressedArtifactPublisher(self.settings())

        first = await publisher.publish_file(source)
        second = await publisher.publish_file(source)

        self.assertEqual(first, second)
        self.assertTrue(
            (self.local_root / "sha256" / first.digest.removeprefix("sha256:")).is_file()
        )

    async def test_corrupt_existing_local_object_fails_closed(self) -> None:
        content = b"immutable output"
        digest_hex = hashlib.sha256(content).hexdigest()
        target = self.local_root / "sha256" / digest_hex
        target.parent.mkdir(parents=True)
        target.write_bytes(b"corrupt")
        publisher = ContentAddressedArtifactPublisher(self.settings())

        with self.assertRaisesRegex(
            ArtifactStoreIntegrityError,
            "existing content-addressed local bytes",
        ):
            await publisher.publish_bytes(content)

    async def test_source_and_size_failures_are_explicit(self) -> None:
        publisher = ContentAddressedArtifactPublisher(
            self.settings(publish_max_bytes=3)
        )
        missing = Path(self.temporary_directory.name) / "missing"

        with self.assertRaisesRegex(ArtifactStoreUnavailableError, "source file"):
            await publisher.publish_file(missing)
        with self.assertRaisesRegex(ArtifactStoreIntegrityError, "publication limit"):
            await publisher.publish_bytes(b"four")

    async def test_minio_publishes_digest_key_and_metadata(self) -> None:
        content = b"minio output"
        client = FakeMinioPublisherClient()
        publisher = ContentAddressedArtifactPublisher(
            self.settings(backend="minio"),
            minio_client_factory=lambda: client,
        )

        published = await publisher.publish_bytes(content)

        digest_hex = hashlib.sha256(content).hexdigest()
        key = f"artifacts/{digest_hex}"
        self.assertEqual(published.content_address, f"minio://agenthub/{key}")
        self.assertEqual(client.stat_calls, [("agenthub", key)])
        self.assertEqual(
            client.put_calls,
            [("agenthub", key, len(content), {"x-amz-meta-sha256": published.digest})],
        )
        self.assertEqual(client.uploaded_bytes, content)

    async def test_minio_existing_same_size_is_idempotent(self) -> None:
        content = b"existing output"
        client = FakeMinioPublisherClient(
            existing_size=len(content),
            existing_content=content,
        )
        publisher = ContentAddressedArtifactPublisher(
            self.settings(backend="minio"),
            minio_client_factory=lambda: client,
        )

        published = await publisher.publish_bytes(content)

        self.assertEqual(len(client.put_calls), 0)
        self.assertEqual(published.size_bytes, len(content))

    async def test_minio_existing_corrupt_bytes_fail_integrity(self) -> None:
        content = b"existing output"
        client = FakeMinioPublisherClient(
            existing_size=len(content),
            existing_content=b"corrupt output",
        )
        publisher = ContentAddressedArtifactPublisher(
            self.settings(backend="minio"),
            minio_client_factory=lambda: client,
        )

        with self.assertRaisesRegex(ArtifactStoreIntegrityError, "MinIO bytes"):
            await publisher.publish_bytes(content)

    async def test_minio_existing_size_mismatch_is_integrity_error(self) -> None:
        client = FakeMinioPublisherClient(existing_size=1)
        publisher = ContentAddressedArtifactPublisher(
            self.settings(backend="minio"),
            minio_client_factory=lambda: client,
        )

        with self.assertRaisesRegex(ArtifactStoreIntegrityError, "MinIO bytes"):
            await publisher.publish_bytes(b"different size")

    async def test_minio_write_failure_is_unavailable(self) -> None:
        client = FakeMinioPublisherClient()
        client.put_error = OSError("storage down")
        publisher = ContentAddressedArtifactPublisher(
            self.settings(backend="minio"),
            minio_client_factory=lambda: client,
        )

        with self.assertRaisesRegex(
            ArtifactStoreUnavailableError,
            "MinIO artifact publication failed",
        ):
            await publisher.publish_bytes(b"output")


def _artifact_for(digest: str, content_address: str, size_bytes: int):
    return build_artifact(
        digest=digest,
        content_address=content_address,
        size_bytes=size_bytes,
    )
