"""Env-derived settings, env constants and fixed identity for the desktop
local runner (split out of ``app.services.desktop_local_runner``).

The desktop shell always talks to the local Mission Control workspace; the
controller itself lives in :mod:`app.services.runner.controller`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from app.services.workspace_context import build_workspace_root

ENABLE_ENV = "AGENTHUB_DESKTOP_LOCAL_RUNNER"
BASE_URL_ENV = "AGENTHUB_DESKTOP_LOCAL_RUNNER_BASE_URL"
ADMIN_NAME_ENV = "AGENTHUB_DESKTOP_ADMIN_NAME"
ADMIN_PASSWORD_ENV = "AGENTHUB_DESKTOP_ADMIN_PASSWORD"
TOKEN_ENV = "AGENTHUB_DESKTOP_RUNNER_TOKEN"
TOKEN_FILE_ENV = "AGENTHUB_DESKTOP_RUNNER_TOKEN_FILE"
USER_ID_ENV = "AGENTHUB_DESKTOP_RUNNER_USER_ID"
WORKSPACE_ROOT_ENV = "AGENTHUB_DESKTOP_WORKSPACE_ROOT"
MODEL_ENV = "AGENTHUB_DESKTOP_LOCAL_RUNNER_MODEL"
MODEL_BASE_URL_ENV = "AGENTHUB_DESKTOP_LOCAL_RUNNER_MODEL_BASE_URL"
PROVIDER_ENV = "AGENTHUB_DESKTOP_LOCAL_RUNNER_PROVIDER"
VERIFY_ENV = "AGENTHUB_DESKTOP_LOCAL_RUNNER_VERIFY"
VERIFY_INTERVAL_ENV = "AGENTHUB_DESKTOP_LOCAL_RUNNER_VERIFY_INTERVAL_SECONDS"
VERIFY_COMMAND_TIMEOUT_ENV = "AGENTHUB_DESKTOP_LOCAL_RUNNER_VERIFY_COMMAND_TIMEOUT"
CONTEXT_CHAR_BUDGET_ENV = "AGENTHUB_DESKTOP_LOCAL_RUNNER_CONTEXT_CHAR_BUDGET"
WORKERS_ENV = "AGENTHUB_DESKTOP_LOCAL_RUNNER_WORKERS"
MCP_CONFIG_ENV = "AGENTHUB_DESKTOP_LOCAL_RUNNER_MCP_CONFIG"
# OS-level sandbox switch (Job Object + restricted token on Windows, bwrap on
# Linux): default on, ``0`` falls back to the original plain subprocess.
SANDBOX_ENV = "AGENTHUB_DESKTOP_LOCAL_RUNNER_SANDBOX"
# P3-2b: prefer the in-process guidance reader over the HTTP feed (the
# desktop runner shares the Mission Control process and database).
INPROCESS_GUIDANCE_ENV = "AGENTHUB_DESKTOP_LOCAL_RUNNER_INPROCESS"

# Test-loop task convention (P1): an objective line starting with this marker
# declares one acceptance command. The unattended verifier runs it in the
# workspace before submitting PASS Evidence; exit code 0 is mandatory.
VERIFY_COMMAND_MARKER = "VERIFY:"
VERIFY_COMMAND_OUTPUT_TAIL_CHARS = 2000

# Shell-command convention (P1-3): objective lines starting with this marker
# declare commands the unattended verifier executes during acceptance. The
# ``command_execute`` tool itself is denial-only, so arbitrary shell runs stay
# confined to declared acceptance semantics.
RUN_COMMAND_MARKER = "RUN:"

# The desktop shell always talks to the local Mission Control workspace.
DESKTOP_WORKSPACE_ID = "local-admin"
DESKTOP_AGENT_ID = "local-desktop-agent"
DESKTOP_ADAPTER_TYPE = "function-calling"
DESKTOP_RUNNER_LABEL = "desktop-local-runner"
DESKTOP_SYSTEM_PROMPT = (
    "你是桌面本地任务执行器。使用提供的文件工具在桌面工作区内完成任务，"
    "不要操作工作区之外的路径，完成后用一句话总结结果。"
    "开始新任务时，可用 memory_search 工具查询历史任务记忆"
    "（记忆键形如 mission-<任务id>，也可按主题关键词搜索）来复用以往任务沉淀的经验。"
)

_MAX_DESKTOP_WORKERS = 4

# Unattended verification identity (submitted as Evidence verifier ref).
DESKTOP_VERIFIER_ID = "desktop-local-verifier"
DESKTOP_VERIFIER_VERSION = "1"
_DESKTOP_EVALUATOR = "artifact-set.v1"

_DEFAULT_BASE_URL = "http://127.0.0.1:28000"
_DEFAULT_MAX_ITERATIONS = 8
_DEFAULT_MAX_TOOL_CALLS = 32
_DEFAULT_MAX_TOTAL_TOKENS = 200_000
_DEFAULT_CONTEXT_CHAR_BUDGET = 24_000
_DEFAULT_TIMEOUT_SECONDS = 300.0
_DEFAULT_LEASE_SECONDS = 300
_DEFAULT_IDLE_DELAY_SECONDS = 0.5
_DEFAULT_MAX_DELAY_SECONDS = 10.0
_DEFAULT_DERIVATION_INTERVAL_SECONDS = 5.0
_DEFAULT_VERIFY_INTERVAL_SECONDS = 5.0
_DEFAULT_VERIFY_COMMAND_TIMEOUT_SECONDS = 120.0


class DesktopRunnerError(RuntimeError):
    """Raised when the desktop local runner cannot be composed or started."""


@dataclass(frozen=True)
class DesktopLocalRunnerSettings:
    """Env-derived, fail-closed desktop runner configuration."""

    enabled: bool
    base_url: str
    admin_name: str
    admin_password: str
    token: str | None
    token_file: str | None
    user_id: str | None
    workspace_id: str
    workspace_root: Path | None
    model_name: str | None
    max_iterations: int
    max_tool_calls: int
    max_total_tokens: int
    timeout_seconds: float
    lease_seconds: int
    idle_delay_seconds: float
    max_delay_seconds: float
    derivation_interval_seconds: float
    verify_enabled: bool
    verify_interval_seconds: float
    verify_command_timeout_seconds: float = _DEFAULT_VERIFY_COMMAND_TIMEOUT_SECONDS
    context_char_budget: int = _DEFAULT_CONTEXT_CHAR_BUDGET
    workers: int = 1
    mcp_config: Path | None = None
    sandbox_enabled: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DesktopLocalRunnerSettings:
        environment = os.environ if env is None else env
        enabled = environment.get(ENABLE_ENV, "").strip() == "1"
        base_url = (
            environment.get(BASE_URL_ENV, "").strip() or _DEFAULT_BASE_URL
        ).rstrip("/")
        workspace_root_value = environment.get(WORKSPACE_ROOT_ENV, "").strip()
        model_name = environment.get(MODEL_ENV, "").strip() or None
        token = environment.get(TOKEN_ENV, "").strip() or None
        token_file = environment.get(TOKEN_FILE_ENV, "").strip() or None
        user_id = environment.get(USER_ID_ENV, "").strip() or None
        if token_file is not None and user_id is None:
            raise DesktopRunnerError(
                f"{TOKEN_FILE_ENV} requires {USER_ID_ENV} to bind the Runner identity"
            )
        if token is not None and user_id is None:
            raise DesktopRunnerError(
                f"{TOKEN_ENV} requires {USER_ID_ENV} to bind the Runner identity"
            )
        max_iterations = _positive_int(
            environment, "AGENTHUB_DESKTOP_LOCAL_RUNNER_MAX_ITERATIONS",
            _DEFAULT_MAX_ITERATIONS,
        )
        max_tool_calls = _positive_int(
            environment, "AGENTHUB_DESKTOP_LOCAL_RUNNER_MAX_TOOL_CALLS",
            _DEFAULT_MAX_TOOL_CALLS,
        )
        max_total_tokens = _positive_int(
            environment, "AGENTHUB_DESKTOP_LOCAL_RUNNER_MAX_TOTAL_TOKENS",
            _DEFAULT_MAX_TOTAL_TOKENS,
        )
        context_char_budget = _positive_int(
            environment, CONTEXT_CHAR_BUDGET_ENV,
            _DEFAULT_CONTEXT_CHAR_BUDGET,
        )
        timeout_seconds = _positive_float(
            environment, "AGENTHUB_DESKTOP_LOCAL_RUNNER_TIMEOUT_SECONDS",
            _DEFAULT_TIMEOUT_SECONDS,
        )
        return cls(
            enabled=enabled,
            base_url=base_url,
            admin_name=environment.get(ADMIN_NAME_ENV, "").strip() or "admin",
            admin_password=environment.get(ADMIN_PASSWORD_ENV, "").strip()
            or "admin123",
            token=token,
            token_file=token_file,
            user_id=user_id,
            workspace_id=DESKTOP_WORKSPACE_ID,
            workspace_root=Path(workspace_root_value) if workspace_root_value else None,
            model_name=model_name,
            max_iterations=max_iterations,
            max_tool_calls=max_tool_calls,
            max_total_tokens=max_total_tokens,
            timeout_seconds=timeout_seconds,
            lease_seconds=_positive_int(
                environment, "AGENTHUB_DESKTOP_LOCAL_RUNNER_LEASE_SECONDS",
                _DEFAULT_LEASE_SECONDS,
            ),
            idle_delay_seconds=_positive_float(
                environment, "AGENTHUB_DESKTOP_LOCAL_RUNNER_IDLE_DELAY_SECONDS",
                _DEFAULT_IDLE_DELAY_SECONDS,
            ),
            max_delay_seconds=_positive_float(
                environment, "AGENTHUB_DESKTOP_LOCAL_RUNNER_MAX_DELAY_SECONDS",
                _DEFAULT_MAX_DELAY_SECONDS,
            ),
            derivation_interval_seconds=_positive_float(
                environment,
                "AGENTHUB_DESKTOP_LOCAL_RUNNER_DERIVATION_INTERVAL_SECONDS",
                _DEFAULT_DERIVATION_INTERVAL_SECONDS,
            ),
            # Unattended verification defaults to on; ``0`` opts out.
            verify_enabled=environment.get(VERIFY_ENV, "1").strip() == "1",
            verify_interval_seconds=_positive_float(
                environment,
                VERIFY_INTERVAL_ENV,
                _DEFAULT_VERIFY_INTERVAL_SECONDS,
            ),
            verify_command_timeout_seconds=_positive_float(
                environment,
                VERIFY_COMMAND_TIMEOUT_ENV,
                _DEFAULT_VERIFY_COMMAND_TIMEOUT_SECONDS,
            ),
            context_char_budget=context_char_budget,
            workers=_worker_count(environment),
            mcp_config=_mcp_config_path(environment),
            # OS sandbox defaults to on; ``0`` degrades to plain subprocess.
            sandbox_enabled=environment.get(SANDBOX_ENV, "1").strip() == "1",
        )

    def default_workspace_root(self) -> Path:
        """Return the runner workspace root under the standard workspace tree."""
        if self.workspace_root is not None:
            return self.workspace_root
        return build_workspace_root(DESKTOP_WORKSPACE_ID, DESKTOP_RUNNER_LABEL)


def _positive_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    value = int(raw)
    if value < 1:
        raise DesktopRunnerError(f"{key} must be positive")
    return value


def _positive_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    value = float(raw)
    if value <= 0:
        raise DesktopRunnerError(f"{key} must be positive")
    return value


def _worker_count(env: Mapping[str, str]) -> int:
    """Resolve the parallel RunnerWorker count (P2-2): default 1, at most 4."""
    raw = env.get(WORKERS_ENV, "").strip()
    if not raw:
        return 1
    value = int(raw)
    if value < 1:
        raise DesktopRunnerError(f"{WORKERS_ENV} must be positive")
    if value > _MAX_DESKTOP_WORKERS:
        raise DesktopRunnerError(
            f"{WORKERS_ENV} must not exceed {_MAX_DESKTOP_WORKERS}"
        )
    return value


def _mcp_config_path(env: Mapping[str, str]) -> Path | None:
    raw = env.get(MCP_CONFIG_ENV, "").strip()
    return Path(raw) if raw else None
