"""End-to-end CLI tests (North Star M0).

These boot a real isolated SQLite mission-control subprocess with the
desktop local runner and the mock model provider, drive one Mission
through ``app.cli``, and assert the exit-code contract.

The suite is slow (server boot + mission loop ≈ 30s per case) so it is
gated behind ``AGENTHUB_CLI_E2E=1`` and skipped by default. The honest
failure case is the load-bearing assertion: the mock model registers an
artifact (bytes verify) but cannot create files, so the declared
``VERIFY:`` command must veto the mission — proving the executor cannot
self-certify on the CLI path.
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
        self.workspace = Path(self._tmp.name)
        self._cwd = Path.cwd()
        os.chdir(self.workspace)
        self.addCleanup(os.chdir, self._cwd)
        # No model key in the environment: the CLI must fall back to the
        # mock provider rather than failing to boot.
        patcher = mock.patch.dict(
            os.environ,
            {"AGENTHUB_CLI_MODEL_API_KEY": "", "AGENTHUB_DESKTOP_MODEL_API_KEY": ""},
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
        return code, buffer.getvalue()

    def test_exec_honest_failure_verify_gate_vetoes_mock(self) -> None:
        """The mock provider cannot create files; VERIFY must veto it."""
        objective = (
            "创建 hello.py 并打印 hello world。\n"
            "VERIFY: python hello.py"
        )
        code, output = self._run_cli(
            "exec",
            objective,
            "--json",
            "--mission-timeout",
            "240",
        )
        # A JSON document must be parseable from stdout.
        payload = json.loads(output)
        self.assertIn(payload["status"], {"FAILED", "SUCCEEDED"})
        self.assertEqual(payload["status"], "FAILED")
        self.assertEqual(payload["exitCode"], 1)
        self.assertEqual(code, 1)
        # The workspace must not contain fake deliverables.
        self.assertEqual(payload["workspaceFiles"], [])
        self.assertFalse((self.workspace / "hello.py").exists())

    def test_run_reports_mission_status(self) -> None:
        """The human-readable path reports the same verdict as exec."""
        objective = (
            "创建 hello.py 并打印 hello world。\n"
            "VERIFY: python hello.py"
        )
        code, output = self._run_cli("run", objective, "--mission-timeout", "240")
        self.assertEqual(code, 1)
        self.assertIn("FAILED", output)
        self.assertIn("exit code 1", output)


if __name__ == "__main__":
    unittest.main()
