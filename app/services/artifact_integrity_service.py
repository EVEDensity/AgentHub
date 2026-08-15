from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

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


class ArtifactByteDescriptor(Protocol):
    """Minimum immutable metadata required to verify registered bytes."""

    id: str
    digest: str
    content_address: str
    size_bytes: int


class ArtifactVerificationStoreSettings(Protocol):
    local_root: Path
    verify_max_bytes: int


@runtime_checkable
class SecretValuePort(Protocol):
    def get_secret_value(self) -> str: ...


@runtime_checkable
class MinioArtifactVerificationSettings(Protocol):
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: SecretValuePort
    minio_bucket: str
    minio_secure: bool


class ArtifactByteVerifier(Protocol):
    async def verify_all(
        self,
        artifacts: Sequence[ArtifactByteDescriptor],
    ) -> list[ArtifactByteVerification]: ...


class ArtifactByteExporter(Protocol):
    async def read_verified(
        self,
        artifact: ArtifactByteDescriptor,
        *,
        max_bytes: int,
    ) -> bytes: ...


class ContentAddressedArtifactByteVerifier:
    """Streams local or MinIO objects and verifies size plus SHA-256."""

    def __init__(
        self,
        settings: ArtifactVerificationStoreSettings,
        *,
        minio_client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._local_root = settings.local_root.resolve()
        self._minio_client_factory = minio_client_factory or self._build_minio_client
        self._minio_client: Any | None = None

    async def verify_all(
        self,
        artifacts: Sequence[ArtifactByteDescriptor],
    ) -> list[ArtifactByteVerification]:
        verified: list[ArtifactByteVerification] = []
        for artifact in artifacts:
            verified.append(await self.verify(artifact))
        return verified

    async def verify(
        self,
        artifact: ArtifactByteDescriptor,
    ) -> ArtifactByteVerification:
        result, _content = await self._consume(artifact, collect_bytes=False)
        return result

    async def read_verified(
        self,
        artifact: ArtifactByteDescriptor,
        *,
        max_bytes: int,
    ) -> bytes:
        if max_bytes < 0:
            raise ValueError("max_bytes cannot be negative")
        _result, content = await self._consume(
            artifact,
            collect_bytes=True,
            max_bytes=max_bytes,
        )
        if content is None:
            raise AssertionError("verified Artifact byte export produced no content")
        return content

    async def _consume(
        self,
        artifact: ArtifactByteDescriptor,
        *,
        collect_bytes: bool,
        max_bytes: int | None = None,
    ) -> tuple[ArtifactByteVerification, bytes | None]:
        byte_limit = self._settings.verify_max_bytes
        if max_bytes is not None:
            byte_limit = min(byte_limit, max_bytes)
        if artifact.size_bytes > byte_limit:
            raise ArtifactIntegrityError(
                f"artifact exceeds byte verification limit: {artifact.id}"
            )
        if artifact.content_address.startswith(_LOCAL_PREFIX):
            return await asyncio.to_thread(
                self._consume_local,
                artifact,
                byte_limit,
                collect_bytes,
            )
        if artifact.content_address.startswith("minio://"):
            return await asyncio.to_thread(
                self._consume_minio,
                artifact,
                byte_limit,
                collect_bytes,
            )
        raise ArtifactBytesUnavailableError(
            f"unsupported artifact content address: {artifact.id}"
        )

    def _consume_local(
        self,
        artifact: ArtifactByteDescriptor,
        byte_limit: int,
        collect_bytes: bool,
    ) -> tuple[ArtifactByteVerification, bytes | None]:
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
                return self._consume_chunks(
                    artifact,
                    iter(lambda: stream.read(_CHUNK_BYTES), b""),
                    byte_limit=byte_limit,
                    collect_bytes=collect_bytes,
                )
        except ArtifactByteVerificationError:
            raise
        except OSError as exc:
            raise ArtifactBytesUnavailableError(
                f"artifact bytes could not be read: {artifact.id}"
            ) from exc

    def _consume_minio(
        self,
        artifact: ArtifactByteDescriptor,
        byte_limit: int,
        collect_bytes: bool,
    ) -> tuple[ArtifactByteVerification, bytes | None]:
        bucket, key = self._parse_minio_address(artifact)
        try:
            response = self._get_minio_client().get_object(bucket, key)
        except Exception as exc:
            raise ArtifactBytesUnavailableError(
                f"artifact bytes are unavailable: {artifact.id}"
            ) from exc

        try:
            return self._consume_chunks(
                artifact,
                response.stream(amt=_CHUNK_BYTES),
                byte_limit=byte_limit,
                collect_bytes=collect_bytes,
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

    def _parse_minio_address(
        self,
        artifact: ArtifactByteDescriptor,
    ) -> tuple[str, str]:
        settings = self._require_minio_settings()
        parsed = urlparse(artifact.content_address)
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ArtifactBytesUnavailableError(
                f"invalid MinIO artifact address: {artifact.id}"
            )
        if parsed.netloc != settings.minio_bucket:
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

    def _consume_chunks(
        self,
        artifact: ArtifactByteDescriptor,
        chunks: Iterable[bytes],
        *,
        byte_limit: int,
        collect_bytes: bool,
    ) -> tuple[ArtifactByteVerification, bytes | None]:
        digest = hashlib.sha256()
        size_bytes = 0
        collected: list[bytes] | None = [] if collect_bytes else None
        for chunk in chunks:
            size_bytes += len(chunk)
            if size_bytes > byte_limit:
                raise ArtifactIntegrityError(
                    f"artifact exceeds byte verification limit: {artifact.id}"
                )
            digest.update(chunk)
            if collected is not None:
                collected.append(chunk)

        if size_bytes != artifact.size_bytes:
            raise ArtifactIntegrityError(
                f"artifact byte size does not match: {artifact.id}"
            )
        calculated = f"sha256:{digest.hexdigest()}"
        if calculated.lower() != artifact.digest.lower():
            raise ArtifactIntegrityError(
                f"artifact byte digest does not match: {artifact.id}"
            )
        return (
            ArtifactByteVerification(
                artifact_id=artifact.id,
                digest=calculated,
                size_bytes=size_bytes,
            ),
            b"".join(collected) if collected is not None else None,
        )

    def _get_minio_client(self) -> Any:
        if self._minio_client is None:
            self._minio_client = self._minio_client_factory()
        return self._minio_client

    def _build_minio_client(self) -> Any:
        settings = self._require_minio_settings()
        try:
            from minio import Minio
        except ImportError as exc:
            raise ArtifactBytesUnavailableError(
                "MinIO artifact support is not installed"
            ) from exc
        return Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key.get_secret_value(),
            secure=settings.minio_secure,
        )

    def _require_minio_settings(self) -> MinioArtifactVerificationSettings:
        if not isinstance(self._settings, MinioArtifactVerificationSettings):
            raise ArtifactBytesUnavailableError(
                "MinIO Artifact verification is not configured"
            )
        return self._settings


def _digest_hex(artifact: ArtifactByteDescriptor) -> str:
    return artifact.digest.removeprefix("sha256:").lower()


def build_artifact_byte_verifier() -> ContentAddressedArtifactByteVerifier:
    from app.core.config import get_settings

    return ContentAddressedArtifactByteVerifier(get_settings().artifact_store)
