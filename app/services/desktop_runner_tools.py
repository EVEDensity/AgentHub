"""Harness FunctionTool wrappers for the desktop local runner.

The desktop local runner exposes a fixed whitelist of the built-in file
tools (see docs/internal/architecture/desktop-local-runner-plan.md §3) to
the function-calling Harness. Each wrapper:

- binds every execution to an explicit ``workspace_root`` (the runner's
  workspace binding) through the workspace-context root override, so the
  built-in handlers keep their existing path-traversal protection;
- rejects paths outside ``workspace_root`` up front with an explicit
  error, before any handler runs;
- renders the built-in handler's structured result as plain text and
  truncates oversized content, which is the first token-economy layer.

The whitelist also includes ``code_execute`` (desktop profile): the
decision flows through :class:`PermissionManager` with a locally
injected allow rule — the desktop runs unattended, so the ASK
interaction is replaced by auto-approval bounded to the workspace root
and a 60s hard timeout ceiling (the control-plane tool_call journal
stays the audit trail). Production server paths keep their own
PermissionManager configuration untouched.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.harness_service import (
    FunctionCallingHarness,
    FunctionTool,
    HarnessRequest,
    ModelPort,
)
from app.services.runner_composition import HarnessModelFactoryPort
from app.services.tool_registry import ToolDefinition, ToolParameter
from app.services.tools.definitions import (
    APPLY_CHANGE_SET,
    CODE_EXECUTE,
    FILE_EDIT,
    FILE_GLOB,
    FILE_READ,
    FILE_SEARCH,
    FILE_WRITE,
    FILE_WRITE_BATCH,
    MEMORY_SAVE,
    MEMORY_SEARCH,
    MKDIR,
    CURRENT_TIME,
    CURRENT_DATE,
    WEATHER,
    GIT_STATUS,
    GIT_DIFF,
    GIT_LOG,
    GIT_BRANCH,
    GIT_BRANCH_CREATE,
    GIT_COMMIT,
    GIT_REVERT,
    GIT_CHERRY_PICK,
    AST_SYMBOLS,
    TEST_DISCOVER,
    FORMATTER,
    TYPE_CHECK,
    PACKAGE_MANAGER,
    LOG_TAIL,
    PROCESS_LIST,
    PORT_CHECK,
    SERVICE_HEALTH,
    CHANGE_PLAN,
)
from app.services.workspace_context import workspace_root_override

# Token-economy first layer: rendered tool results are capped before they
# reach the model. Configurable per runner via build_desktop_runner_tools.
DESKTOP_TOOL_RESULT_MAX_CHARS = 4000

# Desktop档位 hard ceiling for one code_execute call. The built-in handler
# keeps its own finer caps (30s scripts / longer install commands); this
# wrapper only guarantees the desktop never runs longer than 60s.
DESKTOP_CODE_EXECUTE_MAX_TIMEOUT = 60

_TRUNCATION_MARKER = "...[截断]"

# plan §3 whitelist order: read/write/edit/batch-write/mkdir/glob/search,
# plus the desktop-profile code_execute unlocked through the local
# PermissionManager policy below, and the self-contained memory tools
# (G8: no path arguments, so the generic executor wraps them unchanged).
# P1-3/P2-3 append the denial-only ``command_execute`` (shell runs are
# confined to objective-declared acceptance commands) and the zero-dependency
# ``lint_check`` Python syntax probe.
_DESKTOP_TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    APPLY_CHANGE_SET,
    FILE_READ,
    FILE_WRITE,
    FILE_EDIT,
    FILE_WRITE_BATCH,
    MKDIR,
    FILE_GLOB,
    FILE_SEARCH,
    CODE_EXECUTE,
    MEMORY_SAVE,
    MEMORY_SEARCH,
    CURRENT_TIME,
    CURRENT_DATE,
    WEATHER,
    GIT_STATUS,
    GIT_DIFF,
    GIT_LOG,
    GIT_BRANCH,
    GIT_BRANCH_CREATE,
    GIT_COMMIT,
    GIT_REVERT,
    GIT_CHERRY_PICK,
    AST_SYMBOLS,
    TEST_DISCOVER,
    FORMATTER,
    TYPE_CHECK,
    PACKAGE_MANAGER,
    LOG_TAIL,
    PROCESS_LIST,
    PORT_CHECK,
    SERVICE_HEALTH,
    CHANGE_PLAN,
)

# ── delegate_subtask (G8): Codex-style spawn_agent for the desktop ───────

# One subtask per call, bounded iterations: the sub-toolset excludes
# delegate_subtask itself, so recursion is structurally impossible and a
# sub agent can never spawn further agents.
DELEGATE_SUBTASK_TOOL_NAME = "delegate_subtask"
DELEGATE_SUBTASK_MAX_ITERATIONS = 4
DELEGATE_SUBTASK_DEFAULT_ITERATIONS = 4
DELEGATE_SUBTASK_RESULT_MAX_CHARS = 4000

# Defaults mirror the desktop runner parent budgets; the controller passes
# its resolved settings through DelegateSubtaskConfig so the subtask reuses
# the parent configuration.
_DELEGATE_SUBTASK_DEFAULT_TOOL_CALLS = 32
_DELEGATE_SUBTASK_DEFAULT_TOTAL_TOKENS = 200_000
_DELEGATE_SUBTASK_DEFAULT_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True)
class DelegateSubtaskConfig:
    """Budgets for one delegate_subtask Harness run."""

    max_tool_calls: int = _DELEGATE_SUBTASK_DEFAULT_TOOL_CALLS
    max_total_tokens: int | None = _DELEGATE_SUBTASK_DEFAULT_TOTAL_TOKENS
    timeout_seconds: float = _DELEGATE_SUBTASK_DEFAULT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if self.max_tool_calls < 1:
            raise ValueError("max_tool_calls must be at least 1")
        if self.max_total_tokens is not None and self.max_total_tokens < 1:
            raise ValueError("max_total_tokens must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


_JSON_SCHEMA_TYPES: dict[str, str] = {
    "string": "string",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
}


def _resolve_tool_path(path: str, workspace_root: Path) -> Path | None:
    """Resolve one tool path argument against the runner workspace root.

    Returns ``None`` when the path escapes the workspace root, including
    absolute paths that point outside of it.
    """
    if not path.strip():
        return None
    try:
        resolved = (workspace_root / path).resolve()
        resolved.relative_to(workspace_root)
    except (OSError, ValueError):
        return None
    return resolved


def _path_arguments(arguments: Mapping[str, Any]) -> list[str]:
    """Collect every path-bearing argument value of a tool call."""
    paths: list[str] = []
    for key, value in arguments.items():
        if isinstance(value, str) and ("path" in key or key == "cwd"):
            paths.append(value)
        elif key == "paths_contents" and isinstance(value, list):
            for item in value:
                if isinstance(item, Mapping) and isinstance(item.get("path"), str):
                    paths.append(item["path"])
        elif key == "changes" and isinstance(value, list):
            for item in value:
                if isinstance(item, Mapping) and isinstance(item.get("path"), str):
                    paths.append(item["path"])
    return paths


def _build_parameter_schema(definition: ToolDefinition) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for parameter in definition.parameters:
        schema: dict[str, Any] = {
            "type": _JSON_SCHEMA_TYPES.get(parameter.type, "string"),
            "description": parameter.description,
        }
        if parameter.default is not None:
            schema["default"] = parameter.default
        if parameter.enum:
            schema["enum"] = list(parameter.enum)
        properties[parameter.name] = schema
        if parameter.required:
            required.append(parameter.name)
    return {"type": "object", "properties": properties, "required": required}


def _validate_arguments_factory(definition: ToolDefinition) -> Callable[
    [Mapping[str, Any]], Mapping[str, Any]
]:
    def validate(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(arguments, Mapping):
            raise TypeError("tool arguments must be an object")
        for parameter in definition.parameters:
            if parameter.required and parameter.name not in arguments:
                raise ValueError(f"missing required argument: {parameter.name}")
        return dict(arguments)

    return validate


def _truncate(content: str, max_chars: int) -> str:
    if len(content) <= max_chars:
        return content
    return (
        f"{content[:max_chars]}\n{_TRUNCATION_MARKER}"
        f"（已显示前 {max_chars} 字符，共 {len(content)} 字符）"
    )


def _render_result(result: object, max_chars: int) -> str:
    """Render one built-in handler result as bounded model-facing text."""
    if not isinstance(result, Mapping):
        return _truncate(json.dumps(result, ensure_ascii=False, sort_keys=True), max_chars)
    if result.get("success") is False:
        error = result.get("error")
        return _truncate(
            f"工具执行失败: {error}" if error else "工具执行失败",
            max_chars,
        )
    content = result.get("result", "")
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, sort_keys=True)
    metadata = result.get("metadata")
    if isinstance(metadata, Mapping) and metadata:
        content = (
            f"{content}\nmetadata: "
            + json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        )
    return _truncate(content, max_chars)


def _build_tool_executor(
    handler: Callable[..., Awaitable[object]],
    workspace_root: Path,
    max_result_chars: int,
) -> Callable[[Mapping[str, Any]], Awaitable[str]]:
    async def execute(arguments: Mapping[str, Any]) -> str:
        for value in _path_arguments(arguments):
            if _resolve_tool_path(value, workspace_root) is None:
                return (
                    f"工具执行失败: 路径 '{value}' 超出桌面工作区允许范围 "
                    f"({workspace_root})"
                )
        with workspace_root_override(workspace_root):
            result = await handler(**dict(arguments))
        return _render_result(result, max_result_chars)

    return execute


def _clamp_desktop_timeout(timeout: float) -> int:
    """Cap a code_execute timeout at the desktop 60s ceiling."""
    return min(int(timeout), DESKTOP_CODE_EXECUTE_MAX_TIMEOUT)


async def _desktop_code_execute_denial(arguments: Mapping[str, Any]) -> str | None:
    """Decide ``code_execute`` approval through the PermissionManager flow.

    The desktop profile runs unattended, so the interactive ASK outcome is
    replaced by local policy injection: a fresh in-memory manager carries a
    system-level allow rule for code_execute, keeping the central decision
    flow (and the PG/server production configuration) untouched. Any
    non-ALLOW decision fails closed.
    """
    from app.services.tools.permission import (
        PermissionBehavior,
        PermissionManager,
        PermissionMode,
        PermissionRule,
        ToolPermissionContext,
    )

    manager = PermissionManager()
    manager.add_rule(
        PermissionRule(
            tool_pattern=CODE_EXECUTE.name,
            path_pattern="*",
            behavior="allow",
            source="desktop_local_policy",
            priority=100,
        )
    )
    result = await manager.check(
        CODE_EXECUTE.name,
        dict(arguments),
        ToolPermissionContext(mode=PermissionMode.DEFAULT),
        risk_level=CODE_EXECUTE.risk_level,
        requires_user_confirmation=CODE_EXECUTE.requires_user_confirmation,
    )
    if result.behavior is PermissionBehavior.ALLOW:
        return None
    return (
        f"工具执行失败: 桌面本地策略未批准 code_execute "
        f"({result.source}: {result.reason})"
    )


def _build_code_execute_executor(
    handler: Callable[..., Awaitable[object]],
    workspace_root: Path,
    max_result_chars: int,
    sandbox_enabled: bool | None = None,
) -> Callable[[Mapping[str, Any]], Awaitable[str]]:
    """Desktop executor for code_execute: PermissionManager gate + 60s cap.

    The execution channel is the built-in subprocess implementation, reused
    unchanged: python/bash only, cwd resolved inside the workspace root by
    the handler, output truncation applied by the shared renderer.  With the
    OS sandbox enabled (``sandbox_enabled=None`` resolves the env switch) the
    run is instead wrapped by the platform sandbox runner
    (Job Object + restricted token on Windows, bwrap on Linux) with the same
    python/bash semantics and rendering.
    """
    inner = _build_tool_executor(handler, workspace_root, max_result_chars)

    async def execute(arguments: Mapping[str, Any]) -> str:
        denial = await _desktop_code_execute_denial(arguments)
        if denial is not None:
            return denial
        clamped = dict(arguments)
        timeout = clamped.get("timeout")
        if isinstance(timeout, (int, float)) and not isinstance(timeout, bool):
            clamped["timeout"] = _clamp_desktop_timeout(timeout)
        if _sandbox_active(sandbox_enabled):
            return await _sandboxed_code_execute(
                clamped, workspace_root, max_result_chars
            )
        return await inner(clamped)

    return execute


def _sandbox_active(flag: bool | None) -> bool:
    """Resolve the OS-sandbox switch: explicit flag wins, else the env default."""
    if flag is not None:
        return bool(flag)
    from app.services.runner.sandbox import sandbox_enabled as resolve

    return resolve()


async def _sandboxed_code_execute(
    arguments: Mapping[str, Any],
    workspace_root: Path,
    max_result_chars: int,
) -> str:
    """Run one code_execute payload through the OS-level sandbox runner.

    Mirrors the built-in handler semantics (python script / bash one-liner or
    script, workspace-confined cwd, install-command metadata) but executes
    via ``run_sandboxed`` so the child lands in a Job Object under a
    restricted token (Windows) or bwrap (Linux).
    """
    from app.services.runner import sandbox
    from app.services.tools.builtin_tools import (
        MAX_CODE_OUTPUT_CHARS,
        _build_python_cmd,
        _is_install_command,
        _is_one_liner,
    )
    from app.services.tools.sandbox_executor import sandbox_executor

    code = str(arguments.get("code") or "")
    language = str(arguments.get("language") or "python").lower()
    if language in ("sh", "shell"):
        language = "bash"
    if language not in ("python", "bash"):
        return f"工具执行失败: 不支持的语言: {arguments.get('language')}。支持: python, bash"

    cwd_resolved = _resolve_tool_path(
        str(arguments.get("cwd") or ".").strip() or ".", workspace_root
    )
    if cwd_resolved is None:
        return (
            f"工具执行失败: 工作目录 '{arguments.get('cwd')}' "
            f"超出桌面工作区允许范围 ({workspace_root})"
        )
    cwd_resolved.mkdir(parents=True, exist_ok=True)

    timeout = arguments.get("timeout")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
        timeout = 30
    timeout_seconds = max(float(timeout), 0.1)

    code_stripped = code.strip()
    is_install = _is_install_command(code_stripped, language)

    exec_dir = workspace_root / ".agenthub_exec"
    exec_dir.mkdir(parents=True, exist_ok=True)
    script_path: Path | None = None
    if language == "python":
        script_path = exec_dir / "script.py"
        script_path.write_text(code, encoding="utf-8")
        argv = [str(part) for part in _build_python_cmd(workspace_root, script_path)]
    elif _is_one_liner(code_stripped):
        argv = ["bash", "-lc", code_stripped]
    else:
        script_path = exec_dir / "script.sh"
        script_path.write_text(code, encoding="utf-8")
        argv = ["bash", str(script_path)]

    policy = sandbox.build_sandbox_policy(workspace_root, timeout_seconds)
    try:
        loop = asyncio.get_running_loop()
        completed = await loop.run_in_executor(
            None, sandbox.run_sandboxed, argv, str(cwd_resolved), policy
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the model
        return _truncate(
            f"工具执行失败: 沙箱执行异常: {exc}", max_result_chars
        )
    finally:
        if script_path is not None:
            try:
                script_path.unlink(missing_ok=True)
            except OSError:
                pass

    returncode = completed.returncode
    stdout_text = completed.stdout or ""
    stderr_text = completed.stderr or ""

    metadata: dict[str, Any] = {
        "language": language,
        "exit_code": returncode,
        "timeout_seconds": timeout_seconds,
        "cwd": str(cwd_resolved.relative_to(workspace_root)) or ".",
        "is_install": is_install,
        # "os" = restricted-token/Job-Object (Windows) or bwrap (Linux)
        # sandbox applied; "plain" = audited fail-open degrade.
        "sandbox": "os" if getattr(completed, "sandboxed", False) else "plain",
    }
    if returncode is None:
        return _render_result(
            {
                "success": False,
                "error": f"代码执行超时 ({int(timeout_seconds)}秒)",
                "metadata": metadata,
            },
            max_result_chars,
        )
    stdout = sandbox_executor.sanitize_output(stdout_text)[:MAX_CODE_OUTPUT_CHARS]
    stderr = sandbox_executor.sanitize_output(stderr_text)[:MAX_CODE_OUTPUT_CHARS]
    result_parts: list[str] = []
    if stdout:
        result_parts.append(f"[标准输出]\n{stdout}")
    if stderr:
        result_parts.append(f"[标准错误]\n{stderr}")
    if returncode != 0:
        result_parts.append(f"[退出码: {returncode}]")
    if not result_parts:
        result_parts.append("[无输出]")
    metadata["stdout_length"] = len(stdout)
    metadata["stderr_length"] = len(stderr)
    return _render_result(
        {
            "success": returncode == 0,
            "result": "\n\n".join(result_parts),
            "metadata": metadata,
        },
        max_result_chars,
    )


def _validate_delegate_arguments_factory() -> Callable[
    [Mapping[str, Any]], Mapping[str, Any]
]:
    def validate(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(arguments, Mapping):
            raise TypeError("tool arguments must be an object")
        objective = arguments.get("objective")
        if not isinstance(objective, str) or not objective.strip():
            raise ValueError("objective must be a non-empty string")
        validated: dict[str, Any] = {"objective": objective}
        max_iterations = arguments.get(
            "max_iterations", DELEGATE_SUBTASK_DEFAULT_ITERATIONS
        )
        if max_iterations is None:
            max_iterations = DELEGATE_SUBTASK_DEFAULT_ITERATIONS
        if not isinstance(max_iterations, int) or isinstance(max_iterations, bool):
            raise ValueError("max_iterations must be an integer")
        validated["max_iterations"] = max(
            1, min(max_iterations, DELEGATE_SUBTASK_MAX_ITERATIONS)
        )
        return validated

    return validate


def _build_delegate_subtask_executor(
    model_factory: HarnessModelFactoryPort,
    subtask_tools: Sequence[FunctionTool],
    config: DelegateSubtaskConfig,
) -> Callable[[Mapping[str, Any]], Awaitable[str]]:
    """Run one bounded child Harness over the whitelist sans delegate_subtask.

    The child model is built from the parent model factory (same model,
    system prompt and context budget); its tool set excludes this tool so a
    sub agent can neither recurse nor spawn further agents. The child's
    final summary text is returned, truncated to the desktop result cap.
    """
    tools = list(subtask_tools)

    async def execute(arguments: Mapping[str, Any]) -> str:
        objective = str(arguments.get("objective", "")).strip()
        max_iterations = int(
            arguments.get("max_iterations", DELEGATE_SUBTASK_DEFAULT_ITERATIONS)
        )
        harness = FunctionCallingHarness(
            model_factory.build(tools),
            tools,
            max_iterations=max_iterations,
            max_tool_calls=config.max_tool_calls,
            max_total_tokens=config.max_total_tokens,
        )
        try:
            result = await harness.execute(
                HarnessRequest(
                    code=objective,
                    language="python",
                    timeout=config.timeout_seconds,
                )
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the parent model
            return _truncate(f"子任务执行失败: {exc}", DELEGATE_SUBTASK_RESULT_MAX_CHARS)
        if not result.sandbox.success:
            reason = result.sandbox.stderr or result.sandbox.stdout or "未知原因"
            return _truncate(
                f"子任务执行失败: {reason}", DELEGATE_SUBTASK_RESULT_MAX_CHARS
            )
        return _truncate(result.sandbox.stdout or "", DELEGATE_SUBTASK_RESULT_MAX_CHARS)

    return execute


# ── command_execute (P1-3): denial-only shell tool ───────────────────────

COMMAND_EXECUTE_TOOL_NAME = "command_execute"
COMMAND_EXECUTE_DENIED_MESSAGE = (
    "工具执行失败: 桌面档不允许通过 command_execute 工具直接执行 shell 命令。"
    "如需运行命令，请在任务 objective 中以 'RUN: <command>' 行声明，"
    "该命令会在验收（verifier）阶段于工作区内执行，其退出码与输出会进入 Evidence 证据。"
)


async def _command_execute_denied(_arguments: Mapping[str, Any]) -> str:
    return COMMAND_EXECUTE_DENIED_MESSAGE


def _validate_command_execute_arguments_factory() -> Callable[
    [Mapping[str, Any]], Mapping[str, Any]
]:
    def validate(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(arguments, Mapping):
            raise TypeError("tool arguments must be an object")
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")
        return {"command": command.strip()}

    return validate


# ── lint_check (P2-3): zero-dependency Python syntax diagnostics ─────────

LINT_CHECK_TOOL_NAME = "lint_check"

_PATH_ONLY_TOOL_DEFINITION = ToolDefinition(
    name=LINT_CHECK_TOOL_NAME,
    description="Python syntax check for one workspace .py file.",
    category="code",
    parameters=[
        ToolParameter(
            name="path",
            type="string",
            required=True,
            description="Workspace-relative .py file path",
        ),
    ],
    return_type="string",
    examples=[],
)


def _build_lint_check_executor(
    workspace_root: Path,
    _max_result_chars: int,
) -> Callable[[Mapping[str, Any]], Awaitable[str]]:
    async def execute(arguments: Mapping[str, Any]) -> str:
        path = arguments.get("path")
        if not isinstance(path, str) or not path.strip():
            return "工具执行失败: path 必须是非空字符串"
        resolved = _resolve_tool_path(path, workspace_root)
        if resolved is None:
            return (
                f"工具执行失败: 路径 '{path}' 超出桌面工作区允许范围 "
                f"({workspace_root})"
            )
        if resolved.suffix != ".py":
            return f"工具执行失败: lint_check 仅支持 .py 文件，收到 '{path}'"
        if not resolved.is_file():
            return f"工具执行失败: 文件不存在: {path}"
        try:
            source = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return f"工具执行失败: 无法读取文件: {exc}"
        try:
            ast.parse(source, filename=str(resolved))
        except SyntaxError as exc:
            line = exc.lineno or 0
            offset = exc.offset or 0
            return f"语法错误: {path} 行 {line} 列 {offset}: {exc.msg}"
        except ValueError as exc:
            return f"工具执行失败: 无法解析文件: {exc}"
        return "OK: 未发现 Python 语法错误"

    return execute


# ── web_search (north-star M1): bounded public-web research tool ──────────

WEB_SEARCH_TOOL_NAME = "web_search"
WEB_SEARCH_ENV = "AGENTHUB_DESKTOP_WEB_SEARCH"


def web_search_enabled() -> bool:
    """Whether the desktop profile exposes the public-web search tool.

    Defaults to off so packaged desktop deployments keep the historical
    bounded whitelist; the developer CLI opts in explicitly.
    """
    return os.environ.get(WEB_SEARCH_ENV, "").strip().lower() in ("1", "true", "yes")


def _validate_web_search_arguments(
    arguments: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not isinstance(arguments, Mapping):
        raise TypeError("tool arguments must be an object")
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    max_results = arguments.get("max_results", 5)
    if isinstance(max_results, bool) or not isinstance(max_results, (int, float)):
        raise ValueError("max_results must be a number")
    return {"query": query.strip(), "max_results": int(max_results)}


def _build_web_search_executor(
    max_result_chars: int,
) -> Callable[[Mapping[str, Any]], Awaitable[str]]:
    from app.services.tools.network_tools import web_search_handler

    async def execute(arguments: Mapping[str, Any]) -> str:
        query = str(arguments.get("query") or "")
        try:
            max_results = int(arguments.get("max_results", 5))
        except (TypeError, ValueError):
            max_results = 5
        outcome = await web_search_handler(query, max_results=max_results)
        # ``_render_result`` expects the {"success", "error"/"result"} shape.
        rendered_outcome = (
            outcome
            if outcome.get("success") is False
            else {
                "success": True,
                "result": {
                    "backend": outcome.get("backend"),
                    "results": outcome.get("results", []),
                },
            }
        )
        return _render_result(rendered_outcome, max_result_chars)

    return execute


WEB_FETCH_TOOL_NAME = "web_fetch"


# ── tool permission tiers (north-star I-6b, Codex-aligned) ────────────────
#
# suggest — read-only plus research tools; the agent can inspect and
#           propose changes but every workspace mutation is denied.
# edit    — full file read/write inside the workspace (the historical
#           desktop default); code stays confined to objective-declared
#           RUN:/VERIFY: acceptance commands.
# auto    — the complete whitelist including code_execute.

TOOL_PERMISSION_SUGGEST = "suggest"
TOOL_PERMISSION_EDIT = "edit"
TOOL_PERMISSION_AUTO = "auto"
TOOL_PERMISSION_MODES = frozenset(
    {TOOL_PERMISSION_SUGGEST, TOOL_PERMISSION_EDIT, TOOL_PERMISSION_AUTO}
)
TOOL_PERMISSION_ENV = "AGENTHUB_TOOL_PERMISSION_MODE"
_TOOL_PERMISSION_DEFAULT = TOOL_PERMISSION_EDIT

# Tools denied per tier. ``suggest`` keeps the read/research/memory set
# and denies every workspace mutation; ``edit`` additionally unlocks the
# file-write group but keeps code_execute locked (shell runs remain
# objective-declared per the acceptance-command contract); ``auto``
# denies nothing.
_PERMISSION_DENIED_TOOLS: dict[str, frozenset[str]] = {
    TOOL_PERMISSION_SUGGEST: frozenset(
        {"file_write", "file_write_batch", "file_edit", "mkdir", "code_execute"}
    ),
    TOOL_PERMISSION_EDIT: frozenset({"code_execute"}),
    TOOL_PERMISSION_AUTO: frozenset(),
}


def resolve_tool_permission_mode(value: str | None = None) -> str:
    """Resolve the effective tool permission tier.

    Explicit argument wins; otherwise the environment switch
    (``AGENTHUB_TOOL_PERMISSION_MODE``) applies; the default tier is
    ``edit`` — the historical desktop whitelist behaviour.
    """
    candidate = (value or os.environ.get(TOOL_PERMISSION_ENV) or "").strip().lower()
    if not candidate:
        return _TOOL_PERMISSION_DEFAULT
    if candidate in TOOL_PERMISSION_MODES:
        return candidate
    raise ValueError(
        f"unknown tool permission mode '{candidate}'; "
        f"expected one of: {', '.join(sorted(TOOL_PERMISSION_MODES))}"
    )


def _build_permission_denied_executor(
    tool_name: str, mode: str
) -> Callable[[Mapping[str, Any]], Awaitable[str]]:
    async def execute(_: Mapping[str, Any]) -> str:
        return (
            f"错误：工具 {tool_name} 在当前权限档位（{mode}）下被禁用。"
            f"suggest=只读；edit=可写工作区文件；auto=完整白名单。"
            "如需该能力，请以更高权限档位重新运行，或在任务 objective "
            "中以 'RUN: <command>' 声明命令（由验收阶段执行并计入证据）。"
        )

    return execute


def _validate_web_fetch_arguments(
    arguments: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not isinstance(arguments, Mapping):
        raise TypeError("tool arguments must be an object")
    url = arguments.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url must be a non-empty string")
    max_chars = arguments.get("max_chars")
    if max_chars is not None:
        if isinstance(max_chars, bool) or not isinstance(max_chars, (int, float)):
            raise ValueError("max_chars must be a number")
        return {"url": url.strip(), "max_chars": int(max_chars)}
    return {"url": url.strip()}


def _build_web_fetch_executor(
    max_result_chars: int,
) -> Callable[[Mapping[str, Any]], Awaitable[str]]:
    from app.services.tools.network_tools import web_fetch_handler

    async def execute(arguments: Mapping[str, Any]) -> str:
        url = str(arguments.get("url") or "")
        try:
            max_chars = int(arguments.get("max_chars") or 0)
        except (TypeError, ValueError):
            max_chars = 0
        outcome = (
            await web_fetch_handler(url, max_chars=max_chars)
            if max_chars > 0
            else await web_fetch_handler(url)
        )
        rendered_outcome = (
            outcome
            if outcome.get("success") is False
            else {"success": True, "result": outcome.get("result", {})}
        )
        return _render_result(rendered_outcome, max_result_chars)

    return execute


# ── skill tools (north-star M1): read-only workspace skill discovery ──────
#
# Reuses the SKILL.md parser from app.services.tools.skill_tools so the
# CLI/desktop path reads the same skill packages as the web product.
# Only the read-only pair is exposed (list + load); script execution stays
# outside the desktop whitelist — agents can read instructions, not run
# arbitrary skill scripts.

SKILL_LIST_TOOL_NAME = "skill_list"
SKILL_LOAD_TOOL_NAME = "skill_load"


def _workspace_skills_dir(workspace_root: Path) -> Path:
    return workspace_root / ".claude" / "skills"


async def _list_workspace_skills(workspace_root: Path) -> list[dict[str, Any]]:
    from app.services.tools.skill_tools import _parse_skill_md

    skills_dir = _workspace_skills_dir(workspace_root)
    entries: list[dict[str, Any]] = []
    if not skills_dir.is_dir():
        return entries
    try:
        children = sorted(skills_dir.iterdir())
    except OSError:
        return entries
    for entry in children:
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        parsed = await _parse_skill_md(entry)
        if parsed is None:
            continue
        entries.append(
            {
                "name": str(parsed.get("name") or entry.name),
                "description": str(parsed.get("description") or ""),
                "version": str(parsed.get("version") or ""),
                "scripts": list(parsed.get("scripts") or []),
            }
        )
    return entries


def _validate_skill_list_arguments(
    arguments: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not isinstance(arguments, Mapping):
        raise TypeError("tool arguments must be an object")
    return {}


def _validate_skill_load_arguments(
    arguments: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not isinstance(arguments, Mapping):
        raise TypeError("tool arguments must be an object")
    name = arguments.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    cleaned = name.strip()
    # Skill names are directory names; refuse traversal-shaped input.
    if "/" in cleaned or "\\" in cleaned or cleaned in (".", ".."):
        raise ValueError("name must be a plain skill directory name")
    return {"name": cleaned}


def _build_skill_tools(
    workspace_root: Path,
    max_result_chars: int,
) -> list[FunctionTool]:
    async def list_execute(_arguments: Mapping[str, Any]) -> str:
        skills = await _list_workspace_skills(workspace_root)
        outcome = {
            "success": True,
            "result": {
                "skills": skills,
                "total": len(skills),
                "hint": (
                    "用 skill_load 加载某技能的完整文档后再遵循其指引"
                    if skills
                    else "工作区 .claude/skills/ 下没有技能包"
                ),
            },
        }
        return _render_result(outcome, max_result_chars)

    async def load_execute(arguments: Mapping[str, Any]) -> str:
        from app.services.tools.skill_tools import _parse_skill_md

        name = str(arguments.get("name") or "")
        skill_dir = _workspace_skills_dir(workspace_root) / name
        if not skill_dir.is_dir():
            return f"工具执行失败: 未找到技能 '{name}'（可用 skill_list 查看）"
        parsed = await _parse_skill_md(skill_dir)
        if parsed is None:
            return f"工具执行失败: 技能 '{name}' 缺少可解析的 SKILL.md"
        outcome = {
            "success": True,
            "result": {
                "name": parsed.get("name"),
                "description": parsed.get("description"),
                "version": parsed.get("version"),
                "body": parsed.get("body"),
                "scripts": parsed.get("scripts"),
            },
        }
        return _render_result(outcome, max_result_chars)

    return [
        FunctionTool(
            name=SKILL_LIST_TOOL_NAME,
            description=(
                "列出工作区 .claude/skills/ 下的技能包（名称/描述/版本/脚本）。"
                "技能是可复用的工作流说明，先列出再按需加载。"
            ),
            parameters={"type": "object", "properties": {}},
            validate_arguments=_validate_skill_list_arguments,
            handler=list_execute,
        ),
        FunctionTool(
            name=SKILL_LOAD_TOOL_NAME,
            description=(
                "加载指定技能的完整文档（SKILL.md 正文与脚本清单），"
                "加载后严格遵循该技能的流程指引。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "技能目录名（见 skill_list 结果）",
                    },
                },
                "required": ["name"],
            },
            validate_arguments=_validate_skill_load_arguments,
            handler=load_execute,
        ),
    ]


def build_desktop_runner_tools(
    workspace_root: Path,
    *,
    max_result_chars: int = DESKTOP_TOOL_RESULT_MAX_CHARS,
    model_factory: HarnessModelFactoryPort | None = None,
    subtask_config: DelegateSubtaskConfig | None = None,
    sandbox_enabled: bool | None = None,
    permission_mode: str | None = None,
) -> list[FunctionTool]:
    """Build the fixed desktop tool whitelist bound to *workspace_root*.

    When *model_factory* is provided, ``delegate_subtask`` is appended as the
    desktop spawn-agent tool; without it the whitelist stays execution-only,
    which also keeps the sub-toolset recursion-free by construction.

    ``sandbox_enabled`` routes ``code_execute`` through the OS-level sandbox
    runner when truthy; ``None`` resolves the env default switch.

    ``permission_mode`` applies the Codex-style tiering
    (suggest/edit/auto, default edit = the historical whitelist). Tools
    denied by the tier are kept in the toolset with their schema but
    wired to a denial executor, so the model sees an actionable denial
    message instead of a silently missing tool.
    """
    if max_result_chars < 1:
        raise ValueError("max_result_chars must be positive")
    mode = resolve_tool_permission_mode(permission_mode)
    denied = _PERMISSION_DENIED_TOOLS[mode]
    resolved_root = workspace_root.resolve()
    resolved_root.mkdir(parents=True, exist_ok=True)
    tools: list[FunctionTool] = []
    for definition in _DESKTOP_TOOL_DEFINITIONS:
        if definition.name in denied:
            executor = _build_permission_denied_executor(definition.name, mode)
        else:
            handler = definition.handler
            if handler is None:
                raise ValueError(f"desktop tool has no handler: {definition.name}")
            executor = (
                _build_code_execute_executor(
                    handler, resolved_root, max_result_chars, sandbox_enabled
                )
                if definition is CODE_EXECUTE
                else _build_tool_executor(handler, resolved_root, max_result_chars)
            )
        tools.append(
            FunctionTool(
                name=definition.name,
                description=definition.description,
                parameters=_build_parameter_schema(definition),
                validate_arguments=_validate_arguments_factory(definition),
                handler=executor,
            )
        )
    tools.append(
        FunctionTool(
            name=COMMAND_EXECUTE_TOOL_NAME,
            description=(
                "执行任意 shell 命令（桌面档已禁用工具直连执行）："
                "请改在任务 objective 中以 'RUN: <command>' 行声明命令，"
                "由验收阶段执行并计入证据。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令",
                    },
                },
                "required": ["command"],
            },
            validate_arguments=_validate_command_execute_arguments_factory(),
            handler=_command_execute_denied,
        )
    )
    tools.append(
        FunctionTool(
            name=LINT_CHECK_TOOL_NAME,
            description=(
                "对工作区内的 .py 文件运行零依赖的 Python 语法检查，"
                "返回首个语法错误的行号与信息；用于写码后自我检查修复。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "工作区内 .py 文件路径（相对或工作区内绝对路径）",
                    },
                },
                "required": ["path"],
            },
            validate_arguments=_validate_arguments_factory(
                _PATH_ONLY_TOOL_DEFINITION
            ),
            handler=_build_lint_check_executor(resolved_root, max_result_chars),
        )
    )
    if web_search_enabled():
        tools.append(
            FunctionTool(
                name=WEB_SEARCH_TOOL_NAME,
                description=(
                    "搜索公开网络并返回带链接的结果列表（标题/URL/摘要），"
                    "用于查文档、找库用法、调研类任务。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索关键词",
                        },
                        "max_results": {
                            "type": "number",
                            "description": "返回结果数（1-8，默认 5）",
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                },
                validate_arguments=_validate_web_search_arguments,
                handler=_build_web_search_executor(max_result_chars),
            )
        )
        tools.append(
            FunctionTool(
                name=WEB_FETCH_TOOL_NAME,
                description=(
                    "抓取一个公开网页并返回可读正文（HTML 转纯文本，含标题），"
                    "用于读取搜索结果中的具体页面内容。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "要抓取的公开 URL",
                        },
                        "max_chars": {
                            "type": "number",
                            "description": "正文最多返回字符数（200-20000，默认 20000）",
                            "default": 20000,
                        },
                    },
                    "required": ["url"],
                },
                validate_arguments=_validate_web_fetch_arguments,
                handler=_build_web_fetch_executor(max_result_chars),
            )
        )
    tools.extend(
        _build_skill_tools(resolved_root, max_result_chars)
    )
    if model_factory is not None:
        config = subtask_config or DelegateSubtaskConfig()
        tools.append(
            FunctionTool(
                name=DELEGATE_SUBTASK_TOOL_NAME,
                description=(
                    "派生一个子 agent 独立完成一个明确定义的子任务，并返回其最终总结。"
                    "子任务无法再派生更深的子任务；用于把检查、调研等辅助工作"
                    "从当前任务中分离出去。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "objective": {
                            "type": "string",
                            "description": "子任务目标，必须自包含且明确可完成",
                        },
                        "max_iterations": {
                            "type": "number",
                            "description": (
                                "子任务最大迭代轮数（1-4，默认 4）"
                            ),
                            "default": DELEGATE_SUBTASK_DEFAULT_ITERATIONS,
                        },
                    },
                    "required": ["objective"],
                },
                validate_arguments=_validate_delegate_arguments_factory(),
                handler=_build_delegate_subtask_executor(
                    model_factory, tools, config
                ),
            )
        )
    return tools


__all__ = [
    "COMMAND_EXECUTE_DENIED_MESSAGE",
    "COMMAND_EXECUTE_TOOL_NAME",
    "DELEGATE_SUBTASK_DEFAULT_ITERATIONS",
    "DELEGATE_SUBTASK_MAX_ITERATIONS",
    "DELEGATE_SUBTASK_RESULT_MAX_CHARS",
    "DELEGATE_SUBTASK_TOOL_NAME",
    "DESKTOP_CODE_EXECUTE_MAX_TIMEOUT",
    "DESKTOP_TOOL_RESULT_MAX_CHARS",
    "LINT_CHECK_TOOL_NAME",
    "SKILL_LIST_TOOL_NAME",
    "SKILL_LOAD_TOOL_NAME",
    "WEB_SEARCH_ENV",
    "WEB_SEARCH_TOOL_NAME",
    "DelegateSubtaskConfig",
    "build_desktop_runner_tools",
    "web_search_enabled",
]
