from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlparse

from app.core.config import ArtifactStoreSettings, get_settings
from app.domain import Artifact

_CHUNK_BYTES = 1024 * 1024
_LOCAL_PREFIX = "local:sha256/"


class ArtifactByteVerificationError(ValueError):
    """Base error for fail-closed Artifact byte verification."""


class ArtifactBytesUnavailableError(ArtifactByteVerificationError):
    """Raised when registered Artifact bytes cannot be read."""


class ArtifactIntegrityError(ArtifactByteVerificationError):
    """Raised when bytes do not match registered size or digest."""


@dataclass(frozen=True)
class ArtifactByteVerification:
    artifact_id: str
    digest: str
    size_bytes: int


class ArtifactByteVerifier(Protocol):
    async def verify_all(
        self,
        artifacts: list[Artifact],
    ) -> list[ArtifactByteVerification]: ...


class ContentAddressedArtifactByteVerifier:
    """Streams local or MinIO objects and verifies size plus SHA-256."""

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

    async def verify_all(
        self,
        artifacts: list[Artifact],
    ) -> list[ArtifactByteVerification]:
        verified: list[ArtifactByteVerification] = []
        for artifact in artifacts:
            verified.append(await self.verify(artifact))
        return verified

    async def verify(self, artifact: Artifact) -> ArtifactByteVerification:
        if artifact.size_bytes > self._settings.verify_max_bytes:
            raise ArtifactIntegrityError(
                f"artifact exceeds byte verification limit: {artifact.id}"
            )
        if artifact.content_address.startswith(_LOCAL_PREFIX):
            return await asyncio.to_thread(self._verify_local, artifact)
        if artifact.content_address.startswith("minio://"):
            return await asyncio.to_thread(self._verify_minio, artifact)
        raise ArtifactBytesUnavailableError(
            f"unsupported artifact content address: {artifact.id}"
        )

    def _verify_local(self, artifact: Artifact) -> ArtifactByteVerification:
        expected_digest = _digest_hex(artifact)
        address_digest = artifact.content_address.removeprefix(_LOCAL_PREFIX)
        if address_digest.lower() != expected_digest:
            raise ArtifactIntegrityError(
                f"artifact content address digest does not match: {artifact.id}"
            )

        path = (self._local_root / "sha256" / expected_digest).resolve()
        try:
            path.relative_to(self._local_root)
        except ValueError as exc:
            raise ArtifactBytesUnavailableError(
                f"artifact path escapes configured root: {artifact.id}"
            ) from exc
        if not path.is_file():
            raise ArtifactBytesUnavailableError(
                f"artifact bytes are unavailable: {artifact.id}"
            )

        try:
            with path.open("rb") as stream:
                return self._verify_chunks(
                    artifact,
                    iter(lambda: stream.read(_CHUNK_BYTES), b""),
                )
        except ArtifactByteVerificationError:
            raise
        except OSError as exc:
            raise ArtifactBytesUnavailableError(
                f"artifact bytes could not be read: {artifact.id}"
            ) from exc

    def _verify_minio(self, artifact: Artifact) -> ArtifactByteVerification:
        bucket, key = self._parse_minio_address(artifact)
        try:
            response = self._get_minio_client().get_object(bucket, key)
        except Exception as exc:
            raise ArtifactBytesUnavailableError(
                f"artifact bytes are unavailable: {artifact.id}"
            ) from exc

        try:
            return self._verify_chunks(
                artifact,
                response.stream(amt=_CHUNK_BYTES),
            )
        except ArtifactByteVerificationError:
            raise
        except Exception as exc:
            raise ArtifactBytesUnavailableError(
                f"artifact bytes could not be read: {artifact.id}"
            ) from exc
        finally:
            with suppress(Exception):
                response.close()
            with suppress(Exception):
                response.release_conn()

    def _parse_minio_address(self, artifact: Artifact) -> tuple[str, str]:
        parsed = urlparse(artifact.content_address)
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ArtifactBytesUnavailableError(
                f"invalid MinIO artifact address: {artifact.id}"
            )
        if parsed.netloc != self._settings.minio_bucket:
            raise ArtifactBytesUnavailableError(
                f"artifact bucket is not allowed: {artifact.id}"
            )

        key = parsed.path.lstrip("/")
        key_parts = PurePosixPath(key).parts
        expected_digest = _digest_hex(artifact)
        if (
            not key
            or ".." in key_parts
            or expected_digest not in {part.lower() for part in key_parts}
        ):
            raise ArtifactIntegrityError(
                f"artifact content address digest does not match: {artifact.id}"
            )
        return parsed.netloc, key

    def _verify_chunks(
        self,
        artifact: Artifact,
        chunks: Iterable[bytes],
    ) -> ArtifactByteVerification:
        digest = hashlib.sha256()
        size_bytes = 0
        for chunk in chunks:
            size_bytes += len(chunk)
            if size_bytes > self._settings.verify_max_bytes:
                raise ArtifactIntegrityError(
                    f"artifact exceeds byte verification limit: {artifact.id}"
                )
            digest.update(chunk)

        if size_bytes != artifact.size_bytes:
            raise ArtifactIntegrityError(
                f"artifact byte size does not match: {artifact.id}"
            )
        calculated = f"sha256:{digest.hexdigest()}"
        if calculated.lower() != artifact.digest.lower():
            raise ArtifactIntegrityError(
                f"artifact byte digest does not match: {artifact.id}"
            )
        return ArtifactByteVerification(
            artifact_id=artifact.id,
            digest=calculated,
            size_bytes=size_bytes,
        )

    def _get_minio_client(self) -> Any:
        if self._minio_client is None:
            self._minio_client = self._minio_client_factory()
        return self._minio_client

    def _build_minio_client(self) -> Any:
        try:
            from minio import Minio
        except ImportError as exc:
            raise ArtifactBytesUnavailableError(
                "MinIO artifact support is not installed"
            ) from exc
        return Minio(
            self._settings.minio_endpoint,
            access_key=self._settings.minio_access_key,
            secret_key=self._settings.minio_secret_key.get_secret_value(),
            secure=self._settings.minio_secure,
        )


def _digest_hex(artifact: Artifact) -> str:
    return artifact.digest.removeprefix("sha256:").lower()


def build_artifact_byte_verifier() -> ContentAddressedArtifactByteVerifier:
    return ContentAddressedArtifactByteVerifier(get_settings().artifact_store)
