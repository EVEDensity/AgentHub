"""End-to-end CLI tests (North Star M0).

These boot a real isolated SQLite mission-control subprocess with the
desktop local runner and the mock model provider, drive one Mission
through ``app.cli``, and assert the exit-code contract.

The suite is slow (server boot + mission loop ≈ 30s per case) so it is
gated behind ``AGENTHUB_CLI_E2E=1`` and skipped by default. The mock provider
produces output bytes, not workspace deliverables. Success verifies only those
registered bytes. Objective ``VERIFY:`` / ``RUN:`` markers never execute shell
commands and cannot stand in for trusted test evidence.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


@unittest.skipUnless(
    os.environ.get("AGENTHUB_CLI_E2E") == "1",
    "set AGENTHUB_CLI_E2E=1 to run the slow CLI end-to-end suite",
)
class CliEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._runtime_tmp = tempfile.TemporaryDirectory(prefix="cli-state-")
        self.addCleanup(self._runtime_tmp.cleanup)
        self.workspace = Path(self._tmp.name)
        self._cwd = Path.cwd()
        os.chdir(self.workspace)
        self.addCleanup(os.chdir, self._cwd)
        # These are deterministic offline flow tests; explicitly select mock
        # rather than inheriting an operator's configured provider.
        patcher = mock.patch.dict(
            os.environ,
            {"AGENTHUB_CLI_MODEL_API_KEY": "", "AGENTHUB_DESKTOP_MODEL_API_KEY": "",
             "AGENTHUB_RUNNER_STATE_ROOT": self._runtime_tmp.name},
            clear=False,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run_cli(self, *argv: str) -> tuple[int, str]:
        from app.cli.main import cli_main

        buffer = io.StringIO()
        code = -1
        with redirect_stdout(buffer):
            code = cli_main(list(argv))
        if code in {3, 4}:
            from app.cli.control_state import control_state_directory
            control = control_state_directory(self.workspace, self.workspace / ".agenthub")
            for log in (control / "logs").glob("mission-control-*.log"):
                print("Mock CLI runtime diagnostics:\n" + "\n".join(
                    log.read_text(encoding="utf-8", errors="replace").splitlines()[-100:],
                ), file=sys.stderr)
        return code, buffer.getvalue()

    def test_exec_success_means_output_integrity_and_never_runs_objective_commands(self) -> None:
        """A mock response may pass integrity while code creation stays unproven."""
        objective = (
            "创建 hello.py 并打印 hello world。\n"
            "VERIFY: python hello.py\nRUN: python hello.py"
        )
        code, output = self._run_cli(
            "exec",
            objective,
            "--json",
            "--provider", "mock",
            "--no-web-search",
            "--mission-timeout",
            "240",
        )
        # A JSON document must be parseable from stdout.
        payload = json.loads(output)
        self.assertEqual(payload["status"], "SUCCEEDED")
        self.assertEqual(payload["exitCode"], 0)
        self.assertEqual(code, 0)
        # The workspace must not contain fake deliverables.
        self.assertEqual(payload["workspaceFiles"], [])
        self.assertFalse((self.workspace / "hello.py").exists())

    def test_run_reports_mission_status(self) -> None:
        """The human-readable path reports the same output-integrity status."""
        objective = (
            "创建 hello.py 并打印 hello world。\n"
            "VERIFY: python hello.py\nRUN: python hello.py"
        )
        code, output = self._run_cli("run", objective, "--provider", "mock", "--no-web-search", "--mission-timeout", "240")
        self.assertEqual(code, 0)
        self.assertIn("SUCCEEDED", output)
        self.assertIn("exit code 0", output)


if __name__ == "__main__":
    unittest.main()
