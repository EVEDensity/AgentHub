from __future__ import annotations

from io import StringIO

from rich.console import Console

from app.cli import ui


def _render(value, width: int) -> str:
    output = StringIO()
    console = Console(file=output, width=width, force_terminal=False, color_system=None)
    console.print(value)
    return output.getvalue()


def test_tool_approval_card_is_bounded_at_supported_widths() -> None:
    request = ui.ToolApprovalRequest(
        tool_name="file_write",
        path="src/main.py",
        expected_sha256="a" * 64,
        action="overwrite",
        diff_preview="+new line\n-old line",
        risk="workspace mutation",
    )
    for width in (40, 80, 120):
        rendered = _render(ui.render_tool_approval(request), width)
        assert "[Tool: file_write]" in rendered
        assert "src/main.py" in rendered
        assert max((len(line) for line in rendered.splitlines()), default=0) <= width


def test_non_tty_approval_fails_closed_without_reading_input() -> None:
    request = ui.ToolApprovalRequest(tool_name="shell", command="echo unsafe")
    calls: list[str] = []

    def should_not_read(_prompt: str) -> str:
        calls.append("read")
        return "y"

    console = Console(file=StringIO(), force_terminal=False)
    decision = ui.confirm_tool_approval(console, should_not_read, request, is_tty=False)
    assert decision == "deny"
    assert calls == []


def test_interactive_options_are_tool_scoped() -> None:
    request = ui.ToolApprovalRequest(tool_name="shell", command="echo safe")
    answers = iter(["s"])
    console = Console(file=StringIO(), force_terminal=False)
    decision = ui.confirm_tool_approval(console, lambda _prompt: next(answers), request, is_tty=True)
    assert decision == "session"


def test_approval_card_uses_task_write_scope_label() -> None:
    rendered = _render(ui.render_tool_approval(ui.ToolApprovalRequest(tool_name="file_write")), 80)
    assert "Allow Writes for Task" in rendered
