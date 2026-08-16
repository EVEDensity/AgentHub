from __future__ import annotations

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DecisionExpiryServiceSettings(BaseSettings):
    """Bounded process settings; durable expiry policy stays on each Decision."""

    model_config = SettingsConfigDict(
        env_prefix="AGENTHUB_DECISION_EXPIRY_",
        extra="ignore",
        frozen=True,
    )

    host: str = "0.0.0.0"
    port: int = Field(default=8099, ge=1, le=65535)
    idle_delay_seconds: float = Field(default=0.5, gt=0)
    max_delay_seconds: float = Field(default=10.0, gt=0)
    shutdown_timeout_seconds: float = Field(default=30.0, gt=0, le=300)

    @model_validator(mode="after")
    def _validate_poll_policy(self) -> DecisionExpiryServiceSettings:
        if self.max_delay_seconds < self.idle_delay_seconds:
            raise ValueError("max delay must not be lower than idle delay")
        return self


__all__ = ["DecisionExpiryServiceSettings"]
