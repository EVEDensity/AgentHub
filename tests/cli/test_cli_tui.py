"""Headless TUI tests for the developer CLI (north-star M2).

Textual's ``run_test()`` pilot drives the real application without a
terminal. The mission engine is injected as a fake (the ``execute_fn``
seam) so these verify the TUI contract — slash commands, mission
dispatch, chained context, worker marshalling, and honest error
reporting — without booting mission-control.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.cli.tui import AgentHubTUI


@dataclass
class _FakeResult:
    mission_id: str
    status: str = "FAILED"
    exit_code: int = 1
    wall_seconds: float = 2.5
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    workspace_files: list[str] = field(default_factory=list)


class TuiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cwd = Path(self._tmp.name)
        self.calls: list[dict[str, Any]] = []

    def _make_app(self) -> AgentHubTUI:
        def fake_execute(**kwargs: Any) -> _FakeResult:
            self.calls.append(kwargs)
            status_cb = kwargs.get("on_status")
            if status_cb is not None:
                # Emulate the worker-thread callback path.
                status_cb("RUNNING")
            return _FakeResult(mission_id=f"mis-{len(self.calls)}")

        return AgentHubTUI(
            cwd=self.cwd,
            provider=None,
            model=None,
            base_url=None,
            workspace=None,
            mission_timeout=30.0,
            max_total_tokens=1000,
            runner_timeout_seconds=30.0,
            no_web_search=False,
            execute_fn=fake_execute,
        )

    def test_help_command(self) -> None:
        async def scenario() -> None:
            app = self._make_app()
            async with app.run_test() as pilot:
                input_widget = app.query_one("Input")
                input_widget.value = "/help"
                await pilot.press("enter")
                await pilot.pause()
        asyncio.run(scenario())
        self.assertEqual(len(self.calls), 0)

    def test_objective_runs_and_chains(self) -> None:
        async def scenario() -> None:
            app = self._make_app()
            async with app.run_test() as pilot:
                input_widget = app.query_one("Input")
                input_widget.value = "第一个任务"
                await pilot.press("enter")
                await pilot.pause()
                input_widget.value = "第二个任务"
                await pilot.press("enter")
                await pilot.pause()
        asyncio.run(scenario())
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0]["resume_mission_id"], "")
        self.assertEqual(self.calls[1]["resume_mission_id"], "mis-1")

    def test_new_clears_chain(self) -> None:
        async def scenario() -> None:
            app = self._make_app()
            async with app.run_test() as pilot:
                input_widget = app.query_one("Input")
                input_widget.value = "第一个任务"
                await pilot.press("enter")
                await pilot.pause()
                input_widget.value = "/new"
                await pilot.press("enter")
                await pilot.pause()
                input_widget.value = "第二个任务"
                await pilot.press("enter")
                await pilot.pause()
        asyncio.run(scenario())
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[1]["resume_mission_id"], "")

    def test_cost_and_context_commands_use_recorded_session(self) -> None:
        async def scenario() -> None:
            app = self._make_app()
            async with app.run_test() as pilot:
                input_widget = app.query_one("Input")
                input_widget.value = "任务"
                await pilot.press("enter")
                await pilot.pause()
                input_widget.value = "/cost"
                await pilot.press("enter")
                await pilot.pause()
                input_widget.value = "/context"
                await pilot.press("enter")
                await pilot.pause()
            return app

        app = asyncio.run(scenario())
        self.assertEqual(len(app.session.session_records), 1)
        self.assertEqual(app.session.session_records[0]["mission_id"], "mis-1")

    def test_running_guard_rejects_second_objective(self) -> None:
        import threading

        slow_done = threading.Event()

        def slow_execute(**kwargs: Any) -> _FakeResult:
            slow_done.wait(timeout=10)
            return _FakeResult(mission_id="mis-slow")

        app = AgentHubTUI(
            cwd=self.cwd,
            provider=None,
            model=None,
            base_url=None,
            workspace=None,
            mission_timeout=30.0,
            max_total_tokens=1000,
            runner_timeout_seconds=30.0,
            no_web_search=False,
            execute_fn=slow_execute,
        )

        async def scenario() -> None:
            async with app.run_test() as pilot:
                input_widget = app.query_one("Input")
                input_widget.value = "慢任务"
                await pilot.press("enter")
                await pilot.pause(0.2)
                # While the worker holds the engine, a second objective
                # is refused.
                input_widget.value = "并发任务"
                await pilot.press("enter")
                await pilot.pause(0.2)
                slow_done.set()
                # Wait for the completion callback to land on the UI thread.
                for _ in range(50):
                    if app.session.session_missions:
                        break
                    await pilot.pause(0.1)

        asyncio.run(scenario())
        # Only the slow mission ran; the concurrent objective was refused.
        self.assertEqual(app.session.session_missions, ["mis-slow"])

    def test_worker_exception_keeps_session(self) -> None:
        def failing_execute(**kwargs: Any) -> Any:
            raise RuntimeError("server did not boot")

        app = AgentHubTUI(
            cwd=self.cwd,
            provider=None,
            model=None,
            base_url=None,
            workspace=None,
            mission_timeout=30.0,
            max_total_tokens=1000,
            runner_timeout_seconds=30.0,
            no_web_search=False,
            execute_fn=failing_execute,
        )

        async def scenario() -> None:
            async with app.run_test() as pilot:
                input_widget = app.query_one("Input")
                input_widget.value = "任务"
                await pilot.press("enter")
                await pilot.pause()
                # The session stays alive: slash command still handled.
                input_widget.value = "/status"
                await pilot.press("enter")
                await pilot.pause()

        asyncio.run(scenario())
        self.assertIsNone(app.session.chained_mission_id)


if __name__ == "__main__":
    unittest.main()
