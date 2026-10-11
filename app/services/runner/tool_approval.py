"""Explicit desktop grants for the canonical Harness approval gateway."""

from collections.abc import Mapping
from typing import Any

from app.services.tools.policy import ToolExecutionPolicy

WORKSPACE_MUTATIONS = frozenset({
    "file_write", "file_edit", "file_patch", "file_write_batch", "apply_change_set",
    "mkdir", "git_commit", "git_branch_create", "git_revert", "git_cherry_pick",
})


def desktop_tool_approval(policy: ToolExecutionPolicy):
    def approve(name: str, arguments: Mapping[str, Any]) -> bool:
        # The bound built-in handler still validates paths and command limits.
        # Remote MCP and arbitrary network/shell tools receive no desktop grant.
        del arguments
        if name in WORKSPACE_MUTATIONS:
            return policy.allows_workspace_write
        return name == "code_execute" and policy.allow_code_execute

    return approve
