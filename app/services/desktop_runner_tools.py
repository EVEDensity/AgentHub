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
import json
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
) -> Callable[[Mapping[str, Any]], Awaitable[str]]:
    """Desktop executor for code_execute: PermissionManager gate + 60s cap.

    The execution channel itself is the built-in subprocess implementation,
    reused unchanged: python/bash only, cwd resolved inside the workspace
    root by the handler, output truncation applied by the shared renderer.
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
        return await inner(clamped)

    return execute


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


def build_desktop_runner_tools(
    workspace_root: Path,
    *,
    max_result_chars: int = DESKTOP_TOOL_RESULT_MAX_CHARS,
    model_factory: HarnessModelFactoryPort | None = None,
    subtask_config: DelegateSubtaskConfig | None = None,
) -> list[FunctionTool]:
    """Build the fixed desktop tool whitelist bound to *workspace_root*.

    When *model_factory* is provided, ``delegate_subtask`` is appended as the
    desktop spawn-agent tool; without it the whitelist stays execution-only,
    which also keeps the sub-toolset recursion-free by construction.
    """
    if max_result_chars < 1:
        raise ValueError("max_result_chars must be positive")
    resolved_root = workspace_root.resolve()
    resolved_root.mkdir(parents=True, exist_ok=True)
    tools: list[FunctionTool] = []
    for definition in _DESKTOP_TOOL_DEFINITIONS:
        handler = definition.handler
        if handler is None:
            raise ValueError(f"desktop tool has no handler: {definition.name}")
        executor = (
            _build_code_execute_executor(handler, resolved_root, max_result_chars)
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
    "DelegateSubtaskConfig",
    "build_desktop_runner_tools",
]
