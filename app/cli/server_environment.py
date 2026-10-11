"""Request-scoped environment for the local Mission Control subprocess."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from app.services.tools.policy import ToolExecutionPolicy, resolve_tool_execution_policy

_PROJECT_INSTRUCTIONS_FILE_ENV = "AGENTHUB_DESKTOP_PROJECT_INSTRUCTIONS_FILE"
_WEB_SEARCH_ENV = "AGENTHUB_DESKTOP_WEB_SEARCH"
_TOOL_PERMISSION_ENV = "AGENTHUB_TOOL_PERMISSION_MODE"


class ServerModelSettings(Protocol):
    provider: str
    model: str
    api_key: str
    base_url: str


def build_server_env(
    *,
    db_path: Path,
    data_dir: Path,
    workspace_root: Path,
    port: int,
    model: ServerModelSettings,
    max_total_tokens: int,
    runner_timeout_seconds: float,
    project_instructions_file: Path | None = None,
    web_search: bool = False,
    tool_permission_mode: str | None = None,
    tool_policy: ToolExecutionPolicy | None = None,
    disable_tools: bool = False,
    resume_mission_id: str | None = None,
) -> dict[str, str]:
    """Env for the isolated SQLite mission-control subprocess.

    The model API key travels only through the subprocess environment;
    it is never written to any file under the state directory. Project
    instructions (merged layered AGENTS.md), when present, are exposed
    to the desktop model factory through
    ``AGENTHUB_DESKTOP_PROJECT_INSTRUCTIONS_FILE``.
    """
    env = os.environ.copy()
    env.update(
        {
            "AGENTHUB_DB_BACKEND": "sqlite",
            "AGENTHUB_SQLITE_PATH": str(db_path),
            "AGENTHUB_LOCAL_DATA": str(data_dir),
            "AGENTHUB_DESKTOP_LOCAL_RUNNER": "1",
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_BASE_URL": f"http://127.0.0.1:{port}",
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_VERIFY": "1",
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_VERIFY_INTERVAL_SECONDS": "1",
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_DERIVATION_INTERVAL_SECONDS": "1",
            "AGENTHUB_DESKTOP_WORKSPACE_ROOT": str(workspace_root),
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_MODEL": model.model,
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_MODEL_BASE_URL": model.base_url,
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_PROVIDER": model.provider,
            "AGENTHUB_DESKTOP_MODEL_API_KEY": model.api_key,
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_MAX_ITERATIONS": "8",
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_MAX_TOOL_CALLS": "32",
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_MAX_TOTAL_TOKENS": str(max_total_tokens),
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_TIMEOUT_SECONDS": str(
                runner_timeout_seconds
            ),
            # Keep the self-hosted adapter path even if a gateway is
            # configured in the ambient environment.
            "AGENTHUB_LLM_GATEWAY": "",
            "AGENTHUB_DESKTOP_DISABLE_TOOLS": "1" if disable_tools else "0",
        }
    )
    if project_instructions_file is not None:
        env[_PROJECT_INSTRUCTIONS_FILE_ENV] = str(project_instructions_file)
    # North-star M1: the developer CLI exposes the public-web search tool
    # by default; packaged desktop deployments keep it off.
    env[_WEB_SEARCH_ENV] = "1" if web_search else "0"
    # North-star I-6b: Codex-style tool permission tiering. Only a
    # resolved tier travels — an invalid tier fails fast at the CLI.
    if tool_policy is None:
        tool_policy = resolve_tool_execution_policy(
            workspace_root,
            mode=tool_permission_mode,
            environment_value=env.get(_TOOL_PERMISSION_ENV),
        )
    env[_TOOL_PERMISSION_ENV] = tool_policy.mode.value
    env.pop("AGENTHUB_DESKTOP_LOCAL_RUNNER_RESUME_MISSION_ID", None)
    if resume_mission_id is not None:
        env["AGENTHUB_DESKTOP_LOCAL_RUNNER_RESUME_MISSION_ID"] = resume_mission_id
    return env
