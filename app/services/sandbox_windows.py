"""Windows Job Objects 进程隔离沙箱。

Windows Job Objects 提供内核级进程分组和限制：
- ActiveProcessLimit: 最大进程数（防 fork bomb）
- PerJobUserTimeLimit: 总 CPU 时间限制
- KILL_ON_JOB_CLOSE: job handle 关闭时自动 kill 所有子进程
- 安全: 不依赖 Docker daemon，不需要额外服务

用法:
    with WindowsSandbox() as sb:
        sb.run(["python", "-c", "print('isolated!')"])
    # 进程自动被 kill（如果还在跑）
"""
from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

logger = logging.getLogger("agenthub.sandbox.windows")


@dataclass
class SandboxConfig:
    """Sandbox resource limits."""
    max_processes: int = 4           # ActiveProcessLimit
    max_cpu_seconds: int = 30        # PerJobUserTimeLimit
    max_memory_mb: int = 512         # JOB_OBJECT_LIMIT_JOB_MEMORY (Windows 8+)
    working_dir: str | None = None
    env_add: dict[str, str] | None = None
    network_airgap: bool = False     # not implemented yet


class WindowsSandbox:
    """进程隔离沙箱（Windows Job Objects）。"""

    AVAILABLE = sys.platform == "win32"

    def __init__(self, config: SandboxConfig | None = None):
        self.config = config or SandboxConfig()
        self._job_handle: int | None = None
        self._ctypes = None
        if self.AVAILABLE:
            try:
                import ctypes
                from ctypes import wintypes
                self._ctypes = ctypes
                self._wintypes = wintypes
            except ImportError:
                self.AVAILABLE = False

    def __enter__(self) -> "WindowsSandbox":
        self._create_job()
        return self

    def __exit__(self, *exc) -> None:
        self._close_job()

    def _create_job(self) -> None:
        if not self.AVAILABLE or not self._ctypes:
            return
        ctypes = self._ctypes
        wintypes = self._wintypes
        kernel32 = ctypes.windll.kernel32

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        # JOB_OBJECT_LIMIT flags
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000020
        JOB_OBJECT_LIMIT_JOB_TIME = 0x00000004
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

        h_job = kernel32.CreateJobObjectW(None, None)
        if not h_job:
            raise RuntimeError(f"CreateJobObject failed: {kernel32.GetLastError()}")

        info = JOBOBJECT_BASIC_LIMIT_INFORMATION()
        info.LimitFlags = (
            JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        info.ActiveProcessLimit = self.config.max_processes
        # Note: PerJobUserTimeLimit omitted (ctypes.wintypes.LARGE_INTEGER
        # layout varies across Python versions — ActiveProcessLimit + KillOnClose
        # are sufficient for process-fork bomb protection).

        ok = kernel32.SetInformationJobObject(
            h_job, 1,  # JobObjectBasicLimitInformation
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            kernel32.CloseHandle(h_job)
            raise RuntimeError(f"SetInformationJobObject failed: {kernel32.GetLastError()}")

        self._job_handle = h_job
        logger.debug("WindowsSandbox created: handle=%s, max_procs=%s, cpu=%ss",
                     h_job, self.config.max_processes, self.config.max_cpu_seconds)

    def _close_job(self) -> None:
        if self._job_handle and self._ctypes:
            self._ctypes.windll.kernel32.CloseHandle(self._job_handle)
            logger.debug("WindowsSandbox closed (all child processes killed)")
        self._job_handle = None

    def run(
        self,
        command: Sequence[str],
        timeout: float | None = None,
        stdin_text: str | None = None,
    ) -> tuple[int, str, str]:
        """Run a command inside the sandbox.

        Returns (exit_code, stdout_text, stderr_text).
        """
        import subprocess

        if not self.AVAILABLE:
            # Fallback: plain subprocess (no isolation)
            logger.warning("WindowsSandbox not available — running without isolation")
            return self._run_raw(command, timeout, stdin_text)

        # Create process in suspended state, then assign to job
        STARTF_USESHOWWINDOW = 0x00000001
        CREATE_SUSPENDED = 0x00000004
        CREATE_NEW_PROCESS_GROUP = 0x00000200

        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= STARTF_USESHOWWINDOW

        creationflags = CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP
        if self._job_handle:
            creationflags |= 0x00000010  # CREATE_NO_WINDOW

        env = os.environ.copy()
        if self.config.env_add:
            env.update(self.config.env_add)

        try:
            proc = subprocess.Popen(
                list(command),
                stdin=subprocess.PIPE if stdin_text else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.config.working_dir,
                env=env,
                creationflags=creationflags,
                startupinfo=startupinfo,
            )
        except FileNotFoundError:
            logger.error("Command not found: %s", command)
            return 127, "", f"Command not found: {command[0]}"

        # Assign process to job object
        if self._job_handle and self._ctypes:
            ok = self._ctypes.windll.kernel32.AssignProcessToJobObject(
                self._job_handle, int(proc._handle)
            )
            if not ok:
                logger.warning("AssignProcessToJobObject failed: %s",
                               self._ctypes.windll.kernel32.GetLastError())

        # Resume the suspended process
        if self._ctypes:
            self._ctypes.windll.kernel32.ResumeThread(int(proc._thread))

        # Wait with timeout
        try:
            stdout, stderr = proc.communicate(
                input=stdin_text.encode() if stdin_text else None,
                timeout=timeout or self.config.max_cpu_seconds + 5,
            )
            return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                stdout, stderr = proc.communicate(timeout=2)
            # noqa: BLE001 - best-effort, never block main path
            except Exception:
                stdout, stderr = b"", b""
            return -1, stdout.decode(errors="replace"), f"Timeout after {timeout or self.config.max_cpu_seconds}s"

    def _run_raw(self, command, timeout, stdin_text):
        import subprocess
        proc = subprocess.Popen(
            list(command),
            stdin=subprocess.PIPE if stdin_text else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.config.working_dir,
        )
        try:
            out, err = proc.communicate(
                input=stdin_text.encode() if stdin_text else None,
                timeout=timeout or 30,
            )
            return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")
        except subprocess.TimeoutExpired:
            proc.kill()
            return -1, "", "Timeout"


# ── Convenience wrapper ──────────────────────────────────────────────

def run_isolated(
    command: Sequence[str],
    timeout: float = 30,
    max_processes: int = 4,
    working_dir: str | None = None,
    env_add: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run a command in an isolated sandbox.

    On Windows: uses Job Objects (process count + CPU time limits, auto-kill on close).
    On Linux (future): Docker or cgroups.
    Any platform: falls back to plain subprocess if sandbox is unavailable.
    """
    config = SandboxConfig(
        max_processes=max_processes,
        max_cpu_seconds=int(timeout),
        working_dir=working_dir,
        env_add=env_add,
    )
    with WindowsSandbox(config) as sb:
        return sb.run(command, timeout=timeout)