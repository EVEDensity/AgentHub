from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MAX_DATABASE_URL_BYTES = 64 * 1024


class DecisionExpiryServiceSettings(BaseSettings):
    """Bounded process settings; durable expiry policy stays on each Decision."""

    model_config = SettingsConfigDict(
        env_prefix="AGENTHUB_DECISION_EXPIRY_",
        extra="ignore",
        frozen=True,
    )

    database_url_file: Path
    host: str = "0.0.0.0"
    port: int = Field(default=8099, ge=1, le=65535)
    idle_delay_seconds: float = Field(default=0.5, gt=0)
    max_delay_seconds: float = Field(default=10.0, gt=0)
    shutdown_timeout_seconds: float = Field(default=30.0, gt=0, le=300)

    @field_validator("database_url_file")
    @classmethod
    def _require_absolute_database_url_file(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("database URL file path must be absolute")
        return value

    @model_validator(mode="after")
    def _validate_poll_policy(self) -> DecisionExpiryServiceSettings:
        if self.max_delay_seconds < self.idle_delay_seconds:
            raise ValueError("max delay must not be lower than idle delay")
        return self


def read_database_url_file(path: Path) -> str:
    """Load one mounted PostgreSQL DSN without following symbolic links."""

    if path.is_symlink():
        raise ValueError("database URL file must not be a symbolic link")
    try:
        if not path.is_file():
            raise ValueError("database URL file is not a regular file")
        size_bytes = path.stat().st_size
        if size_bytes < 1 or size_bytes > _MAX_DATABASE_URL_BYTES:
            raise ValueError("database URL file size is invalid")
        database_url = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ValueError("database URL file could not be read") from exc
    if not database_url or "\n" in database_url or "\r" in database_url:
        raise ValueError("database URL file must contain one non-empty value")
    if any(character.isspace() for character in database_url):
        raise ValueError("database URL must not contain unencoded whitespace")

    try:
        parsed = urlsplit(database_url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("database URL is invalid") from exc
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("database URL must use PostgreSQL wire protocol")
    if not parsed.hostname:
        raise ValueError("database URL must contain a valid host")
    if port == 0:
        raise ValueError("database URL port must be positive")
    if not parsed.path.lstrip("/"):
        raise ValueError("database URL must contain a database name")
    if parsed.fragment:
        raise ValueError("database URL must not contain a fragment")
    return database_url


__all__ = [
    "DecisionExpiryServiceSettings",
    "read_database_url_file",
]
