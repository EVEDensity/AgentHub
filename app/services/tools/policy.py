"""Canonical tool execution policy shared by CLI, Runner, and tests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class ToolPermissionMode(StrEnum):
    SUGGEST = "suggest"
    EDIT = "edit"
    AUTO = "auto"


@dataclass(frozen=True)
class ToolExecutionPolicy:
    """Resolved capabilities for one workspace-bound tool execution."""

    mode: ToolPermissionMode | str
    allow_code_execute: bool
    allow_shell: bool
    workspace_root: Path

    def __post_init__(self) -> None:
        mode = ToolPermissionMode(str(self.mode).lower())
        root = Path(self.workspace_root).resolve()
        if not root:
            raise ValueError("workspace_root must be provided")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "workspace_root", root)

    @classmethod
    def for_mode(cls, mode: str | ToolPermissionMode, workspace_root: Path) -> "ToolExecutionPolicy":
        resolved = ToolPermissionMode(str(mode).lower())
        return cls(
            mode=resolved,
            allow_code_execute=resolved is ToolPermissionMode.AUTO,
            # Shell remains an explicit acceptance-channel operation. AUTO
            # permits the capability; command_execute still requires its
            # dedicated RUN: contract before a shell is actually run.
            allow_shell=resolved is ToolPermissionMode.AUTO,
            workspace_root=Path(workspace_root),
        )

    @property
    def allows_workspace_write(self) -> bool:
        return self.mode in {ToolPermissionMode.EDIT, ToolPermissionMode.AUTO}


def resolve_tool_execution_policy(
    workspace_root: Path,
    *,
    mode: str | ToolPermissionMode | None = None,
    environment_value: str | None = None,
) -> ToolExecutionPolicy:
    candidate = str(mode or environment_value or "edit").strip().lower()
    return ToolExecutionPolicy.for_mode(candidate, workspace_root)


__all__ = [
    "ToolExecutionPolicy",
    "ToolPermissionMode",
    "resolve_tool_execution_policy",
]
