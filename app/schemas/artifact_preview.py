"""Narrow, byte-verified text projection; never a verifier verdict."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from app.domain import ArtifactKind
from app.domain.models import Digest, DomainModel, Identifier


class PreviewArtifact(DomainModel):
    id: Identifier
    mission_id: Identifier
    work_unit_id: Identifier
    attempt: Annotated[int, Field(ge=1)]
    kind: ArtifactKind
    media_type: Annotated[str, Field(min_length=1, max_length=255)]
    size_bytes: Annotated[int, Field(ge=0, le=262144)]
    digest: Digest


class ArtifactTextPreview(DomainModel):
    schema_version: Literal[1] = 1
    artifact: PreviewArtifact
    encoding: Literal["utf-8"] = "utf-8"
    integrity: Literal["verified"] = "verified"
    text: Annotated[str, Field(max_length=262144)]
