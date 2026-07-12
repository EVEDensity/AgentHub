# ─────────────────────────────────────────────────────────────────────
# Plugin Hook Specifications (P0.2 — Sprint 5)
# ─────────────────────────────────────────────────────────────────────
# Defines the hook contract for AgentHub tool system plugins. Plugins
# implement these hooks using @hookimpl to inject behavior into the
# tool execution lifecycle.
#
# Hook lifecycle (in execution order):
#   1. register_tools()     — plugin declares custom tools (startup only)
#   2. tool_categories()    — plugin declares categories it cares about
#   3. pre_tool_use()       — before tool execution (can block/modify)
#   4. [tool executes]
#   5. post_tool_use()      — after tool execution (can modify result)
#
# See docs/plugin-development.md for the full plugin development guide.
# ─────────────────────────────────────────────────────────────────────
from __future__ import annotations

from typing import Any

import pluggy

# Plugin namespace — must match across hookspec and hookimpl markers.
HOOK_NAMESPACE = "agenthub"

hookspec = pluggy.HookspecMarker(HOOK_NAMESPACE)
hookimpl = pluggy.HookimplMarker(HOOK_NAMESPACE)


class ToolHookSpecs:
    """AgentHub tool system hook specifications.

    A plugin class implements one or more of these methods, decorated
    with @hookimpl. The PluginManager calls all registered implementations
    in LIFO order (last registered = first called) by default.
    """

    @hookspec
    def pre_tool_use(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Called before a tool executes.

        Args:
            tool_name: The name of the tool being called.
            arguments: The tool's input arguments (mutable).
            context: Execution context (tenant_id, user_id, session_id, etc.).

        Returns:
            None to continue execution normally, or a dict with:
              - "blocked": bool — if True, abort the tool call
              - "reason": str — human-readable reason (if blocked)
              - "modified_input": dict — replaces the tool's input arguments
            The first hook that blocks short-circuits the chain.
        """

    @hookspec
    def post_tool_use(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Called after a tool executes.

        Args:
            tool_name: The name of the tool that was called.
            arguments: The tool's input arguments (as executed).
            result: The tool's output dict (mutable).
            context: Execution context.

        Returns:
            None to leave the result unchanged, or a dict with:
              - "modified_result": dict — replaces the tool's output
            All hook results are merged (last non-None wins).
        """

    @hookspec
    def register_tools(self) -> list[dict[str, Any]] | None:
        """Register custom tools provided by this plugin.

        Called once at startup. Returns a list of ToolDefinition dicts,
        each with keys: name, description, category, parameters (JSON Schema),
        handler (callable or import path string).

        Returns:
            List of tool definition dicts, or None if the plugin
            provides no tools.
        """

    @hookspec
    def tool_categories(self) -> list[str] | None:
        """Declare tool categories this plugin is interested in.

        Used for filtering: pre/post hooks are only called for tools
        in the declared categories. Returns None to receive all tools.

        Returns:
            List of category names (e.g. ["file", "web"]), or None for all.
        """
