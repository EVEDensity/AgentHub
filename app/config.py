from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "agenthub.sqlite3"
WORKSPACE_REPO_PATH = PROJECT_ROOT

APP_NAME = "AgentHub 多智能体协作平台"
APP_VERSION = "3.0-modular"

DEFAULT_SESSION_ID = "session-1"
DEFAULT_USER_ID = "local-admin"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

REQUEST_TIMEOUT_SECONDS = float(os.getenv("AGENTHUB_REQUEST_TIMEOUT", "45"))
ENABLE_REAL_LLM = os.getenv("AGENTHUB_ENABLE_REAL_LLM", "true").lower() != "false"
