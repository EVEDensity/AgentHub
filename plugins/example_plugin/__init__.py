# ─────────────────────────────────────────────────────────────────────
# example_plugin package — demo plugin for the AgentHub tool system
# ─────────────────────────────────────────────────────────────────────
# Re-exports ExamplePlugin from plugin.py so it can be imported either way:
#   from plugins.example_plugin import ExamplePlugin
#   from plugins.example_plugin.plugin import ExamplePlugin
# ─────────────────────────────────────────────────────────────────────
from __future__ import annotations

from .plugin import ExamplePlugin

__all__ = ["ExamplePlugin"]
