"""Tests for interactive context compact/replay (north-star I-6c).

/compact folds the session chain into one structured document built
from local mission records (never invented); /replay prints every
session mission's digest. The compacted context is one-shot — it
replaces the per-turn chain for exactly one turn.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.cli import chat, runtime


class SessionStateTests(unittest.TestCase):
    def test_initial_state_has_no_compact_context(self) -> None:
        state = chat.ChatSessionState()
        self.assertIsNone(state.compact_context)
        self.assertEqual(state.session_records, [])


class RecordSessionMissionTests(unittest.TestCase):
    def test_records_digest_fields(self) -> None:
        state = chat.ChatSessionState()
        result = SimpleNamespace(
            mission_id="m-1",
            objective="first line of objective\nsecond line",
            status="SUCCEEDED",
            wall_seconds=1.5,
            artifacts=[1, 2],
            workspace_files=["a.py", "b.py"],
        )
        chat._record_session_mission(state, result)
        record = state.session_records[0]
        self.assertEqual(record["mission_id"], "m-1")
        self.assertEqual(record["objective_first_line"], "first line of objective")
        self.assertEqual(record["status"], "SUCCEEDED")
        self.assertEqual(record["artifacts"], 2)


class ReplayTests(unittest.TestCase):
    def _emit_lines(self) -> list[str]:
        lines: list[str] = []
        return lines

    def test_replay_empty_session(self) -> None:
        state = chat.ChatSessionState()
        lines = self._emit_lines()
        chat._replay_session(state, lines.append)
        self.assertTrue(any("尚无任务" in line for line in lines))

    def test_replay_lists_each_mission(self) -> None:
        state = chat.ChatSessionState()
        state.session_records = [
            {
                "mission_id": "m-1",
                "objective_first_line": "write pong.txt",
                "status": "SUCCEEDED",
                "wall_seconds": 12.0,
                "artifacts": 1,
                "workspace_files": ["pong.txt"],
            },
            {
                "mission_id": "m-2",
                "objective_first_line": "write fizzbuzz",
                "status": "FAILED",
                "wall_seconds": 8.0,
                "artifacts": 0,
                "workspace_files": [],
            },
        ]
        lines = self._emit_lines()
        chat._replay_session(state, lines.append)
        joined = "\n".join(lines)
        self.assertIn("2 个任务", joined)
        self.assertIn("m-1", joined)
        self.assertIn("write pong.txt", joined)
        self.assertIn("FAILED", joined)

    def test_replay_notes_compact_mode(self) -> None:
        state = chat.ChatSessionState()
        state.session_records = [
            {
                "mission_id": "m-1",
                "objective_first_line": "x",
                "status": "SUCCEEDED",
                "wall_seconds": 1.0,
                "artifacts": 0,
                "workspace_files": [],
            }
        ]
        state.compact_context = "doc"
        lines = self._emit_lines()
        chat._replay_session(state, lines.append)
        self.assertTrue(any("/compact" in line for line in lines))


class CompactTests(unittest.TestCase):
    def test_empty_session_is_a_noop(self) -> None:
        state = chat.ChatSessionState()
        lines: list[str] = []
        chat._compact_session_context(
            settings=mock.Mock(),
            workspace_root=Path("."),
            directory=Path("."),
            session=state,
            emit=lines.append,
        )
        self.assertIsNone(state.compact_context)
        self.assertTrue(any("无需压缩" in line for line in lines))

    def test_infra_error_keeps_chain(self) -> None:
        state = chat.ChatSessionState()
        state.session_missions = ["m-1"]
        lines: list[str] = []
        with mock.patch.object(
            chat,
            "MissionControlProcess",
            side_effect=RuntimeError("server did not start"),
        ):
            chat._compact_session_context(
                settings=mock.Mock(),
                workspace_root=Path("."),
                directory=Path("."),
                session=state,
                emit=lines.append,
            )
        self.assertIsNone(state.compact_context)
        self.assertTrue(any("error" in line for line in lines))


class BuildCompactContextTests(unittest.TestCase):
    def test_empty_ids_return_empty_string(self) -> None:
        self.assertEqual(runtime.build_compact_context(mock.Mock(), []), "")

    def test_unreadable_mission_degrades_honestly(self) -> None:
        import httpx

        client = mock.Mock()
        client.get_mission = mock.Mock(side_effect=httpx.HTTPError("gone"))
        document = runtime.build_compact_context(client, ["m-x"])
        self.assertIn("m-x", document)
        self.assertIn("记录不可读", document)


class ExecuteObjectiveContextTextTests(unittest.TestCase):
    """context_text replaces the resume chain for exactly one turn."""

    def test_context_text_preferred_over_resume_id(self) -> None:
        captured: dict[str, object] = {}

        class _Client:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def login(self):
                pass

            def create_and_start_mission(self, *, title, objective, time_seconds):
                captured["objective"] = objective
                # Terminal status immediately — no polling loop.
                return {"id": "m-1", "status": "SUCCEEDED"}

            def get_mission(self, mission_id):
                return {"id": mission_id, "status": "SUCCEEDED", "objective": "o"}

            def work_units(self, mission_id):
                return []

            def artifacts(self, mission_id):
                return []

        class _Process:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            base_url = "http://127.0.0.1:1"

        with mock.patch.object(runtime, "MissionControlProcess", _Process), mock.patch.object(
            runtime, "MissionControlClient", lambda url: _Client()
        ):
            runtime.execute_objective(
                objective="do the next thing",
                workspace_root=Path("."),
                state_dir=Path("."),
                model=runtime.CliModelSettings(
                    provider="mock", model="m", api_key="k", base_url=""
                ),
                resume_mission_id="m-0",
                context_text="COMPACTED CONTEXT DOC",
            )
        objective = str(captured["objective"])
        self.assertIn("COMPACTED CONTEXT DOC", objective)
        self.assertIn("do the next thing", objective)
        # The resume chain must NOT be expanded when compact context wins.
        self.assertNotIn("先前任务 m-0", objective)


if __name__ == "__main__":
    unittest.main()
