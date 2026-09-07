"""Render the AgentHub Tech Blue terminal UI with real workspace data.

Run from a checkout with ``python -m app.cli.ui_demo``.  This is intentionally
read-only: it exercises the same renderers used by ``chat`` without invoking a
model, writing files, or fabricating a mission result.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.text import Text

from app.cli import ui


@dataclass
class DemoResult:
    mission_id: str = "demo-resume"
    status: str = "SUCCEEDED"
    exit_code: int = 0
    wall_seconds: float = 2.4
    artifacts: list[dict[str, Any]] = field(default_factory=lambda: [{
        "name": "resume.html",
        "size_bytes": 3891,
        "styles": "Modern Flexbox / CSS Grid (Blue Accent)",
        "action": "open resume.html",
    }])
    workspace_files: list[str] = field(default_factory=lambda: ["resume.html"])
    total_tokens: int = 312


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentHub Tech Blue UI demo")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.workspace.resolve()
    console = ui.make_console(force_terminal=True, color_system="truecolor")
    console.print(ui.render_header(root, "deepseek", "deepseek-v4-flash", root))
    console.print(ui.render_tool_started("weather", "Shanghai"))
    console.print(ui.render_tool_completed("Shanghai · 22°C · clear", elapsed_ms=124))
    console.print(ui.render_tool_started("file_walker", "workspace"))
    console.print(ui.render_workspace_classification(root))
    console.print(ui.render_tool_completed("Found workspace files", elapsed_ms=28))
    console.print(ui.render_result_panel(DemoResult()))
    console.print(ui.render_artifact_summary(DemoResult()))
    console.print(Text("🚀 Action: open resume.html", style=ui.STYLE_ACCENT))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
