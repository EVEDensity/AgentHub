"""Immutable candidate bytes returned by an executor, before lease admission."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.domain import ArtifactKind

MAX_BUNDLE_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class HarnessArtifact:
    id: str
    kind: str
    media_type: str
    content: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id or len(self.id) > 255:
            raise ValueError("candidate Artifact ID is invalid")
        ArtifactKind(self.kind)
        if not isinstance(self.media_type, str) or not self.media_type or len(self.media_type) > 255:
            raise ValueError("candidate Artifact media type is invalid")
        if type(self.content) is not bytes or len(self.content) > MAX_BUNDLE_BYTES:
            raise ValueError("candidate Artifact bytes exceed the bound")

    @property
    def digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.content).hexdigest()


def validate_bundle(artifacts: tuple[HarnessArtifact, ...]) -> None:
    if not isinstance(artifacts, tuple) or not 1 <= len(artifacts) <= 32:
        raise ValueError("candidate Artifact bundle must contain 1 to 32 entries")
    if any(not isinstance(artifact, HarnessArtifact) for artifact in artifacts):
        raise ValueError("candidate Artifact bundle contains an invalid entry")
    if len({artifact.id for artifact in artifacts}) != len(artifacts):
        raise ValueError("candidate Artifact bundle contains duplicate IDs")
    if sum(len(artifact.content) for artifact in artifacts) > MAX_BUNDLE_BYTES:
        raise ValueError("candidate Artifact bundle exceeds the byte bound")
