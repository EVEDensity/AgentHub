from __future__ import annotations

import asyncio
import hashlib
import io
import os
import tempfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from app.core.config import ArtifactStoreSettings, get_settings

_CHUNK_BYTES = 1024 * 1024
_LOCAL_PREFIX = "local:sha256/"
_MINIO_PREFIX = "minio://"
_MINIO_KEY_PREFIX = "artifacts/"


class ArtifactPublicationError(ValueError):
    """Base error for content-addressed Artifact publication."""


class ArtifactStoreUnavailableError(ArtifactPublicationError):
    """Raised when the configured byte store cannot be reached or written."""


class ArtifactStoreIntegrityError(ArtifactPublicationError):
    """Raised when an existing content-addressed object is inconsistent."""


class ArtifactPublisher(Protocol):
    async def publish_bytes(self, content: bytes) -> PublishedArtifact: ...

    async def publish_file(self, path: Path) -> PublishedArtifact: ...


@dataclass(frozen=True)
class PublishedArtifact:
    digest: str
    size_bytes: int
    content_address: str


@dataclass(frozen=True)
class _StagedArtifact:
    path: Path
    digest: str
    size_bytes: int


class ContentAddressedArtifactPublisher:
    """Publishes Runner output without creating Mission metadata."""

    def __init__(
        self,
        settings: ArtifactStoreSettings,
        *,
        minio_client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._local_root = settings.local_root.resolve()
        self._minio_client_factory = (
            minio_client_factory or self._build_minio_client
        )
        self._minio_client: Any | None = None

    async def publish_bytes(self, content: bytes) -> PublishedArtifact:
        return await asyncio.to_thread(self._publish_bytes, content)

    async def publish_file(self, path: Path) -> PublishedArtifact:
        return await asyncio.to_thread(self._publish_file, path)

    def _publish_bytes(self, content: bytes) -> PublishedArtifact:
        return self._publish_stream(io.BytesIO(content))

    def _publish_file(self, path: Path) -> PublishedArtifact:
        if not path.is_file():
            raise ArtifactStoreUnavailableError(
                f"artifact source file is unavailable: {path}"
            )
        try:
            with path.open("rb") as source:
                return self._publish_stream(source)
        except ArtifactPublicationError:
            raise
        except OSError as exc:
            raise ArtifactStoreUnavailableError(
                f"artifact source file could not be read: {path}"
            ) from exc

    def _publish_stream(self, source: BinaryIO) -> PublishedArtifact:
        staged = self._stage(source)
        try:
            if self._settings.backend == "local":
                return self._publish_local(staged)
            return self._publish_minio(staged)
        finally:
            if self._settings.backend != "local":
                with suppress(OSError):
                    staged.path.unlink()

    def _stage(self, source: BinaryIO) -> _StagedArtifact:
        temp_file: Path | None = None
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            staging_directory: str | None = None
            if self._settings.backend == "local":
                local_staging_directory = self._local_root / "sha256"
                local_staging_directory.mkdir(parents=True, exist_ok=True)
                staging_directory = str(local_staging_directory)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix="agenthub-artifact-",
                delete=False,
                dir=staging_directory,
            ) as destination:
                temp_file = Path(destination.name)
                for chunk in iter(lambda: source.read(_CHUNK_BYTES), b""):
                    if not isinstance(chunk, bytes):
                        raise ArtifactStoreUnavailableError(
                            "artifact source did not yield bytes"
                        )
                    size_bytes += len(chunk)
                    if size_bytes > self._settings.publish_max_bytes:
                        raise ArtifactStoreIntegrityError(
                            "artifact exceeds publication limit"
                        )
                    digest.update(chunk)
                    destination.write(chunk)
        except ArtifactPublicationError:
            if temp_file is not None:
                with suppress(OSError):
                    temp_file.unlink()
            raise
        except OSError as exc:
            if temp_file is not None:
                with suppress(OSError):
                    temp_file.unlink()
            raise ArtifactStoreUnavailableError(
                "artifact source could not be staged"
            ) from exc

        return _StagedArtifact(
            path=temp_file,
            digest=f"sha256:{digest.hexdigest()}",
            size_bytes=size_bytes,
        )

    def _publish_local(self, staged: _StagedArtifact) -> PublishedArtifact:
        root = self._local_root
        target_directory = root / "sha256"
        try:
            target_directory.mkdir(parents=True, exist_ok=True)
            target = (target_directory / staged.digest.removeprefix("sha256:")).resolve()
            target.relative_to(root)
        except (OSError, ValueError) as exc:
            with suppress(OSError):
                staged.path.unlink()
            raise ArtifactStoreUnavailableError(
                "configured local artifact root is unavailable"
            ) from exc

        if target.exists():
            try:
                self._assert_existing_file(target, staged)
            finally:
                with suppress(OSError):
                    staged.path.unlink()
        else:
            try:
                os.replace(staged.path, target)
            except OSError as exc:
                with suppress(OSError):
                    staged.path.unlink()
                raise ArtifactStoreUnavailableError(
                    "local artifact publication failed"
                ) from exc

        return PublishedArtifact(
            digest=staged.digest,
            size_bytes=staged.size_bytes,
            content_address=f"{_LOCAL_PREFIX}{staged.digest.removeprefix('sha256:')}",
        )

    def _assert_existing_file(self, path: Path, staged: _StagedArtifact) -> None:
        if not path.is_file():
            raise ArtifactStoreIntegrityError(
                "content-addressed local path is not a regular file"
            )
        try:
            with path.open("rb") as source:
                digest, size_bytes = _digest_stream(
                    source,
                    self._settings.publish_max_bytes,
                )
        except ArtifactPublicationError:
            raise
        except OSError as exc:
            raise ArtifactStoreUnavailableError(
                "existing content-addressed local bytes could not be read"
            ) from exc
        if digest != staged.digest or size_bytes != staged.size_bytes:
            raise ArtifactStoreIntegrityError(
                "existing content-addressed local bytes do not match"
            )

    def _publish_minio(self, staged: _StagedArtifact) -> PublishedArtifact:
        bucket = self._settings.minio_bucket
        key = f"{_MINIO_KEY_PREFIX}{staged.digest.removeprefix('sha256:')}"
        client = self._get_minio_client()
        try:
            existing = client.stat_object(bucket, key)
        except Exception:  # MinIO SDK exposes provider-specific exception types.  # noqa: BLE001
            existing = None
        if existing is not None:
            existing_size = getattr(existing, "size", None)
            if existing_size is not None and existing_size != staged.size_bytes:
                raise ArtifactStoreIntegrityError(
                    "existing content-addressed MinIO bytes do not match"
                )
            self._assert_existing_minio_object(client, bucket, key, staged)
            return PublishedArtifact(
                digest=staged.digest,
                size_bytes=staged.size_bytes,
                content_address=f"{_MINIO_PREFIX}{bucket}/{key}",
            )

        try:
            with staged.path.open("rb") as source:
                client.put_object(
                    bucket,
                    key,
                    source,
                    length=staged.size_bytes,
                    metadata={"x-amz-meta-sha256": staged.digest},
                )
        except Exception as exc:
            raise ArtifactStoreUnavailableError(
                "MinIO artifact publication failed"
            ) from exc
        return PublishedArtifact(
            digest=staged.digest,
            size_bytes=staged.size_bytes,
            content_address=f"{_MINIO_PREFIX}{bucket}/{key}",
        )

    def _assert_existing_minio_object(
        self,
        client: Any,
        bucket: str,
        key: str,
        staged: _StagedArtifact,
    ) -> None:
        try:
            response = client.get_object(bucket, key)
        except Exception as exc:  # MinIO SDK provider errors are not stable.
            raise ArtifactStoreUnavailableError(
                "existing content-addressed MinIO bytes could not be read"
            ) from exc
        try:
            digest, size_bytes = _digest_chunks(
                response.stream(amt=_CHUNK_BYTES),
                self._settings.publish_max_bytes,
            )
        except ArtifactPublicationError:
            raise
        except Exception as exc:  # MinIO response errors are provider-specific.
            raise ArtifactStoreUnavailableError(
                "existing content-addressed MinIO bytes could not be read"
            ) from exc
        finally:
            with suppress(Exception):
                response.close()
            with suppress(Exception):
                response.release_conn()
        if digest != staged.digest or size_bytes != staged.size_bytes:
            raise ArtifactStoreIntegrityError(
                "existing content-addressed MinIO bytes do not match"
            )

    def _get_minio_client(self) -> Any:
        if self._minio_client is None:
            self._minio_client = self._minio_client_factory()
        return self._minio_client

    def _build_minio_client(self) -> Any:
        try:
            from minio import Minio
        except ImportError as exc:
            raise ArtifactStoreUnavailableError(
                "MinIO artifact support is not installed"
            ) from exc
        return Minio(
            self._settings.minio_endpoint,
            access_key=self._settings.minio_access_key,
            secret_key=self._settings.minio_secret_key.get_secret_value(),
            secure=self._settings.minio_secure,
        )


def _digest_stream(source: BinaryIO, max_bytes: int) -> tuple[str, int]:
    try:
        return _digest_chunks(
            iter(lambda: source.read(_CHUNK_BYTES), b""),
            max_bytes,
        )
    finally:
        source.close()


def _digest_chunks(chunks: Any, max_bytes: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    for chunk in chunks:
        if not isinstance(chunk, bytes):
            raise ArtifactStoreUnavailableError("artifact store yielded non-bytes")
        size_bytes += len(chunk)
        if size_bytes > max_bytes:
            raise ArtifactStoreIntegrityError(
                "existing artifact exceeds publication limit"
            )
        digest.update(chunk)
    return f"sha256:{digest.hexdigest()}", size_bytes


def build_artifact_publisher() -> ContentAddressedArtifactPublisher:
    return ContentAddressedArtifactPublisher(get_settings().artifact_store)


__all__ = [
    "ArtifactPublicationError",
    "ArtifactPublisher",
    "ArtifactStoreIntegrityError",
    "ArtifactStoreUnavailableError",
    "ContentAddressedArtifactPublisher",
    "PublishedArtifact",
    "build_artifact_publisher",
]
