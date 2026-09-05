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
from app.cli.chat import ChatSessionState, chat_session, _run_slash_command, _likely_side_effect_objective


def test_normal_conversation_is_not_classified_as_side_effect():
    assert not _likely_side_effect_objective("你好")
    assert not _likely_side_effect_objective("请解释这段代码")
    assert _likely_side_effect_objective("请修改 app.py 并运行测试")


@dataclass
class _FakeResult:
    mission_id: str
    status: str = "FAILED"
    exit_code: int = 1
    wall_seconds: float = 3.0
    objective: str = ""
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

    def test_normal_conversation_uses_read_only_without_attempt_snapshot(self) -> None:
        _, _, calls = self._run(["你好", "/quit"])
        self.assertFalse(calls[0]["capture_attempt_snapshot"])
        self.assertEqual(calls[0]["tool_permission_mode"], "suggest")

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

    def test_context_reports_usage_and_compact_state(self) -> None:
        session = self._session()
        session.session_missions = ["m-1"]
        session.session_records = [{"total_tokens": 42}]
        session.compact_context = "summary"
        outputs = _CapturingOutput()
        handled = _run_slash_command(
            "/context",
            settings=self._settings(),
            workspace_root=Path("/ws"),
            directory=Path("/ws/.agenthub"),
            session=session,
            emit=outputs,
        )
        self.assertTrue(handled)
        self.assertIn("42", outputs.text())
        self.assertIn("active", outputs.text())

    def test_status_reports_session_allowed_tools(self) -> None:
        session = self._session()
        session.allowed_tools.add("shell")
        outputs = _CapturingOutput()
        _run_slash_command(
            "/status", settings=self._settings(), workspace_root=Path("/ws"),
            directory=Path("/ws/.agenthub"), session=session, emit=outputs,
        )
        self.assertIn("allowed tools: shell", outputs.text())

    def test_path_permission_commands(self) -> None:
        session = self._session()
        outputs = _CapturingOutput()
        _run_slash_command("/allow shell src/*", settings=self._settings(), workspace_root=Path("/ws"), directory=Path("/ws/.agenthub"), session=session, emit=outputs)
        assert ("shell", "src/*") in session.allowed_paths
        _run_slash_command("/permissions", settings=self._settings(), workspace_root=Path("/ws"), directory=Path("/ws/.agenthub"), session=session, emit=outputs)
        assert "allowed paths" in outputs.text()

    def test_permission_check_explains_cli_source_and_server_precedence(self) -> None:
        session = self._session()
        outputs = _CapturingOutput()
        _run_slash_command("/allow shell src/*", settings=self._settings(), workspace_root=Path("/ws"), directory=Path("/ws/.agenthub"), session=session, emit=outputs)
        _run_slash_command("/permissions explain shell src/main.py", settings=self._settings(), workspace_root=Path("/ws"), directory=Path("/ws/.agenthub"), session=session, emit=outputs)
        assert "来源: cli-session allow" in outputs.text()
        assert "服务端仍需再次校验" in outputs.text()

    def test_permission_policy_persists_and_reloads(self) -> None:
        import tempfile
        from app.cli.chat import _load_permission_policy

        with tempfile.TemporaryDirectory() as raw:
            tmp_path = Path(raw)
            session = self._session()
            outputs = _CapturingOutput()
            directory = tmp_path / ".agenthub"
            _run_slash_command("/allow shell src/*", settings=self._settings(), workspace_root=tmp_path, directory=directory, session=session, emit=outputs)
            _run_slash_command("/deny shell secrets/*", settings=self._settings(), workspace_root=tmp_path, directory=directory, session=session, emit=outputs)
            restored = self._session()
            _load_permission_policy(directory, restored)
            assert ("shell", "src/*") in restored.allowed_paths
            assert ("shell", "secrets/*") in restored.denied_paths

            _run_slash_command("/clear-permissions", settings=self._settings(), workspace_root=tmp_path, directory=directory, session=session, emit=outputs)
            cleared = self._session()
            _load_permission_policy(directory, cleared)
            assert not cleared.allowed_paths
            assert not cleared.denied_paths

    def test_permission_policy_export_import_merge_and_replace(self) -> None:
        import tempfile
        from app.cli.chat import _run_slash_command

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            directory = root / ".agenthub"
            source = self._session()
            output = _CapturingOutput()
            _run_slash_command("/allow shell src/*", settings=self._settings(), workspace_root=root, directory=directory, session=source, emit=output)
            policy = root / "policy.json"
            _run_slash_command(f"/permissions export {policy}", settings=self._settings(), workspace_root=root, directory=directory, session=source, emit=output)
            target = self._session()
            _run_slash_command(f"/permissions import {policy} replace", settings=self._settings(), workspace_root=root, directory=directory, session=target, emit=output)
            assert ("shell", "src/*") in target.allowed_paths

    def test_permission_remove_and_check(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            session = self._session(); output = _CapturingOutput(); directory = root / ".agenthub"
            _run_slash_command("/allow shell src/*", settings=self._settings(), workspace_root=root, directory=directory, session=session, emit=output)
            _run_slash_command("/permissions check shell src/a.py", settings=self._settings(), workspace_root=root, directory=directory, session=session, emit=output)
            assert "匹配: allow" in output.text()
            _run_slash_command("/permissions remove allow shell src/*", settings=self._settings(), workspace_root=root, directory=directory, session=session, emit=output)
            _run_slash_command("/permissions check shell src/a.py", settings=self._settings(), workspace_root=root, directory=directory, session=session, emit=output)
            assert "需要 Decision" in output.text()

    def test_undo_preview_does_not_prompt_or_modify(self) -> None:
        import tempfile
        from app.cli.snapshots import capture_attempt
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); directory = root / ".agenthub"; directory.mkdir()
            import subprocess
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "a.txt").write_text("base", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "a.txt"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"], check=True)
            snap = capture_attempt(root, directory / "attempt-snapshots")
            (root / "a.txt").write_text("agent", encoding="utf-8")
            snap.finalize()
            self.session = self._session()
            self.session.session_records = [{"attempt_snapshot_id": snap.id}]
            output = _CapturingOutput()
            _run_slash_command("/undo preview", settings=self._settings(), workspace_root=root, directory=directory, session=self.session, emit=output, read_line=None)
            assert "撤销预览完成" in output.text()
            assert (root / "a.txt").read_text(encoding="utf-8") == "agent"

    def test_permission_import_invalid_schema_preserves_existing_policy(self) -> None:
        import tempfile, json
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); directory = root / ".agenthub"; session = self._session(); output = _CapturingOutput()
            _run_slash_command("/allow shell src/*", settings=self._settings(), workspace_root=root, directory=directory, session=session, emit=output)
            bad = root / "bad.json"; bad.write_text(json.dumps({"version": 2, "allowedTools": []}), encoding="utf-8")
            _run_slash_command(f"/permissions import {bad} replace", settings=self._settings(), workspace_root=root, directory=directory, session=session, emit=output)
            assert ("shell", "src/*") in session.allowed_paths
            assert "导入失败" in output.text()
if __name__ == "__main__":
    unittest.main()
