"""Resolve the effective Desktop tool surface for diagnostics and CLI UX."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.services.tool_registry import ToolDefinition, tool_registry
from app.services.tools.policy import ToolExecutionPolicy, resolve_tool_execution_policy


@dataclass(frozen=True)
class ToolAvailability:
    name: str
    registered: bool
    runner_exposed: bool
    permission: str
    contract_capability: str
    server_deny: str
    environment: str
    executable: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_tool_availability(
    workspace_root: Path,
    *,
    policy: ToolExecutionPolicy | None = None,
    server_denied: set[str] | None = None,
    names: list[str] | None = None,
) -> list[ToolAvailability]:
    """Compute registry ∩ runner ∩ policy ∩ contract ∩ environment.

    Contract and server integrations are intentionally fail-closed only when
    an explicit deny is supplied.  In their absence the result is reported as
    ``unknown`` rather than pretending the tool is universally available.
    """
    from app.services.desktop_runner_tools import build_desktop_runner_tools

    policy = policy or resolve_tool_execution_policy(workspace_root)
    registered = {item.name: item for item in tool_registry.list_all()}
    runner_names = {item.name for item in build_desktop_runner_tools(workspace_root, policy=policy)}
    denied = server_denied or set()
    selected = names or sorted(set(registered) | runner_names)
    result: list[ToolAvailability] = []
    for name in selected:
        definition: ToolDefinition | None = registered.get(name)
        is_registered = definition is not None
        exposed = name in runner_names
        permission = "allow" if exposed else "deny:runner_not_exposed"
        contract = "unknown:not_declared"
        server = "deny:server" if name in denied else "unknown:not_checked"
        environment = "available" if exposed else "unknown:not_exposed"
        executable = is_registered and exposed and name not in denied
        if name in {"file_write", "file_edit", "apply_change_set", "file_write_batch", "mkdir"}:
            contract = "allow:repository.write" if policy.allows_workspace_write else "deny:repository.write"
            executable = executable and policy.allows_workspace_write
        elif name in {"code_execute", "command_execute"}:
            contract = "allow:execution" if policy.allow_code_execute or name == "command_execute" else "deny:execution"
            executable = executable and (policy.allow_code_execute or name == "command_execute")
        if name in denied:
            executable = False
        result.append(ToolAvailability(name, is_registered, exposed, permission, contract, server, environment, executable))
    return result


__all__ = ["ToolAvailability", "resolve_tool_availability"]
