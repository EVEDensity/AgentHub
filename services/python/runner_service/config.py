from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MAX_SECRET_BYTES = 64 * 1024
_MAX_BINDING_MANIFEST_BYTES = 1024 * 1024


class RunnerServiceSettings(BaseSettings):
    """Fail-closed Runner process configuration.

    Identities and network locations intentionally have no defaults. Secrets are
    represented only by mounted file paths and are loaded during composition.
    """

    model_config = SettingsConfigDict(
        env_prefix="AGENTHUB_RUNNER_",
        extra="ignore",
        frozen=True,
    )

    runner_id: str
    workspace_id: str
    assigned_agent_id: str
    assigned_adapter: str

    mission_control_url: str
    mission_control_token_file: Path
    model_gateway_url: str
    model_gateway_token_file: Path
    model: str
    mcp_endpoint: str
    mcp_token_file: Path
    mcp_bindings_file: Path
    artifact_local_root: Path

    host: str = "0.0.0.0"
    port: int = Field(default=8097, ge=1, le=65535)
    lease_seconds: int = Field(default=300, ge=1, le=3600)
    idle_delay_seconds: float = Field(default=0.5, gt=0)
    max_delay_seconds: float = Field(default=10.0, gt=0)
    heartbeat_interval_seconds: float | None = Field(default=None, gt=0)
    shutdown_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    http_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    max_model_response_bytes: int = Field(default=4 * 1024 * 1024, ge=1024)
    max_artifact_bytes: int = Field(default=64 * 1024 * 1024, ge=1)
    max_context_chars: int = Field(default=32_768, ge=1)
    max_work_unit_timeout_seconds: float = Field(default=300.0, gt=0)
    max_iterations: int = Field(default=8, ge=1, le=128)
    max_tool_calls: int = Field(default=32, ge=1, le=1024)
    max_total_tokens: int | None = Field(default=None, ge=1)
    max_model_cost: float | None = Field(default=None, ge=0)
    model_max_output_tokens: int | None = Field(default=None, ge=1)
    prompt_token_cost: float = Field(default=0.0, ge=0)
    completion_token_cost: float = Field(default=0.0, ge=0)
    system_prompt: str = ""

    @field_validator(
        "runner_id",
        "workspace_id",
        "assigned_agent_id",
        "assigned_adapter",
        "model",
    )
    @classmethod
    def _require_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must be non-empty")
        return normalized

    @field_validator("mission_control_url", "model_gateway_url", "mcp_endpoint")
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

    @field_validator("model")
    @classmethod
    def _reject_mock_model(cls, value: str) -> str:
        if value.casefold().startswith("mock"):
            raise ValueError("Runner service cannot use a mock model")
        return value

    @field_validator(
        "mission_control_token_file",
        "model_gateway_token_file",
        "mcp_token_file",
        "mcp_bindings_file",
        "artifact_local_root",
    )
    @classmethod
    def _require_absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("Runner service paths must be absolute")
        return value

    @model_validator(mode="after")
    def _validate_process_policy(self) -> RunnerServiceSettings:
        if self.assigned_adapter == "a2a.outbound":
            raise ValueError("Runner service cannot claim through a2a.outbound")
        if self.max_delay_seconds < self.idle_delay_seconds:
            raise ValueError("max delay must not be lower than idle delay")
        return self


class MCPBindingDefinition(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: value.split("_")[0]
        + "".join(part.capitalize() for part in value.split("_")[1:]),
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )

    capability: str = Field(min_length=1, max_length=255)
    function_name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("capability", "function_name")
    @classmethod
    def _strip_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("identifier must be non-empty")
        return normalized


class MCPBindingManifest(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: value.split("_")[0]
        + "".join(part.capitalize() for part in value.split("_")[1:]),
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )

    schema_version: Literal["agenthub.runner.mcp-bindings.v1"]
    bindings: tuple[MCPBindingDefinition, ...] = ()

    @model_validator(mode="after")
    def _validate_unique_bindings(self) -> MCPBindingManifest:
        capabilities = [binding.capability for binding in self.bindings]
        function_names = [binding.function_name for binding in self.bindings]
        if len(capabilities) != len(set(capabilities)):
            raise ValueError("MCP capabilities must be unique")
        if len(function_names) != len(set(function_names)):
            raise ValueError("MCP function names must be unique")
        if "a2a.receive" in capabilities:
            raise ValueError("a2a.receive is an admission marker, not an MCP tool")
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
    except OSError as exc:
        raise ValueError("secret file could not be read") from exc
    if not value or "\n" in value or "\r" in value:
        raise ValueError("secret file must contain one non-empty value")
    return value


def load_mcp_binding_manifest(path: Path) -> MCPBindingManifest:
    """Load the credential-free capability manifest used by one process."""

    if path.is_symlink():
        raise ValueError("MCP binding manifest must not be a symbolic link")
    try:
        if not path.is_file():
            raise ValueError("MCP binding manifest is not a regular file")
        size_bytes = path.stat().st_size
        if size_bytes < 2 or size_bytes > _MAX_BINDING_MANIFEST_BYTES:
            raise ValueError("MCP binding manifest size is invalid")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("MCP binding manifest is not valid JSON") from exc
    except OSError as exc:
        raise ValueError("MCP binding manifest could not be read") from exc
    return MCPBindingManifest.model_validate(payload)


__all__ = [
    "MCPBindingDefinition",
    "MCPBindingManifest",
    "RunnerServiceSettings",
    "load_mcp_binding_manifest",
    "read_secret_file",
]
