from __future__ import annotations

import os
from pathlib import Path

# ── Load .env file (if python-dotenv is available) ─────────────────
try:
    from dotenv import load_dotenv
    _env_file = Path(__file__).resolve().parent.parent / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
except ImportError:
    pass  # dotenv not installed — env vars must be set externally

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# ── Per-user per-session isolated workspace directories ─────────────────
# Each user+session gets an independent subdirectory:
#   DATA_DIR/workspaces/{user_id}/{session_id}/
# Agent tools (file_read, file_write, code_execute, git, etc.) resolve
# their working directory dynamically from the workspace context, never
# from PROJECT_ROOT.
WORKSPACES_DIR = DATA_DIR / "workspaces"

# ── Database ───────────────────────────────────────────────────────
# PostgreSQL connection URL (required — e.g. Neon serverless).
# Set via .env file or environment variable.
DATABASE_URL = os.getenv("DATABASE_URL", "")

APP_NAME = "AgentHub 多智能体协作平台"
APP_VERSION = "3.0-modular"

DEFAULT_SESSION_ID = "session-1"
DEFAULT_USER_ID = "local-admin"

# Memory system: project-local .claude/memory/ directory
MEMORY_DIR = PROJECT_ROOT / ".claude" / "memory"

# Auto memory extraction settings
AUTO_MEMORY_ENABLED = os.getenv("AGENTHUB_AUTO_MEMORY", "true").lower() != "false"
AUTO_MEMORY_MIN_MSG = int(os.getenv("AGENTHUB_MEMORY_MIN_MSG", "2"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

# ── Search provider API keys ──────────────────────────────────────────
# Set at least one to enable real web search.  If none are set the
# DuckDuckGo free API is used as a best-effort fallback.
#
# WEB_SEARCH_MODE controls provider priority:
#   auto      → smart fallback chain (default)
#   bing      → Bing Web Search API v7 only (explicit)
#   serpapi   → SerpAPI / Google via SerpAPI only
#   google    → Google Custom Search API only
#   tavily    → Tavily Search API only
#   brave     → Brave Search API only
#   duckduckgo→ DuckDuckGo Instant Answer (free, no key)
#   disabled  → no web search (returns unavailable message)
WEB_SEARCH_MODE = os.getenv("WEB_SEARCH_MODE", "auto")
BING_API_KEY = os.getenv("BING_API_KEY", "")
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")

# ── Skill system directories ────────────────────────────────────────
# Skills are loaded from two locations (project-level takes precedence):
#   1. ~/.claude/skills/   — user-level, available across all projects
#   2. .claude/skills/     — project-level (under PROJECT_ROOT)
SKILLS_DIR_USER = Path.home() / ".claude" / "skills"
SKILLS_DIR_PROJECT = PROJECT_ROOT / ".claude" / "skills"

# ── Command execution limits ─────────────────────────────────────────
COMMAND_EXECUTE_TIMEOUT = int(os.getenv("AGENTHUB_COMMAND_TIMEOUT", "120"))
COMMAND_EXECUTE_MAX_OUTPUT = int(os.getenv("AGENTHUB_COMMAND_MAX_OUTPUT", "100000"))

# Default 600 s (10 min) — complex tasks (full blog HTML, multi-file codegen,
# long chains of tool calls) can take several minutes.  The old 45 s default
# was too aggressive and silently killed legitimate long-running requests.
# Operators can still override via AGENTHUB_REQUEST_TIMEOUT if needed.
REQUEST_TIMEOUT_SECONDS = float(os.getenv("AGENTHUB_REQUEST_TIMEOUT", "600"))
ENABLE_REAL_LLM = os.getenv("AGENTHUB_ENABLE_REAL_LLM", "true").lower() != "false"

# ── Orchestrator pre-processing ──────────────────────────────────────
# When enabled, the Orchestrator pre-processes complex user questions
# before sending them to the main LLM — performing intent analysis,
# question clarification, sub-task decomposition, and constraint extraction.
ORCHESTRATOR_PREPROCESS_ENABLED = os.getenv("AGENTHUB_ORCHESTRATOR_PREPROCESS", "true").lower() != "false"
# Minimum character length for a question to trigger pre-processing.
# Simple greetings and very short questions (< this length) skip
# pre-processing to avoid unnecessary LLM calls.
ORCHESTRATOR_PREPROCESS_MIN_LENGTH = int(os.getenv("AGENTHUB_PREPROCESS_MIN_LENGTH", "30"))

# ── Office document preview limits ───────────────────────────────────
# PPTX/DOCX files often embed large images; these limits are more
# permissive than the general 5 MB cap.  Image optimisation (Pillow)
# further reduces the output HTML size inside the browser.
OFFICE_PREVIEW_MAX_MB = int(os.getenv("AGENTHUB_OFFICE_PREVIEW_MAX_MB", "20"))
OFFICE_WORKSPACE_READ_MAX_MB = int(os.getenv("AGENTHUB_OFFICE_WORKSPACE_READ_MAX_MB", "30"))

# ── Streaming timeouts ──────────────────────────────────────────────
# Maximum time to wait for the first streaming chunk (token) before
# considering the model unresponsive and falling back.
STREAM_FIRST_BYTE_TIMEOUT = int(os.getenv("AGENTHUB_STREAM_FIRST_BYTE_TIMEOUT", "30"))
# Maximum idle time between streaming chunks before the connection is
# considered stale and the stream is interrupted.
STREAM_IDLE_TIMEOUT = int(os.getenv("AGENTHUB_STREAM_IDLE_TIMEOUT", "120"))
