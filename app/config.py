"""Backward-compatible config bridge.

All settings are now managed by :mod:`app.core.config`.  This module re-exports
every attribute that existing callers import so the migration to Pydantic
Settings is transparent.

New code should import ``settings`` from ``app.core.config`` directly::

    from app.core.config import settings
"""

from __future__ import annotations

# Force re-export of every attribute the old config.py exposed.
# This is intentionally verbose so that ``from app.config import X``
# continues to work for all 19 existing callers.
from app.core.config import settings as _cfg

# ── Paths ─────────────────────────────────────────────────────────────
BASE_DIR = _cfg.base_dir
PROJECT_ROOT = _cfg.project_root
DATA_DIR = _cfg.data_dir
WORKSPACES_DIR = _cfg.workspaces_dir
MEMORY_DIR = _cfg.memory_dir
SKILLS_DIR_USER = _cfg.skills_dir_user
SKILLS_DIR_PROJECT = _cfg.skills_dir_project

# ── Database ──────────────────────────────────────────────────────────
DATABASE_URL = _cfg.DATABASE_URL

# ── Application ───────────────────────────────────────────────────────
APP_NAME = _cfg.app_name
APP_VERSION = _cfg.app_version
DEFAULT_SESSION_ID = _cfg.default_session_id
DEFAULT_USER_ID = _cfg.default_user_id

# ── LLM ───────────────────────────────────────────────────────────────
OPENAI_API_KEY = _cfg.llm.openai_api_key
ANTHROPIC_API_KEY = _cfg.llm.anthropic_api_key
OLLAMA_BASE_URL = _cfg.llm.ollama_base_url

# ── Search ────────────────────────────────────────────────────────────
WEB_SEARCH_MODE = _cfg.search.web_search_mode
BING_API_KEY = _cfg.search.bing_api_key
SERPAPI_API_KEY = _cfg.search.serpapi_api_key
GOOGLE_API_KEY = _cfg.search.google_api_key
GOOGLE_CSE_ID = _cfg.search.google_cse_id
TAVILY_API_KEY = _cfg.search.tavily_api_key
BRAVE_API_KEY = _cfg.search.brave_api_key

# ── Orchestrator ──────────────────────────────────────────────────────
ORCHESTRATOR_PREPROCESS_ENABLED = _cfg.orchestrator.preprocess_enabled
ORCHESTRATOR_PREPROCESS_MIN_LENGTH = _cfg.orchestrator.preprocess_min_length
AGENTHUB_AUTO_DECOMPOSE = _cfg.orchestrator.auto_decompose
AGENTHUB_AUTO_DECOMPOSE_MIN_LENGTH = _cfg.orchestrator.auto_decompose_min_length

# ── File operations ───────────────────────────────────────────────────
AGENTHUB_FILE_AUTO_GIT = _cfg.files.auto_git
AGENTHUB_FILE_BROADCAST = _cfg.files.broadcast

# ── Office preview ────────────────────────────────────────────────────
OFFICE_PREVIEW_MAX_MB = _cfg.office.preview_max_mb
OFFICE_WORKSPACE_READ_MAX_MB = _cfg.office.workspace_read_max_mb

# ── Memory ────────────────────────────────────────────────────────────
AUTO_MEMORY_ENABLED = _cfg.memory.auto_memory_enabled
AUTO_MEMORY_MIN_MSG = _cfg.memory.memory_min_msg

# ── Command execution ─────────────────────────────────────────────────
COMMAND_EXECUTE_TIMEOUT = _cfg.command.execute_timeout
COMMAND_EXECUTE_MAX_OUTPUT = _cfg.command.max_output

# ── Streaming ─────────────────────────────────────────────────────────
STREAM_FIRST_BYTE_TIMEOUT = _cfg.streaming.first_byte_timeout
STREAM_IDLE_TIMEOUT = _cfg.streaming.idle_timeout

# ── Request ───────────────────────────────────────────────────────────
REQUEST_TIMEOUT_SECONDS = _cfg.request_timeout_seconds
ENABLE_REAL_LLM = _cfg.enable_real_llm
