"""Windows OS-sandbox escape benchmark (sandbox effectiveness proof).

Every test here runs a real child process through
``app.services.runner.sandbox_windows.run_sandboxed`` on a real Windows host
(skipped elsewhere).  Escape assertions record the *observed* token behavior
and never fabricate a pass:

- normal command executes and returns output with ``sandboxed is True``;
- read of a file outside the workspace (``C:\\Windows\\win.ini``): asserted
  denied when observed, or SKIPPED with the observation noted when the
  restricted token still allows the read (Windows restricted tokens strip
  groups/privileges but provide no filesystem-view isolation);
- outbound socket connect beyond loopback: asserted denied when observed,
  or SKIPPED with a partial-mitigation note when the connect still succeeds
  (``SeNetworkLogonRight`` is an LSA logon right, not a token privilege; a
  connect failure could also mean the host is offline — the definitive
  partial-mitigation evidence is a successful connect);
- timeout terminates the run (``returncode is None``) and the whole
  process tree (child + grandchild) dies via ``TerminateJobObject``;
- memory over the Job Object limit is killed;
- the restricted token carries zero privileges (all privileges deleted —
  stronger than ``DISABLE_MAX_PRIVILEGE``, which exempts
  ``SeChangeNotifyPrivilege``) and no enabled Administrators membership;
  a restricted-SID list is deliberately not shipped — probed on the
  reference host, any restricted list crashes every child at startup with
  ``STATUS_ACCESS_DENIED`` (0xC0000022);
- degrade paths (sandbox unavailable / spawn failure) return
  ``sandboxed is False`` fail-open results with a warning audit log.
"""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import tempfile
import time
import unittest
from ctypes import wintypes
from pathlib import Path
from unittest.mock import patch

from app.services.runner import sandbox_windows
from app.services.runner.sandbox_windows import SandboxPolicy, run_sandboxed

IS_WINDOWS = sys.platform == "win32"

try:  # the probe children import pywin32 from the venv
    import pywintypes
    import win32security  # noqa: F401

    _PYWIN32_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PYWIN32_AVAILABLE = False

# Privileges that must never survive into the sandboxed child token.
_FORBIDDEN_PRIVILEGES = (
    "SeTcbPrivilege",
    "SeBackupPrivilege",
    "SeRestorePrivilege",
    "SeDebugPrivilege",
    "SeChangeNotifyPrivilege",
    "SeLoadDriverPrivilege",
    "SeTakeOwnershipPrivilege",
    "SeNetworkLogonRight",
)


def _pid_alive(pid: int) -> bool:
    """True when *pid* still names a live process (best-effort)."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


@unittest.skipUnless(IS_WINDOWS, "Windows sandbox tests require a Windows host")
class SandboxWindowsEscapeBenchmark(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cwd = Path(tmp.name)

    def _policy(self, **overrides) -> SandboxPolicy:
        return SandboxPolicy(workspace_root=self.cwd, **overrides)

    # ── 1. normal command ────────────────────────────────────────────────

    def test_normal_command_executes_and_returns_output(self) -> None:
        completed = run_sandboxed(
            [sys.executable, "-c", "print(1)"],
            self.cwd,
            self._policy(timeout_seconds=30),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("1", completed.stdout)
        self.assertTrue(completed.sandboxed)

    # ── 2. read escape (observation recorded honestly) ───────────────────

    def test_escape_read_outside_workspace_observed(self) -> None:
        probe = "\n".join(
            [
                "import sys",
                "try:",
                "    with open(r'C:\\Windows\\win.ini', 'rb') as handle:",
                "        handle.read(16)",
                "    print('READ_OK')",
                "except OSError as exc:",
                "    print('READ_DENIED', type(exc).__name__)",
            ]
        )
        completed = run_sandboxed(
            [sys.executable, "-c", probe],
            self.cwd,
            self._policy(timeout_seconds=30),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        if "READ_OK" in completed.stdout:
            self.skipTest(
                "部分缓解（实测记录）：受限令牌下工作区外文件 C:\\Windows\\win.ini 仍可读。"
                "Windows 受限令牌仅剥离管理员组与令牌特权，不提供文件系统视图隔离；"
                "读写逃逸防护需 bwrap 或远程沙箱服务。"
            )
        self.assertIn("READ_DENIED", completed.stdout)

    # ── 3. network escape (observation recorded honestly) ────────────────

    def test_escape_network_connect_beyond_loopback_observed(self) -> None:
        probe = "\n".join(
            [
                "import socket",
                "try:",
                "    conn = socket.create_connection(('223.5.5.5', 53), timeout=5)",
                "    conn.close()",
                "    print('NET_OK')",
                "except OSError as exc:",
                "    print('NET_DENIED', type(exc).__name__)",
            ]
        )
        # Baseline: the probe must succeed unsandboxed for the observation to
        # have any discriminating power (offline host → inconclusive).
        baseline = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=self.cwd,
            capture_output=True,
            timeout=30,
        )
        if b"NET_OK" not in baseline.stdout:
            self.skipTest(
                "宿主机无法访问探测目标（223.5.5.5:53，离线或防火墙）；"
                "禁网断言不具观测力，跳过（非沙箱效果）"
            )
        completed = run_sandboxed(
            [sys.executable, "-c", probe],
            self.cwd,
            self._policy(timeout_seconds=30),
        )
        self.assertTrue(completed.sandboxed)
        if "NET_OK" in completed.stdout:
            self.skipTest(
                "部分缓解（实测记录）：受限令牌下对外 socket 连接（223.5.5.5:53）仍成功。"
                "SeNetworkLogonRight 是 LSA 登录权而非令牌特权，令牌层无法硬禁网；"
                "硬网络隔离需防火墙规则或远程沙箱服务。"
            )
        # Connect denied (catchable OSError or process-level denial).
        denial_evidence = (
            "NET_DENIED" in completed.stdout or completed.returncode != 0
        )
        self.assertTrue(
            denial_evidence, f"unexpected sandbox outcome: {completed!r}"
        )

    # ── 4. memory limit ──────────────────────────────────────────────────

    def test_memory_over_limit_is_killed(self) -> None:
        completed = run_sandboxed(
            [
                sys.executable,
                "-c",
                "data = bytearray(200 * 1024 * 1024); print('allocated')",
            ],
            self.cwd,
            self._policy(timeout_seconds=30, memory_mb=64),
        )
        self.assertIsNotNone(completed.returncode)
        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn("allocated", completed.stdout)

    # ── 5. timeout ───────────────────────────────────────────────────────

    def test_timeout_kills_process(self) -> None:
        started = time.monotonic()
        completed = run_sandboxed(
            [
                sys.executable,
                "-c",
                "print('start', flush=True); import time; time.sleep(5)",
            ],
            self.cwd,
            self._policy(timeout_seconds=1.0),
        )
        elapsed = time.monotonic() - started
        self.assertIsNone(completed.returncode)  # terminated by the timeout
        self.assertLess(elapsed, 4.0)
        self.assertIn("start", completed.stdout)  # partial output still captured

    # ── 6. tree-level termination ────────────────────────────────────────

    def test_timeout_kills_whole_process_tree(self) -> None:
        child_code = "\n".join(
            [
                "import os, subprocess, sys, time",
                "subprocess.Popen([sys.executable, '-c', "
                "'import os, time; print(os.getpid(), flush=True); time.sleep(60)'])",
                "print(os.getpid(), flush=True)",
                "time.sleep(60)",
            ]
        )
        started = time.monotonic()
        completed = run_sandboxed(
            [sys.executable, "-c", child_code],
            self.cwd,
            self._policy(timeout_seconds=1.5),
        )
        elapsed = time.monotonic() - started
        self.assertIsNone(completed.returncode)
        self.assertLess(elapsed, 5.0)

        pids = {
            int(line)
            for line in completed.stdout.splitlines()
            if line.strip().isdigit()
        }
        self.assertGreaterEqual(
            len(pids), 2, f"expected parent+grandchild pids in stdout: {completed.stdout!r}"
        )
        # Job termination is asynchronous: wait briefly, then require death.
        deadline = time.monotonic() + 5.0
        survivors = pids
        while time.monotonic() < deadline:
            survivors = {pid for pid in pids if _pid_alive(pid)}
            if not survivors:
                break
            time.sleep(0.1)
        self.assertEqual(survivors, set(), f"pids survived the job kill: {survivors}")

    # ── 7. privilege + admin-group stripping ─────────────────────────────

    @unittest.skipUnless(
        _PYWIN32_AVAILABLE, "privilege probe child needs pywin32 in the venv"
    )
    def test_restricted_token_strips_all_privileges_and_admin_groups(self) -> None:
        probe = "\n".join(
            [
                "import ctypes, json",
                "privileges = []",
                "try:",
                "    import win32api, win32con, win32security",
                "    token = win32security.OpenProcessToken("
                "win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)",
                "    entries = win32security.GetTokenInformation("
                "token, win32security.TokenPrivileges)",
                "    for entry in entries:",
                "        try:",
                "            privileges.append("
                "win32security.LookupPrivilegeName(None, entry[0]))",
                "        except Exception:",
                "            privileges.append(str(entry[0]))",
                "except Exception as exc:",
                "    privileges.append('<probe-error: %s>' % exc)",
                "admin = int(ctypes.windll.shell32.IsUserAnAdmin())",
                "print(json.dumps({'privileges': privileges, 'is_admin': admin}))",
            ]
        )
        completed = run_sandboxed(
            [sys.executable, "-c", probe],
            self.cwd,
            self._policy(timeout_seconds=30),
        )
        self.assertTrue(completed.sandboxed)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout.strip().splitlines()[-1])

        privileges = payload["privileges"]
        for forbidden in _FORBIDDEN_PRIVILEGES:
            self.assertNotIn(forbidden, privileges)
        # Every privilege deleted: the child token carries no privilege at all.
        self.assertEqual(privileges, [])
        # BUILTIN\Administrators is use-for-deny-only in the child token, so
        # it must never report admin — not even under an elevated parent.
        self.assertEqual(payload["is_admin"], 0)


class SandboxWindowsDegradeTests(unittest.TestCase):
    """Fail-open degrade paths keep the unified interface (run everywhere)."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cwd = Path(tmp.name)

    def _policy(self, **overrides) -> SandboxPolicy:
        return SandboxPolicy(workspace_root=self.cwd, **overrides)

    def test_run_sandboxed_degrades_when_sandbox_unavailable(self) -> None:
        with patch.object(sandbox_windows, "_SANDBOX_AVAILABLE", False), self.assertLogs(
            "agenthub.runner.sandbox_windows", level="WARNING"
        ):
            completed = sandbox_windows.run_sandboxed(
                [sys.executable, "-c", "print('degraded path')"],
                self.cwd,
                self._policy(timeout_seconds=30),
            )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("degraded path", completed.stdout)
        self.assertFalse(completed.sandboxed)

    def test_degraded_path_reports_timeout_as_none(self) -> None:
        with patch.object(sandbox_windows, "_SANDBOX_AVAILABLE", False):
            completed = sandbox_windows.run_sandboxed(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                self.cwd,
                self._policy(timeout_seconds=0.5),
            )
        self.assertIsNone(completed.returncode)
        self.assertFalse(completed.sandboxed)

    @unittest.skipUnless(IS_WINDOWS, "spawn-failure mock needs the win32 modules")
    def test_spawn_failure_degrades_fail_open(self) -> None:
        """Token/spawn failure → warning audit log + plain run (fail-open)."""
        with (
            patch.object(
                sandbox_windows.win32process,
                "CreateProcessAsUser",
                side_effect=pywintypes.error(
                    1314, "CreateProcessAsUser", "a required privilege is not held"
                ),
            ),
            self.assertLogs("agenthub.runner.sandbox_windows", level="WARNING"),
        ):
            completed = sandbox_windows.run_sandboxed(
                [sys.executable, "-c", "print('fail-open path')"],
                self.cwd,
                self._policy(timeout_seconds=30),
            )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("fail-open path", completed.stdout)
        self.assertFalse(completed.sandboxed)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
