# ─────────────────────────────────────────────────────────────────────
# ExamplePlugin — counts tool invocations (demo / testing)
# ─────────────────────────────────────────────────────────────────────
from __future__ import annotations

from typing import Any

# Import the hookimpl marker from the AgentHub tool system. When this
# plugin is loaded via PLUGINS_PATH, the app.services.tools package is
# already on sys.path, so the import works.
from app.services.tools.plugin_spec import hookimpl


class ExamplePlugin:
    """Example plugin: counts tool invocations per tool name.

    Demonstrates the minimal plugin structure: a class with one or more
    ``@hookimpl`` methods. This plugin listens to ``post_tool_use`` and
    increments a counter; it never blocks or modifies results.

    Inspect the counts at runtime:
        from plugins.example_plugin.plugin import ExamplePlugin
        # (the registered instance is held by pluggy; access via
        # plugin_manager.pm.get_plugin("user.ExamplePlugin"))
    """

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    @hookimpl
    def post_tool_use(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Increment the per-tool counter. Returns None (no modification)."""
        self.counts[tool_name] = self.counts.get(tool_name, 0) + 1
        return None

    @hookimpl
    def tool_categories(self) -> list[str] | None:
        """Receive events for all tool categories."""
        return None
