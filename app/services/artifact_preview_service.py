"""Read bounded registered text without changing Mission or Evidence state."""
from __future__ import annotations

import asyncio
import hashlib
import re

from app.domain import Artifact, ArtifactSensitivity
from app.schemas.artifact_preview import ArtifactTextPreview, PreviewArtifact
from app.services.artifact_integrity_service import (
    ArtifactByteExporter,
    ArtifactIntegrityError,
)

PREVIEW_MAX_BYTES = 262144
PREVIEW_TIMEOUT_SECONDS = 10.0
_MEDIA_TOKEN = re.compile(r"^[a-z0-9!#$%&'*+.^_`|~-]+$")
_APPLICATION_TEXT = {"json", "javascript", "x-python"}
_PREVIEW_SENSITIVITY = {ArtifactSensitivity.PUBLIC, ArtifactSensitivity.INTERNAL}


class ArtifactPreviewError(ValueError):
    """Static transport-safe failure with no bytes, path, or storage address."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _require_previewable(artifact: Artifact) -> None:
    if artifact.sensitivity not in _PREVIEW_SENSITIVITY:
        raise ArtifactPreviewError(403, "Artifact sensitivity does not permit preview")
    if artifact.size_bytes > PREVIEW_MAX_BYTES:
        raise ArtifactPreviewError(413, "Artifact exceeds preview byte limit")
    media_type = artifact.media_type.split(";", 1)[0].strip().lower()
    category, separator, subtype = media_type.partition("/")
    valid_subtype = bool(separator and _MEDIA_TOKEN.fullmatch(subtype))
    if valid_subtype and category == "text":
        return
    if valid_subtype and category == "application" and (
        subtype in _APPLICATION_TEXT or subtype.endswith("+json")
    ):
        return
    raise ArtifactPreviewError(415, "Artifact media type does not permit text preview")


def _verified_text(artifact: Artifact, content: bytes) -> str:
    if not isinstance(content, bytes):
        raise ArtifactPreviewError(503, "Artifact preview bytes are unavailable")
    if len(content) > PREVIEW_MAX_BYTES:
        raise ArtifactPreviewError(413, "Artifact exceeds preview byte limit")
    calculated = "sha256:" + hashlib.sha256(content).hexdigest()
    if len(content) != artifact.size_bytes or calculated.lower() != artifact.digest.lower():
        raise ArtifactPreviewError(409, "Artifact preview integrity mismatch")
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ArtifactPreviewError(415, "Artifact is not supported UTF-8 text") from exc
    if "\x00" in text:
        raise ArtifactPreviewError(415, "Artifact is not supported UTF-8 text")
    return text


async def read_artifact_text_preview(
    artifact: Artifact, *, exporter: ArtifactByteExporter,
) -> ArtifactTextPreview:
    _require_previewable(artifact)
    try:
        async with asyncio.timeout(PREVIEW_TIMEOUT_SECONDS):
            content = await exporter.read_verified(artifact, max_bytes=PREVIEW_MAX_BYTES)
    except ArtifactIntegrityError as exc:
        raise ArtifactPreviewError(409, "Artifact preview integrity mismatch") from exc
    except Exception as exc:
        raise ArtifactPreviewError(503, "Artifact preview bytes are unavailable") from exc
    text = _verified_text(artifact, content)
    return ArtifactTextPreview(
        artifact=PreviewArtifact(
            id=artifact.id, mission_id=artifact.mission_id,
            work_unit_id=artifact.work_unit_id, attempt=artifact.attempt,
            kind=artifact.kind, media_type=artifact.media_type,
            size_bytes=artifact.size_bytes, digest=artifact.digest,
        ),
        text=text,
    )
