from __future__ import annotations

from pathlib import Path

import httpx
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MAX_SECRET_BYTES = 64 * 1024


class VerifierServiceSettings(BaseSettings):
    """Fail-closed configuration for one workspace-scoped verifier process."""

    model_config = SettingsConfigDict(
        env_prefix="AGENTHUB_VERIFIER_",
        extra="ignore",
        frozen=True,
    )

    verifier_id: str
    verifier_version: str
    workspace_id: str
    mission_control_url: str
    mission_control_token_file: Path
    artifact_local_root: Path

    host: str = "0.0.0.0"
    port: int = Field(default=8098, ge=1, le=65535)
    idle_delay_seconds: float = Field(default=0.5, gt=0)
    max_delay_seconds: float = Field(default=10.0, gt=0)
    shutdown_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    http_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    max_artifact_bytes: int = Field(default=64 * 1024 * 1024, ge=1)

    @field_validator("verifier_id", "verifier_version", "workspace_id")
    @classmethod
    def _require_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must be non-empty")
        return normalized

    @field_validator("mission_control_url")
    @classmethod
    def _validate_http_url(cls, value: str) -> str:
        normalized = value.strip()
        parsed = httpx.URL(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            raise ValueError("URL must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError("URL must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("URL must not contain a query or fragment")
        return normalized.rstrip("/")

    @field_validator("mission_control_token_file", "artifact_local_root")
    @classmethod
    def _require_absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("Verifier service paths must be absolute")
        return value

    @model_validator(mode="after")
    def _validate_poll_policy(self) -> VerifierServiceSettings:
        if self.max_delay_seconds < self.idle_delay_seconds:
            raise ValueError("max delay must not be lower than idle delay")
        return self


def read_secret_file(path: Path) -> str:
    """Read one mounted secret without accepting links or multi-line values."""

    if path.is_symlink():
        raise ValueError("secret file must not be a symbolic link")
    try:
        if not path.is_file():
            raise ValueError("secret file is not a regular file")
        size_bytes = path.stat().st_size
        if size_bytes < 1 or size_bytes > _MAX_SECRET_BYTES:
            raise ValueError("secret file size is invalid")
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ValueError("secret file could not be read") from exc
    if not value or "\n" in value or "\r" in value:
        raise ValueError("secret file must contain one non-empty value")
    return value


__all__ = ["VerifierServiceSettings", "read_secret_file"]
