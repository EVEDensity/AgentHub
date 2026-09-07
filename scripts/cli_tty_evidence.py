"""Physical TTY width gate for the Tech Blue terminal renderer.

Run this command from a real terminal with ``AGENTHUB_CLI_TTY_WIDTH`` set to
40, 80, or 120.  CI pipes and redirected output intentionally produce SKIP.
"""
from __future__ import annotations

import json
import os
import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rich.console import Console

from app.cli import ui
from scripts.production_evidence import new_evidence, write_evidence


def main() -> int:
    output = os.environ.get("AGENTHUB_TTY_EVIDENCE_OUTPUT", "").strip()
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return _emit(output, status="SKIP", errorType="physical_tty_required")
    try:
        width = int(os.environ.get("AGENTHUB_CLI_TTY_WIDTH", ""))
    except ValueError:
        return _emit(output, status="FAIL", errorType="invalid_tty_width")
    if width not in {40, 80, 120}:
        return _emit(output, status="FAIL", errorType="unsupported_tty_width", width=width)

    output_buffer = StringIO()
    console = Console(
        file=output_buffer,
        width=width,
        force_terminal=True,
        color_system="standard",
        record=True,
    )
    result = SimpleNamespace(
        status="SUCCEEDED",
        mission_id="tty-evidence",
        exit_code=0,
        wall_seconds=2.4,
        artifacts=[{"name": "result.txt", "size_bytes": 128, "action": "open result.txt"}],
        workspace_files=["README.md", "result.txt"],
    )
    console.print(ui.render_header(Path.cwd(), "deepseek", "deepseek-v4-flash", Path.cwd()))
    console.print(ui.render_tool_started("search_files", 'extension=".py"'))
    console.print(ui.render_tool_completed("Found 18 files across 4 directories", elapsed_ms=28))
    console.print(ui.render_artifact_summary(result))
    console.print(ui.render_result_panel(result))
    rendered = console.export_text(styles=False)
    max_line = max((len(line) for line in rendered.splitlines()), default=0)
    ansi_present = "\x1b[" in output_buffer.getvalue()
    return _emit(
        output,
        status="PASS" if max_line <= width and ansi_present else "FAIL",
        width=width,
        maxRenderedLine=max_line,
        ansiRendered=ansi_present,
    )


def _emit(output: str, **fields: object) -> int:
    record = new_evidence(scope="tty", evidence_level="real-tty", **fields)
    rendered = json.dumps(record, ensure_ascii=False, sort_keys=True)
    print(rendered)
    write_evidence(record, scope="tty", mirror_path=output or None)
    return 0 if record["status"] in {"PASS", "SKIP"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
