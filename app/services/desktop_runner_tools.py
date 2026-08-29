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
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from app.services.harness_service import FunctionTool
from app.services.tool_registry import ToolDefinition
from app.services.tools.definitions import (
    FILE_EDIT,
    FILE_GLOB,
    FILE_READ,
    FILE_SEARCH,
    FILE_WRITE,
    FILE_WRITE_BATCH,
    MKDIR,
)
from app.services.workspace_context import workspace_root_override

# Token-economy first layer: rendered tool results are capped before they
# reach the model. Configurable per runner via build_desktop_runner_tools.
DESKTOP_TOOL_RESULT_MAX_CHARS = 4000

_TRUNCATION_MARKER = "...[截断]"

# plan §3 whitelist order: read/write/edit/batch-write/mkdir/glob/search.
_DESKTOP_TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    FILE_READ,
    FILE_WRITE,
    FILE_EDIT,
    FILE_WRITE_BATCH,
    MKDIR,
    FILE_GLOB,
    FILE_SEARCH,
)

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


def build_desktop_runner_tools(
    workspace_root: Path,
    *,
    max_result_chars: int = DESKTOP_TOOL_RESULT_MAX_CHARS,
) -> list[FunctionTool]:
    """Build the fixed desktop tool whitelist bound to *workspace_root*."""
    if max_result_chars < 1:
        raise ValueError("max_result_chars must be positive")
    resolved_root = workspace_root.resolve()
    resolved_root.mkdir(parents=True, exist_ok=True)
    tools: list[FunctionTool] = []
    for definition in _DESKTOP_TOOL_DEFINITIONS:
        handler = definition.handler
        if handler is None:
            raise ValueError(f"desktop tool has no handler: {definition.name}")
        tools.append(
            FunctionTool(
                name=definition.name,
                description=definition.description,
                parameters=_build_parameter_schema(definition),
                validate_arguments=_validate_arguments_factory(definition),
                handler=_build_tool_executor(handler, resolved_root, max_result_chars),
            )
        )
    return tools


__all__ = [
    "DESKTOP_TOOL_RESULT_MAX_CHARS",
    "build_desktop_runner_tools",
]
