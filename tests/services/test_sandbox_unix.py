"""POSIX sandbox (bwrap) parameter construction and degrade matrix.

bwrap invocations are mocked at the ``subprocess`` boundary so the suite
runs on every platform; assertions cover the exact bubblewrap argument
construction, the ``sandboxed`` flag, and every degrade path (no bwrap,
macOS, timeout).  A real bwrap run additionally executes when Linux + bwrap
are actually present.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.runner import sandbox_unix
from app.services.runner.sandbox import get_sandbox_runner, sandbox_enabled
from app.services.runner.sandbox_unix import SandboxPolicy

_HAS_REAL_BWRAP = (
    sys.platform.startswith("linux")
    and sandbox_unix._bwrap_available()  # noqa: SLF001 - test probe
)


def _fake_completed(
    returncode: int = 0, stdout: bytes = b"ok\n", stderr: bytes = b""
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


class SandboxUnixBwrapArgvTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cwd = Path(tmp.name)

    def _policy(self, **overrides) -> SandboxPolicy:
        return SandboxPolicy(workspace_root=self.cwd, **overrides)

    def test_bwrap_argv_construction_with_network_denied(self) -> None:
        with (
            patch.object(sandbox_unix.sys, "platform", "linux"),
            patch.object(sandbox_unix, "_bwrap_available", return_value=True),
            patch.object(
                sandbox_unix.subprocess, "run", return_value=_fake_completed()
            ) as run_spy,
        ):
            completed = sandbox_unix.run_sandboxed(
                ["python", "-c", "print(1)"],
                self.cwd,
                self._policy(timeout_seconds=30),
            )
        expected = [
            "bwrap",
            "--ro-bind", "/", "/",
            "--bind", str(self.cwd), str(self.cwd),
            "--dev", "/dev",
            "--proc", "/proc",
            "--unshare-net",
            "--die-with-parent",
            "--new-session",
            "--",
            "python", "-c", "print(1)",
        ]
        run_spy.assert_called_once()
        self.assertEqual(run_spy.call_args.args[0], expected)
        self.assertEqual(run_spy.call_args.kwargs["cwd"], str(self.cwd))
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "ok\n")
        self.assertTrue(completed.sandboxed)

    def test_bwrap_keeps_network_when_disable_network_false(self) -> None:
        with (
            patch.object(sandbox_unix.sys, "platform", "linux"),
            patch.object(sandbox_unix, "_bwrap_available", return_value=True),
            patch.object(
                sandbox_unix.subprocess, "run", return_value=_fake_completed()
            ) as run_spy,
        ):
            sandbox_unix.run_sandboxed(
                ["python", "-c", "print(1)"],
                self.cwd,
                self._policy(disable_network=False),
            )
        argv = run_spy.call_args.args[0]
        self.assertNotIn("--unshare-net", argv)
        self.assertEqual(argv[0], "bwrap")
        self.assertEqual(argv[-4:], ["--", "python", "-c", "print(1)"])

    def test_bwrap_timeout_reports_none(self) -> None:
        with (
            patch.object(sandbox_unix.sys, "platform", "linux"),
            patch.object(sandbox_unix, "_bwrap_available", return_value=True),
            patch.object(
                sandbox_unix.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(
                    "bwrap", 0.5, output=b"partial\n", stderr=b""
                ),
            ),
        ):
            completed = sandbox_unix.run_sandboxed(
                ["python", "-c", "import time"],
                self.cwd,
                self._policy(timeout_seconds=0.5),
            )
        self.assertIsNone(completed.returncode)
        self.assertEqual(completed.stdout, "partial\n")
        self.assertTrue(completed.sandboxed)


class SandboxUnixDegradeTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cwd = Path(tmp.name)

    def _policy(self, **overrides) -> SandboxPolicy:
        return SandboxPolicy(workspace_root=self.cwd, **overrides)

    def test_degrades_when_bwrap_missing(self) -> None:
        with (
            patch.object(sandbox_unix, "_bwrap_available", return_value=False),
            patch.object(
                sandbox_unix.subprocess, "run", return_value=_fake_completed()
            ) as run_spy,
            self.assertLogs("agenthub.runner.sandbox_unix", level="WARNING"),
        ):
            completed = sandbox_unix.run_sandboxed(
                ["py", "-3"], self.cwd, self._policy()
            )
        run_spy.assert_called_once()
        self.assertEqual(run_spy.call_args.args[0], ["py", "-3"])
        self.assertEqual(completed.returncode, 0)
        self.assertFalse(completed.sandboxed)

    def test_degrades_on_macos_even_with_bwrap(self) -> None:
        # sandbox-exec is deprecated/unsupported on macOS: the module must
        # degrade even when a bwrap binary happens to be present.
        with (
            patch.object(sandbox_unix.sys, "platform", "darwin"),
            patch.object(sandbox_unix, "_bwrap_available", return_value=True),
            patch.object(
                sandbox_unix.subprocess, "run", return_value=_fake_completed()
            ) as run_spy,
            self.assertLogs("agenthub.runner.sandbox_unix", level="WARNING"),
        ):
            completed = sandbox_unix.run_sandboxed(
                ["py", "-3"], self.cwd, self._policy()
            )
        run_spy.assert_called_once()
        self.assertEqual(run_spy.call_args.args[0], ["py", "-3"])
        self.assertFalse(completed.sandboxed)


@unittest.skipUnless(
    _HAS_REAL_BWRAP, "real bwrap run requires Linux with bwrap installed"
)
class SandboxUnixRealBwrapTests(unittest.TestCase):
    def test_bwrap_real_run_returns_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = sandbox_unix.run_sandboxed(
                [sys.executable, "-c", "print('bwrap ok')"],
                Path(tmp),
                SandboxPolicy(workspace_root=tmp, timeout_seconds=30),
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("bwrap ok", completed.stdout)
        self.assertTrue(completed.sandboxed)

    def test_bwrap_unshare_net_denies_loopback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = sandbox_unix.run_sandboxed(
                [
                    sys.executable,
                    "-c",
                    "import socket\n"
                    "socket.create_connection(('127.0.0.1', 59999), timeout=2)\n"
                    "print('network reachable')",
                ],
                Path(tmp),
                SandboxPolicy(workspace_root=tmp, timeout_seconds=30),
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn("network reachable", completed.stderr)


class SandboxFactoryTests(unittest.TestCase):
    def test_get_sandbox_runner_exposes_unified_interface(self) -> None:
        module = get_sandbox_runner()
        self.assertTrue(callable(getattr(module, "run_sandboxed", None)))
        policy_cls = getattr(module, "SandboxPolicy", None)
        self.assertIsNotNone(policy_cls)
        # Both platform modules mirror the same policy fields.
        with tempfile.TemporaryDirectory() as tmp:
            policy = policy_cls(workspace_root=tmp)
            self.assertEqual(policy.timeout_seconds, 60.0)
            self.assertIsNone(policy.memory_mb)
            self.assertIsNone(policy.cpu_rate)
            self.assertTrue(policy.disable_network)

    def test_sandbox_enabled_defaults_on_and_zero_disables(self) -> None:
        self.assertTrue(sandbox_enabled({}))
        self.assertTrue(sandbox_enabled({"AGENTHUB_DESKTOP_LOCAL_RUNNER_SANDBOX": "1"}))
        self.assertFalse(sandbox_enabled({"AGENTHUB_DESKTOP_LOCAL_RUNNER_SANDBOX": "0"}))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
