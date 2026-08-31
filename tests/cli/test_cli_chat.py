"""Tests for the interactive chat REPL (north-star M2 baseline).

The mission engine is mocked: these verify the REPL loop contract —
slash commands, prompt/banner emission, chaining of the previous
mission into the next turn, and honest error handling — without
booting a mission-control subprocess.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest import mock

from app.cli import chat as chat_module
from app.cli.chat import ChatSessionState, chat_session, _run_slash_command


@dataclass
class _FakeResult:
    mission_id: str
    status: str = "FAILED"
    exit_code: int = 1
    wall_seconds: float = 3.0
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    workspace_files: list[str] = field(default_factory=list)


class _ScriptedInput:
    """Feeds scripted lines; records the prompt each time."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self._lines:
            raise EOFError
        return self._lines.pop(0)


class _CapturingOutput:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, text: str = "") -> None:
        self.lines.append(str(text))

    def text(self) -> str:
        return "\n".join(self.lines)


class ChatReplTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cwd = Path(self._tmp.name)

    def _run(self, lines: list[str]) -> tuple[int, str, list[dict[str, Any]]]:
        inputs = _ScriptedInput(lines)
        outputs = _CapturingOutput()
        calls: list[dict[str, Any]] = []

        def fake_execute(**kwargs: Any) -> _FakeResult:
            calls.append(kwargs)
            return _FakeResult(mission_id=f"mis-{len(calls)}")

        with mock.patch.object(chat_module, "execute_objective", fake_execute):
            code = chat_session(
                cwd=self.cwd,
                provider=None,
                model=None,
                base_url=None,
                workspace=None,
                input_fn=inputs,
                output_fn=outputs,
            )
        return code, outputs.text(), calls

    def test_banner_and_quit(self) -> None:
        code, text, _ = self._run(["/quit"])
        self.assertEqual(code, 0)
        self.assertIn("AgentHub interactive session", text)
        self.assertIn("bye", text)

    def test_eof_exits_cleanly(self) -> None:
        code, text, _ = self._run([])
        self.assertEqual(code, 0)
        self.assertIn("bye", text)

    def test_unknown_command_reports_honestly(self) -> None:
        code, text, _ = self._run(["/nonsense", "/quit"])
        self.assertEqual(code, 0)
        self.assertIn("未知命令", text)
        self.assertIn("/help", text)

    def test_objective_runs_mission(self) -> None:
        code, text, calls = self._run(["创建 hello.py", "/quit"])
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertIn("创建 hello.py", calls[0]["objective"])
        self.assertIn("mis-1", text)

    def test_chaining_previous_mission(self) -> None:
        _, _, calls = self._run(
            ["第一个任务", "第二个任务", "/quit"]
        )
        self.assertEqual(len(calls), 2)
        # First turn: no chain.
        self.assertEqual(calls[0]["resume_mission_id"], "")
        # Second turn: chained to the first mission's id.
        self.assertEqual(calls[1]["resume_mission_id"], "mis-1")

    def test_new_clears_chain(self) -> None:
        _, _, calls = self._run(
            ["第一个任务", "/new", "第二个任务", "/quit"]
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["resume_mission_id"], "")
        self.assertEqual(calls[1]["resume_mission_id"], "")

    def test_status_reports_session(self) -> None:
        code, text, _ = self._run(["/status", "/quit"])
        self.assertEqual(code, 0)
        self.assertIn("chained mission: （无）", text)
        self.assertIn("session missions: 0", text)

    def test_help_lists_commands(self) -> None:
        _, text, _ = self._run(["/help", "/quit"])
        for command in ("/missions", "/resume", "/new", "/status", "/quit"):
            self.assertIn(command, text)

    def test_infra_error_keeps_session_alive(self) -> None:
        inputs = _ScriptedInput(["任务", "/status", "/quit"])
        outputs = _CapturingOutput()

        def failing_execute(**kwargs: Any) -> Any:
            raise RuntimeError("server did not boot")

        with mock.patch.object(chat_module, "execute_objective", failing_execute):
            code = chat_session(
                cwd=self.cwd,
                provider=None,
                model=None,
                base_url=None,
                workspace=None,
                input_fn=inputs,
                output_fn=outputs,
            )
        self.assertEqual(code, 0)
        self.assertIn("error: server did not boot", outputs.text())
        # Session survived: /status still handled after the failure.
        self.assertIn("session missions: 0", outputs.text())


class SlashCommandUnitTests(unittest.TestCase):
    def _session(self) -> ChatSessionState:
        return ChatSessionState()

    def _settings(self) -> Any:
        from app.cli.runtime import CliModelSettings

        return CliModelSettings(
            provider="mock", model="mock-llm", api_key="mock", base_url=""
        )

    def test_resume_sets_chain(self) -> None:
        session = self._session()
        outputs = _CapturingOutput()
        handled = _run_slash_command(
            "/resume mis-42",
            settings=self._settings(),
            workspace_root=Path("/ws"),
            directory=Path("/ws/.agenthub"),
            session=session,
            emit=outputs,
        )
        self.assertTrue(handled)
        self.assertEqual(session.chained_mission_id, "mis-42")
        self.assertIn("mis-42", outputs.text())

    def test_resume_requires_id(self) -> None:
        session = self._session()
        outputs = _CapturingOutput()
        handled = _run_slash_command(
            "/resume",
            settings=self._settings(),
            workspace_root=Path("/ws"),
            directory=Path("/ws/.agenthub"),
            session=session,
            emit=outputs,
        )
        self.assertTrue(handled)
        self.assertIn("用法", outputs.text())
        self.assertIsNone(session.chained_mission_id)


if __name__ == "__main__":
    unittest.main()
