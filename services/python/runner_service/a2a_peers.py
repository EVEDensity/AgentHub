from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.services.a2a_peer_credentials import OriginBoundA2ABearerProvider
from app.services.a2a_peer_route_service import (
    A2AAgentCardTrustPolicy,
    normalize_a2a_origin,
)

_MAX_PEER_MANIFEST_BYTES = 1 << 20
_MAX_BEARER_TOKEN_FILE_BYTES = 16 * 1_024
_MAX_BEARER_TOKEN_CHARS = 8_192


def _camel_alias(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class A2ARunnerPeerDefinition(BaseModel):
    """Non-secret trust and token-file references for one exact peer."""

    model_config = ConfigDict(
        alias_generator=_camel_alias,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )

    agent_origin: str
    trusted_public_keys: tuple[str, ...] = Field(min_length=1, max_length=16)
    bearer_token_file: Path | None = None

    @field_validator("agent_origin")
    @classmethod
    def _canonicalize_origin(cls, value: str) -> str:
        return normalize_a2a_origin(value)

    @field_validator("trusted_public_keys")
    @classmethod
    def _normalize_unique_keys(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip().casefold() for value in values)
        if any(not value for value in normalized):
            raise ValueError("trusted public keys must be non-empty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("trusted public keys must be unique")
        return normalized

    @field_validator("bearer_token_file")
    @classmethod
    def _require_absolute_token_path(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("bearer token file path must be absolute")
        return value


class A2ARunnerPeerManifest(BaseModel):
    """Versioned strict-pinning configuration without credential values."""

    model_config = ConfigDict(
        alias_generator=_camel_alias,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )

    schema_version: Literal["agenthub.runner.a2a-peers.v1"]
    peers: tuple[A2ARunnerPeerDefinition, ...] = Field(
        min_length=1,
        max_length=256,
    )

    def model_post_init(self, __context: Any, /) -> None:
        del __context
        origins = [peer.agent_origin for peer in self.peers]
        if len(set(origins)) != len(origins):
            raise ValueError("A2A peer origins must be unique")

        token_paths = [
            _path_identity(peer.bearer_token_file)
            for peer in self.peers
            if peer.bearer_token_file is not None
        ]
        if len(set(token_paths)) != len(token_paths):
            raise ValueError("A2A peers must not share a bearer token file")


@dataclass(frozen=True, slots=True)
class LoadedA2ARunnerPeers:
    """Startup-only trust and credential adapters for outbound transport."""

    trust_policy: A2AAgentCardTrustPolicy
    credential_provider: OriginBoundA2ABearerProvider


def load_a2a_runner_peers(path: Path) -> LoadedA2ARunnerPeers:
    """Load strict peer pins and receiver tokens from separate mounted files."""

    payload = _load_manifest_json(path)
    try:
        manifest = A2ARunnerPeerManifest.model_validate(payload)
        trust_policy = A2AAgentCardTrustPolicy(
            require_pinned_keys=True,
            trusted_public_keys={
                peer.agent_origin: peer.trusted_public_keys for peer in manifest.peers
            },
        )
    except (TypeError, ValueError, ValidationError):
        raise ValueError("A2A Runner peer manifest is invalid") from None

    bearer_by_origin: dict[str, str] = {}
    for peer in manifest.peers:
        if peer.bearer_token_file is not None:
            bearer_by_origin[peer.agent_origin] = _read_bearer_token_file(
                peer.bearer_token_file
            )
    try:
        credential_provider = OriginBoundA2ABearerProvider(bearer_by_origin)
    except (TypeError, ValueError):
        raise ValueError("A2A Runner peer credentials are invalid") from None
    return LoadedA2ARunnerPeers(
        trust_policy=trust_policy,
        credential_provider=credential_provider,
    )


def _load_manifest_json(path: Path) -> dict[str, Any]:
    if not isinstance(path, Path):
        raise TypeError("A2A Runner peer manifest path must be a Path")
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("A2A Runner peer manifest must be a regular file")
        size_bytes = path.stat().st_size
        if size_bytes < 2 or size_bytes > _MAX_PEER_MANIFEST_BYTES:
            raise ValueError("A2A Runner peer manifest size is invalid")
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("A2A Runner peer manifest could not be read") from None
    if not isinstance(payload, dict):
        raise TypeError("A2A Runner peer manifest must be a JSON object")
    return payload


def _read_bearer_token_file(path: Path) -> str:
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("A2A bearer token must be a regular file")
        size_bytes = path.stat().st_size
        if size_bytes < 1 or size_bytes > _MAX_BEARER_TOKEN_FILE_BYTES:
            raise ValueError("A2A bearer token file size is invalid")
        token = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise ValueError("A2A bearer token file could not be read") from None
    if (
        not token
        or len(token) > _MAX_BEARER_TOKEN_CHARS
        or "\n" in token
        or "\r" in token
        or any(not 0x21 <= ord(character) <= 0x7E for character in token)
    ):
        raise ValueError("A2A bearer token file does not contain a valid token")
    return token


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("A2A Runner peer manifest has duplicate JSON fields")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    raise ValueError("A2A Runner peer manifest contains a non-finite number")


def _path_identity(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


__all__ = [
    "A2ARunnerPeerDefinition",
    "A2ARunnerPeerManifest",
    "LoadedA2ARunnerPeers",
    "load_a2a_runner_peers",
]
