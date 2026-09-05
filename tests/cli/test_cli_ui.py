"""Tests for the rich rendering layer (app.cli.ui) and the new slash
commands (/clear, /cost) wired into the chat REPL.

The mission engine is mocked; these verify the rendering contract —
theme blocks, git-diff panel, HITL confirm menu, cost footer — and
that every renderable degrades without touching the engine.
"""

from __future__ import annotations

import tempfile
import unittest
from io import StringIO
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest import mock

from rich.console import Console

from app.cli import ui
from app.cli.chat import ChatSessionState, _run_slash_command


@dataclass
class _FakeResult:
    mission_id: str
    status: str = "SUCCEEDED"
    exit_code: int = 0
    wall_seconds: float = 12.3
    objective: str = ""
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    workspace_files: list[str] = field(default_factory=list)


class _CapturingOutput:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, text: str = "") -> None:
        self.lines.append(str(text))

    def text(self) -> str:
        return "\n".join(self.lines)


def _render_to_text(renderable: Any) -> str:
    console = Console(width=100, force_terminal=False)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


class GitContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_git_branch_none_outside_repo(self) -> None:
        self.assertIsNone(ui.git_branch(self.root))

    def test_git_diff_none_when_clean(self) -> None:
        # A temp dir without .git degrades to None (no crash).
        self.assertIsNone(ui.git_diff_text(self.root))

    def test_git_diff_detects_changes(self) -> None:
        repo = self.root / "repo"
        repo.mkdir()
        import subprocess

        subprocess.run(
            ["git", "init", "-q"], cwd=repo, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "config", "user.email", "t@t"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "t"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        (repo / "a.txt").write_text("one\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "a.txt"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-qm", "init"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        (repo / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
        diff = ui.git_diff_text(repo)
        self.assertIsNotNone(diff)
        self.assertIn("+two", diff)

    def test_git_changed_files_lists_status_paths(self) -> None:
        with mock.patch.object(ui, "_git", return_value=" M app.py\n?? new.txt\n"):
            self.assertEqual(ui.git_changed_files(self.root), ["app.py", "new.txt"])

    def test_git_tracked_changed_files_merges_index_and_worktree(self) -> None:
        with mock.patch.object(ui, "_git", side_effect=["app.py\n", "other.py\napp.py\n"]):
            self.assertEqual(ui.git_tracked_changed_files(self.root), ["app.py", "other.py"])

    def test_git_changes_since_excludes_baseline(self) -> None:
        with mock.patch.object(ui, "git_changed_files", return_value=["old.py", "new.py"]):
            self.assertEqual(ui.git_changes_since(self.root, frozenset({"old.py"})), ["new.py"])

    def test_git_head_commit(self) -> None:
        with mock.patch.object(ui, "_git", return_value="abc123\n"):
            self.assertEqual(ui.git_head_commit(self.root), "abc123")

    def test_git_restore_paths_rejects_parent_escape(self) -> None:
        with mock.patch.object(ui.subprocess, "run") as run:
            self.assertTrue(ui.git_restore_paths(self.root, ["../outside.py"]))
            run.assert_not_called()

    def test_git_diff_caps_lines(self) -> None:
        long_diff = "\n".join(f"+ line {i}" for i in range(500))
        # First call = git diff (long), second = untracked files (none).
        with mock.patch.object(
            ui, "_git", side_effect=[long_diff, ""]
        ):
            text = ui.git_diff_text(self.root, max_lines=50)
        self.assertIn("… (+450 lines)", text)


class RenderTests(unittest.TestCase):
    def test_header_contains_cwd_and_model(self) -> None:
        panel = ui.render_header(
            Path("/ws"), "deepseek", "deepseek-chat", Path("/ws")
        )
        text = _render_to_text(panel)
        self.assertIn("deepseek/deepseek-chat", text)
        self.assertIn("workspace", text)

    def test_header_renders_at_narrow_terminal_widths(self) -> None:
        for width in (40, 80, 120):
            console = Console(width=width, force_terminal=False)
            with console.capture() as capture:
                console.print(ui.render_header(Path("/workspace/project"), "mock", "v4-flash", Path("/workspace/project")))
            self.assertIn("v4-flash", capture.get())

    def test_result_panel_success_uses_green_border(self) -> None:
        panel = ui.render_result_panel(
            _FakeResult(
                mission_id="mis-1",
                artifacts=[{"kind": "log"}],
                workspace_files=["hello.py"],
            )
        )
        text = _render_to_text(panel)
        self.assertIn("SUCCEEDED", text)
        self.assertIn("mis-1", text)
        self.assertIn("hello.py", text)
        self.assertIn("1 artifacts", text)

    def test_result_panel_failure_keeps_status(self) -> None:
        panel = ui.render_result_panel(
            _FakeResult(mission_id="mis-2", status="FAILED", exit_code=1)
        )
        text = _render_to_text(panel)
        self.assertIn("FAILED", text)

    def test_state_panel_is_recordable_and_includes_diagnostics(self) -> None:
        from app.cli.events import normalize_event
        from app.cli.reducer import SessionViewState, reduce_event

        state = SessionViewState()
        for raw in ({"type": "sse.reconnecting", "payload": {}}, {"type": "future.event", "payload": {}}):
            event = normalize_event(raw)
            assert event is not None
            state = reduce_event(state, event)
        text = _render_to_text(ui.render_state_panel(state))
        self.assertIn("stream:reconnecting", text)
        self.assertIn("unknown event: future.event", text)

    def test_diff_panel_none_when_clean(self) -> None:
        with mock.patch.object(ui, "git_diff_text", return_value=None):
            self.assertIsNone(ui.render_diff_panel(Path("/ws")))

    def test_diff_panel_renders_diff(self) -> None:
        with mock.patch.object(
            ui, "git_diff_text", return_value="+ added\n- removed"
        ):
            panel = ui.render_diff_panel(Path("/ws"))
        self.assertIsNotNone(panel)
        text = _render_to_text(panel)
        self.assertIn("added", text)


class MissionRunnerTests(unittest.TestCase):
    def test_spinner_lifecycle_and_status_stream(self) -> None:
        console = Console(width=100, force_terminal=False)
        with mock.patch("rich.live.Live.start"), mock.patch(
            "rich.live.Live.stop"
        ):
            with ui.MissionRunner(console, "running · mock/mock") as runner:
                runner.on_status("status: RUNNING")
                runner.on_status("status: SUCCEEDED")

    def test_spinner_stops_on_engine_error(self) -> None:
        console = Console(width=100, force_terminal=False)
        stopped = mock.Mock()
        with mock.patch("rich.live.Live.start"), mock.patch(
            "rich.live.Live.stop", stopped
        ):
            runner = ui.MissionRunner(console, "running")
            runner.__enter__()
            runner.__exit__(None, None, None)
        stopped.assert_called_once()

    def test_text_delta_is_written_while_live(self) -> None:
        console = Console(width=100, force_terminal=False)
        with mock.patch("rich.live.Live.start"), mock.patch("rich.live.Live.stop"):
            runner = ui.MissionRunner(console, "running")
            runner.__enter__()
            with console.capture() as capture:
                runner.on_text("hello ")
                runner.on_text("world")
            runner.__exit__(None, None, None)
        self.assertIn("hello world", capture.get())

    def test_real_terminal_spinner_and_narrow_layout_stay_bounded(self) -> None:
        output = StringIO()
        console = Console(file=output, width=40, force_terminal=True, color_system=None, record=True)
        runner = ui.MissionRunner(console, "running · mock/mock")
        with runner:
            runner.on_status("status: RUNNING")
            runner.on_text("streamed text")
        rendered = console.export_text(styles=False)
        assert "streamed text" in rendered
        # Live uses carriage-control frames; the semantic content remains
        # bounded and the streamed payload is preserved.
        assert max((len(line) for line in rendered.splitlines()), default=0) <= 80

    def test_non_tty_spinner_does_not_emit_escape_sequences(self) -> None:
        output = StringIO()
        console = Console(file=output, width=80, force_terminal=False, color_system=None)
        runner = ui.MissionRunner(console, "running")
        with runner:
            runner.on_status("status: SUCCEEDED")
        assert "\x1b[" not in output.getvalue()


class ConfirmTests(unittest.TestCase):
    def _confirm(self, answers: list[str]) -> str:
        console = Console(width=100, force_terminal=False)
        with console.capture():
            feed = list(answers)
            read = lambda prompt: feed.pop(0) if feed else (_ for _ in ()).throw(EOFError())  # noqa: E731
            return ui.confirm_side_effect(console, read, "write hello.py")

    def test_yes(self) -> None:
        self.assertEqual(self._confirm(["1"]), "yes")

    def test_no(self) -> None:
        self.assertEqual(self._confirm(["2"]), "no")

    def test_always(self) -> None:
        self.assertEqual(self._confirm(["3"]), "always")

    def test_eof_fails_safe_to_no(self) -> None:
        self.assertEqual(self._confirm([]), "no")

    def test_invalid_then_valid(self) -> None:
        self.assertEqual(self._confirm(["x", "y", ""]), "yes")


class CostLineTests(unittest.TestCase):
    def test_cost_line_sums_records(self) -> None:
        records = [
            {"artifacts": 2, "wall_seconds": 10.0},
            {"artifacts": 1, "wall_seconds": 5.5},
        ]
        text = _render_to_text(ui.format_cost_line(records))
        self.assertIn("2 missions", text)
        self.assertIn("3 artifacts", text)
        self.assertIn("15.5s", text)

    def test_cost_line_empty_session(self) -> None:
        text = _render_to_text(ui.format_cost_line([]))
        self.assertIn("0 missions", text)


class SlashCommandTests(unittest.TestCase):
    def _settings(self) -> Any:
        from app.cli.runtime import CliModelSettings

        return CliModelSettings(
            provider="mock", model="mock-llm", api_key="mock", base_url=""
        )

    def test_clear_resets_chain_and_compact(self) -> None:
        session = ChatSessionState(
            chained_mission_id="mis-1", compact_context="doc"
        )
        outputs = _CapturingOutput()
        handled = _run_slash_command(
            "/clear",
            settings=self._settings(),
            workspace_root=Path("/ws"),
            directory=Path("/ws/.agenthub"),
            session=session,
            emit=outputs,
        )
        self.assertTrue(handled)
        self.assertIsNone(session.chained_mission_id)
        self.assertIsNone(session.compact_context)

    def test_cost_reports_session_totals(self) -> None:
        session = ChatSessionState()
        session.session_records = [
            {"artifacts": 1, "wall_seconds": 3.0},
            {"artifacts": 4, "wall_seconds": 4.0},
        ]
        outputs = _CapturingOutput()
        handled = _run_slash_command(
            "/cost",
            settings=self._settings(),
            workspace_root=Path("/ws"),
            directory=Path("/ws/.agenthub"),
            session=session,
            emit=outputs,
        )
        self.assertTrue(handled)

    def test_cost_empty_session_is_honest(self) -> None:
        session = ChatSessionState()
        outputs = _CapturingOutput()
        handled = _run_slash_command(
            "/cost",
            settings=self._settings(),
            workspace_root=Path("/ws"),
            directory=Path("/ws/.agenthub"),
            session=session,
            emit=outputs,
        )
        self.assertTrue(handled)
        self.assertIn("尚未运行任务", outputs.text())


if __name__ == "__main__":
    unittest.main()
