"""Unified configuration — single source of truth for all AgentHub settings.

All settings are loaded from environment variables (with ``.env`` file support
via ``python-dotenv``) and validated at startup via Pydantic.

Usage::

    from app.core.config import settings

    print(settings.DATABASE_URL)
    print(settings.orchestrator.enabled)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ── Derive base paths ──────────────────────────────────────────────────
_BASE_DIR = Path(__file__).resolve().parent.parent.parent  # app/core → app → project root
_PROJECT_ROOT = _BASE_DIR.parent
_DATA_DIR = _BASE_DIR / "data"

# Ensure data directory exists (idempotent)
_DATA_DIR.mkdir(exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════
# Sub-models (logical groups)
# ═══════════════════════════════════════════════════════════════════════════


class LLMKeys(BaseSettings):
    """API keys and base URLs for LLM providers."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    openai_api_key: str = Field(default="", description="OpenAI API key")
    anthropic_api_key: str = Field(default="", description="Anthropic API key")
    ollama_base_url: str = Field(
        default="http://127.0.0.1:11434",
        description="Ollama server URL",
    )


class OrchestratorSettings(BaseSettings):
    """Orchestrator pre-processing and auto-decomposition."""

    model_config = SettingsConfigDict(env_prefix="AGENTHUB_ORCHESTRATOR_", extra="ignore")

    preprocess_enabled: bool = Field(
        default=True, alias="AGENTHUB_ORCHESTRATOR_PREPROCESS",
        description="Enable Orchestrator intent analysis before main LLM call",
    )
    preprocess_min_length: int = Field(
        default=30, alias="AGENTHUB_PREPROCESS_MIN_LENGTH",
        description="Minimum character length for pre-processing trigger",
    )

    auto_decompose: bool = Field(
        default=True, alias="AGENTHUB_AUTO_DECOMPOSE",
        description="Auto-decompose large requests into DAG subtasks",
    )
    auto_decompose_min_length: int = Field(
        default=300, alias="AGENTHUB_AUTO_DECOMPOSE_MIN_LENGTH",
        description="Minimum character length for auto-decomposition",
    )


class SearchSettings(BaseSettings):
    """Web search provider configuration."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    web_search_mode: str = Field(
        default="auto",
        description="Search provider priority: auto, bing, serpapi, google, tavily, brave, duckduckgo, disabled",
    )
    bing_api_key: str = Field(default="")
    serpapi_api_key: str = Field(default="")
    google_api_key: str = Field(default="")
    google_cse_id: str = Field(default="")
    tavily_api_key: str = Field(default="")
    brave_api_key: str = Field(default="")


class FileSettings(BaseSettings):
    """File operation limits and versioning."""

    model_config = SettingsConfigDict(env_prefix="AGENTHUB_FILE_", extra="ignore")

    auto_git: bool = Field(default=True, description="Auto git-commit on file_write / file_patch")
    broadcast: bool = Field(default=True, description="Broadcast workspace_change on file write")


class OfficePreviewSettings(BaseSettings):
    """Office document preview limits."""

    model_config = SettingsConfigDict(env_prefix="AGENTHUB_OFFICE_", extra="ignore")

    preview_max_mb: int = Field(default=20, alias="AGENTHUB_OFFICE_PREVIEW_MAX_MB")
    workspace_read_max_mb: int = Field(default=30, alias="AGENTHUB_OFFICE_WORKSPACE_READ_MAX_MB")


class StreamingSettings(BaseSettings):
    """Streaming timeout configuration."""

    model_config = SettingsConfigDict(env_prefix="AGENTHUB_STREAM_", extra="ignore")

    first_byte_timeout: int = Field(default=30, alias="AGENTHUB_STREAM_FIRST_BYTE_TIMEOUT")
    idle_timeout: int = Field(default=120, alias="AGENTHUB_STREAM_IDLE_TIMEOUT")


class MemorySettings(BaseSettings):
    """Auto-memory extraction settings."""

    model_config = SettingsConfigDict(env_prefix="AGENTHUB_", extra="ignore")

    auto_memory_enabled: bool = Field(default=True, alias="AGENTHUB_AUTO_MEMORY")
    memory_min_msg: int = Field(default=2, alias="AGENTHUB_MEMORY_MIN_MSG")


class CommandSettings(BaseSettings):
    """Command execution limits for agent CLI tools."""

    model_config = SettingsConfigDict(env_prefix="AGENTHUB_COMMAND_", extra="ignore")

    execute_timeout: int = Field(default=120, alias="AGENTHUB_COMMAND_TIMEOUT")
    max_output: int = Field(default=100000, alias="AGENTHUB_COMMAND_MAX_OUTPUT")


# ═══════════════════════════════════════════════════════════════════════════
# Main settings (aggregates sub-models + top-level keys)
# ═══════════════════════════════════════════════════════════════════════════


class Settings(BaseSettings):
    """Root settings — load once at startup, access everywhere."""

    model_config = SettingsConfigDict(
        env_file=str(_BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        # Allow env vars without prefix
        env_prefix="",
    )

    # ── Application ────────────────────────────────────────────────────
    app_name: str = "AgentHub 多智能体协作平台"
    app_version: str = "3.0-modular"

    # ── Environment ────────────────────────────────────────────────────
    env: Literal["development", "production", "test"] = Field(
        default="development",
        alias="AGENTHUB_ENV",
        description="Runtime environment (development, production, test)",
    )

    # ── Database ───────────────────────────────────────────────────────
    DATABASE_URL: str = Field(
        default="",
        description="PostgreSQL connection URL (postgresql://...)",
    )
    db_pool_min: int = Field(default=2, alias="AGENTHUB_DB_POOL_MIN")
    db_pool_max: int = Field(default=20, alias="AGENTHUB_DB_POOL_MAX")

    # ── Network ────────────────────────────────────────────────────────
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"],
        alias="AGENTHUB_CORS_ORIGINS",
        description="Comma-separated CORS allowed origins",
    )
    max_body_mb: int = Field(default=50, alias="AGENTHUB_MAX_BODY_MB")
    request_timeout_seconds: float = Field(
        default=600.0,
        alias="AGENTHUB_REQUEST_TIMEOUT",
        description="Overall HTTP request timeout (seconds)",
    )
    enable_real_llm: bool = Field(
        default=True,
        alias="AGENTHUB_ENABLE_REAL_LLM",
        description="When False, all agent calls use MockAdapter",
    )

    # ── Identity ───────────────────────────────────────────────────────
    default_session_id: str = "session-1"
    default_user_id: str = "local-admin"

    # ── Sub-models (validated groups) ──────────────────────────────────
    llm: LLMKeys = Field(default_factory=LLMKeys)
    orchestrator: OrchestratorSettings = Field(default_factory=OrchestratorSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    files: FileSettings = Field(default_factory=FileSettings)
    office: OfficePreviewSettings = Field(default_factory=OfficePreviewSettings)
    streaming: StreamingSettings = Field(default_factory=StreamingSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    command: CommandSettings = Field(default_factory=CommandSettings)

    # ── Derived paths (not from env) ──────────────────────────────────
    @property
    def base_dir(self) -> Path:
        return _BASE_DIR

    @property
    def project_root(self) -> Path:
        return _PROJECT_ROOT

    @property
    def data_dir(self) -> Path:
        return _DATA_DIR

    @property
    def workspaces_dir(self) -> Path:
        return _DATA_DIR / "workspaces"

    @property
    def memory_dir(self) -> Path:
        return _PROJECT_ROOT / ".claude" / "memory"

    @property
    def skills_dir_user(self) -> Path:
        return Path.home() / ".claude" / "skills"

    @property
    def skills_dir_project(self) -> Path:
        return _PROJECT_ROOT / ".claude" / "skills"

    # ── Validation ─────────────────────────────────────────────────────

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v: object) -> list[str]:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        if isinstance(v, list):
            return v
        return ["http://localhost:3000", "http://127.0.0.1:3000"]

    @field_validator("DATABASE_URL")
    @classmethod
    def _warn_missing_db(cls, v: str) -> str:
        if not v and os.getenv("AGENTHUB_ENV", "development") == "production":
            import warnings
            warnings.warn("DATABASE_URL is empty — database operations will fail")
        return v


# ═══════════════════════════════════════════════════════════════════════════
# Singleton (load once at import time)
# ═══════════════════════════════════════════════════════════════════════════

_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the cached Settings singleton (lazy-init on first call).

    Use this in FastAPI dependencies or async contexts::

        from app.core.config import get_settings
        cfg = get_settings()
    """
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings


# Module-level shortcut for non-DI code (backward compatible)
settings: Settings  # will be bound below after class definition


def _init_settings() -> Settings:
    global _settings, settings
    _settings = Settings()  # type: ignore[call-arg]
    settings = _settings
    return _settings


_init_settings()
