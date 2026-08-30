"""Windows OS-level sandbox for restricted subprocess execution.

Two hardening layers (subprocess-level only; no system settings/ACL touched):

1. **Restricted token** — the child runs through
   ``win32process.CreateProcessAsUser`` with a first-generation restricted
   derivative of the caller's primary token built by
   ``win32security.CreateRestrictedToken``:

   - *normal side*: the caller's groups minus the administrative builtin
     groups (``BUILTIN\\Administrators`` / Server Operators / Backup
     Operators become use-for-deny-only), i.e. only the user's own SID keeps
     granting access;
   - *privileges*: every privilege of the primary token is **deleted** via
     ``SE_PRIVILEGE_REMOVED`` — strictly stronger than the
     ``DISABLE_MAX_PRIVILEGE`` flag, which only disables privileges and
     exempts ``SeChangeNotifyPrivilege`` (observed: with the flag set the
     privilege survives, without it the child token carries zero privileges);
   - *restricted side*: **probed and not shipped** — on the reference Windows
     host, any restricted-SID list (even the user's own SID alone, or
     ``BUILTIN\\Users``) makes *every* child die at startup with
     ``STATUS_ACCESS_DENIED`` (0xC0000022), independent of the executable
     (base python.exe, cmd.exe and venv-launcher children all reproduce
     it).  The shipped hardening is therefore admin-group stripping plus
     full privilege deletion, both verified working; SID-level isolation
     would require a dedicated low-privileged logon account instead.

   The token must stay a *first-generation* derivative of the caller's
   primary token: ``CreateProcessAsUser`` then accepts it without
   ``SE_ASSIGNPRIMARYTOKEN_NAME``.

2. **Job Object** — the process is created suspended, assigned to a fresh
   job, then resumed.  The job carries
   ``JOB_OBJECT_LIMIT_PROCESS_MEMORY`` (when ``policy.memory_mb`` is set),
   ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` (tree dies even if the parent
   crashes) and an optional CPU rate limit
   (``JOBOBJECT_CPU_RATE_CONTROL_INFORMATION`` via ctypes).  The timeout is
   enforced by polling the process handle and calling
   ``TerminateJobObject`` on expiry (tree-level kill).

**Network** — *documented limitation*: ``SeNetworkLogonRight`` is an LSA
logon right, not a token privilege, so a hard token-level network block is
not expressible without LSA policy edits or a dedicated low-privileged
account.  ``policy.disable_network`` is accepted for interface parity; the
shipped token deletes every privilege (including the network/traverse
``SeChangeNotifyPrivilege``), but socket connections are **not** blocked at
token level on this path.  Callers that need hard network isolation should
use firewall rules or the remote sandbox-service.

Interface (shared with :mod:`app.services.runner.sandbox_unix`):
``run_sandboxed(cmd, cwd, policy) -> subprocess.CompletedProcess`` whose
``stdout``/``stderr`` are decoded text and whose ``returncode`` is ``None``
when the run was terminated by the timeout.  The returned object carries a
dynamic ``sandboxed: bool`` attribute so callers can audit whether the OS
sandbox layer was actually applied.

Fail-open semantics (enhancement layer, unlike the fail-closed
PermissionManager): when the platform layer is unavailable
(non-Windows / pywin32 missing) or the sandbox setup fails (e.g. token
privilege shortfalls), the module logs a warning and degrades to a plain
``subprocess.run`` with ``sandboxed=False``.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("agenthub.runner.sandbox_windows")

_WIN32 = sys.platform == "win32"

if _WIN32:  # pragma: no branch - platform dependent
    try:
        import pywintypes
        import win32api
        import win32con
        import win32event
        import win32file
        import win32job
        import win32pipe
        import win32process
        import win32security

        _PYWIN32_AVAILABLE = True
    except ImportError:  # pragma: no cover - pywin32 missing
        _PYWIN32_AVAILABLE = False
else:
    _PYWIN32_AVAILABLE = False

_SANDBOX_AVAILABLE = _WIN32 and _PYWIN32_AVAILABLE

# Per-stream capture cap: anything beyond is drained but discarded so a
# chatty child can never exhaust parent memory nor deadlock on a full pipe.
MAX_OUTPUT_BYTES_PER_STREAM = 1_000_000

# Sentinel exit code passed to the termination call on timeout.
_JOB_TIMEOUT_EXIT_CODE = 124  # GNU timeout convention

_POLL_INTERVAL_MS = 50
# Bounded post-exit drain window so buffered pipe bytes are collected even
# when the child exited without flushing the pipe bookkeeping promptly.
_FINAL_DRAIN_SECONDS = 2.0
_STILL_ACTIVE = 259  # STILL_ACTIVE from WinBase.h

_SE_PRIVILEGE_REMOVED = 0x4  # LUID_AND_ATTRIBUTES attribute for deletion
_BUILTIN_ADMINISTRATORS_SID = "S-1-5-32-544"
# Administrative builtin groups stripped from the unrestricted token side.
_DISABLED_GROUP_SIDS = {
    _BUILTIN_ADMINISTRATORS_SID,  # BUILTIN\Administrators
    "S-1-5-32-549",  # BUILTIN\Server Operators
    "S-1-5-32-551",  # BUILTIN\Backup Operators
}


@dataclass(frozen=True)
class SandboxPolicy:
    """Resource bounds for one sandboxed run (mirrored by sandbox_unix)."""

    workspace_root: str | Path
    timeout_seconds: float = 60.0
    # None → no Job Object memory limit.
    memory_mb: int | None = None
    # Optional CPU rate limit for the whole job, in percent (1-100).
    cpu_rate: int | None = None
    # Accepted for interface parity; see the module docstring for why a hard
    # network block is not expressible at token level.
    disable_network: bool = True


def run_sandboxed(
    cmd: list[str] | str, cwd: str | Path, policy: SandboxPolicy
) -> subprocess.CompletedProcess:
    """Run *cmd* under the OS sandbox and return a ``CompletedProcess``.

    *cmd* is either an argv list (joined with the MSVCRT ``list2cmdline``
    quoting rules) or a raw command-line string used verbatim — the string
    form exists for ``cmd.exe /c`` wrappers, whose quote handling differs
    from MSVCRT (``cmd`` does not honor ``\\"``).  ``returncode`` is ``None``
    when the run hit ``policy.timeout_seconds`` and the whole job tree was
    terminated.  The returned object carries ``sandboxed: bool`` — ``True``
    only when the restricted-token + Job Object layer was applied.
    """
    if not _SANDBOX_AVAILABLE:
        logger.warning(
            "Windows sandbox unavailable (sys.platform=%r, pywin32=%r); "
            "degrading to plain subprocess (fail-open, sandboxed=False)",
            sys.platform,
            _PYWIN32_AVAILABLE,
        )
        return _run_plain(cmd, cwd, policy)
    return _run_restricted(cmd, cwd, policy)


def _as_command_line(cmd: list[str] | str) -> str:
    """Normalize *cmd* to a Windows command line string."""
    if isinstance(cmd, str):
        return cmd
    return subprocess.list2cmdline([str(part) for part in cmd])


# ── native Windows path ──────────────────────────────────────────────────


def _string_sid(sid) -> str:
    return win32security.ConvertSidToStringSid(sid)


def _create_restricted_token():
    """Build the first-generation restricted token described in the module doc.

    Normal side keeps the user's own SID (administrative builtin groups are
    disabled) and every privilege is deleted.  The restricted-SID side is
    deliberately left empty: probed on the reference host, any restricted
    list crashes every child at startup with ``STATUS_ACCESS_DENIED``
    (0xC0000022) — see the module docstring.
    """
    process_handle = win32api.GetCurrentProcess()
    base_token = win32security.OpenProcessToken(
        process_handle, win32con.TOKEN_ALL_ACCESS
    )
    groups = win32security.GetTokenInformation(
        base_token, win32security.TokenGroups
    )
    disable_sids = [
        (sid, 0)
        for sid, _attrs in groups
        if _string_sid(sid) in _DISABLED_GROUP_SIDS
    ]
    privileges = win32security.GetTokenInformation(
        base_token, win32security.TokenPrivileges
    )
    delete_privileges = [
        (luid, _SE_PRIVILEGE_REMOVED) for luid, _attrs in privileges
    ]
    return win32security.CreateRestrictedToken(
        base_token, 0, disable_sids, delete_privileges, []
    )


def _configure_job(job, policy: SandboxPolicy) -> None:
    """Apply kill-on-close / optional memory / optional CPU rate limits."""
    info = win32job.QueryInformationJobObject(
        job, win32job.JobObjectExtendedLimitInformation
    )
    limit_flags = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if policy.memory_mb is not None:
        limit_flags |= win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY
        info["ProcessMemoryLimit"] = int(policy.memory_mb) * 1024 * 1024
    info["BasicLimitInformation"]["LimitFlags"] |= limit_flags
    win32job.SetInformationJobObject(
        job, win32job.JobObjectExtendedLimitInformation, info
    )
    if policy.cpu_rate is not None:
        _set_cpu_rate_limit(job, policy.cpu_rate)


def _set_cpu_rate_limit(job, cpu_rate_percent: int) -> None:
    """Best-effort CPU rate limit via ctypes (pywin32 lacks the info class)."""
    import ctypes
    from ctypes import wintypes

    class JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("ControlFlags", wintypes.DWORD),
            ("CpuRate", wintypes.WORD),
        ]

    JOB_OBJECT_CPU_RATE_CONTROL_INFORMATION = 21
    JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x1
    JOB_OBJECT_CPU_RATE_CONTROL_HARD_LIMIT = 0x2

    rate = min(max(int(cpu_rate_percent), 1), 100) * 100
    control = JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(
        ControlFlags=(
            JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_LIMIT
        ),
        CpuRate=rate,
    )
    set_information = ctypes.windll.kernel32.SetInformationJobObject
    set_information.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    if not set_information(
        int(job),
        JOB_OBJECT_CPU_RATE_CONTROL_INFORMATION,
        ctypes.byref(control),
        ctypes.sizeof(control),
    ):
        raise OSError(f"SetInformationJobObject(CpuRateControl) failed: rate={rate}")


def _degrade(
    reason: str,
    exc: Exception,
    cmd: list[str] | str,
    cwd: str | Path,
    policy: SandboxPolicy,
) -> subprocess.CompletedProcess:
    """Fail-open audit log + plain subprocess fallback (command not started yet)."""
    logger.warning(
        "Windows sandbox setup failed (%s: %s); degrading to plain "
        "subprocess (fail-open, sandboxed=False)",
        reason,
        exc,
    )
    return _run_plain(cmd, cwd, policy)


def _run_restricted(
    cmd: list[str] | str, cwd: str | Path, policy: SandboxPolicy
) -> subprocess.CompletedProcess:
    command_line = _as_command_line(cmd)

    try:
        token = _create_restricted_token()
    except pywintypes.error as exc:
        return _degrade("CreateRestrictedToken", exc, cmd, cwd, policy)

    job = win32job.CreateJobObject(None, "")
    try:
        _configure_job(job, policy)
    except (pywintypes.error, OSError, RuntimeError) as exc:
        job.Close()
        token.Close()
        return _degrade("job configuration", exc, cmd, cwd, policy)

    # Inheritable pipes for stdin/stdout/stderr.
    sa = pywintypes.SECURITY_ATTRIBUTES()
    sa.bInheritHandle = True
    stdin_read, stdin_write = win32pipe.CreatePipe(sa, 0)
    stdout_read, stdout_write = win32pipe.CreatePipe(sa, 0)
    stderr_read, stderr_write = win32pipe.CreatePipe(sa, 0)

    startup = win32process.STARTUPINFO()
    startup.dwFlags = win32con.STARTF_USESTDHANDLES
    startup.hStdInput = stdin_read
    startup.hStdOutput = stdout_write
    startup.hStdError = stderr_write

    try:
        process, thread, _pid, _tid = win32process.CreateProcessAsUser(
            token,
            None,
            command_line,
            None,
            None,
            True,  # bInheritHandles
            win32con.CREATE_SUSPENDED,
            None,
            str(cwd),
            startup,
        )
    except pywintypes.error as exc:
        for handle in (
            stdin_read,
            stdin_write,
            stdout_read,
            stdout_write,
            stderr_read,
            stderr_write,
        ):
            handle.Close()
        job.Close()
        token.Close()
        return _degrade("CreateProcessAsUser", exc, cmd, cwd, policy)
    token.Close()  # the child owns its derived token now

    job_assigned = True
    try:
        # Assign before resume: the job then owns every descendant.
        win32job.AssignProcessToJobObject(job, process)
        win32process.ResumeThread(thread)
    except pywintypes.error as exc:
        # Post-spawn failure: the child is already running — fail open
        # without job limits instead of losing the run.
        job_assigned = False
        logger.warning(
            "sandbox job assignment failed (%s); continuing without job "
            "limits (sandboxed=False)",
            exc,
        )
    finally:
        # Parent copies of the child-side ends must go so EOF propagates;
        # stdin write closes immediately (child sees EOF).
        stdin_read.Close()
        stdin_write.Close()
        stdout_write.Close()
        stderr_write.Close()

    timed_out = False
    stdout_buf = bytearray()
    stderr_buf = bytearray()
    try:
        deadline = time.monotonic() + max(float(policy.timeout_seconds), 0.0)
        while True:
            _drain_pipe(stdout_read, stdout_buf)
            _drain_pipe(stderr_read, stderr_buf)
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                timed_out = True
                break
            status = win32event.WaitForMultipleObjects(
                [process], False, min(_POLL_INTERVAL_MS, remaining_ms)
            )
            if status == win32event.WAIT_OBJECT_0:
                break
        if timed_out:
            if job_assigned:
                win32job.TerminateJobObject(job, _JOB_TIMEOUT_EXIT_CODE)
            else:
                win32process.TerminateProcess(process, _JOB_TIMEOUT_EXIT_CODE)
            # Reap so the exit code below is meaningful afterwards.
            win32event.WaitForSingleObject(process, 10_000)
        # Final bounded drain: wait until every pipe writer closed (EOF) —
        # the direct process may have exited while a re-exec'd child (e.g.
        # a venv launcher) still holds the inherited stdout/stderr handles —
        # or until the drain window expires, so the parent never returns
        # while the tree still writes output (or holds its cwd handle).
        drain_deadline = time.monotonic() + _FINAL_DRAIN_SECONDS
        while True:
            out_bytes = _drain_pipe(stdout_read, stdout_buf)
            err_bytes = _drain_pipe(stderr_read, stderr_buf)
            if _pipe_writers_closed(stdout_read) and _pipe_writers_closed(
                stderr_read
            ):
                break
            if time.monotonic() >= drain_deadline:
                break
            if not out_bytes and not err_bytes:
                time.sleep(0.01)
        exit_code = win32process.GetExitCodeProcess(process)
    finally:
        thread.Close()
        process.Close()
        stdout_read.Close()
        stderr_read.Close()
        job.Close()  # KILL_ON_JOB_CLOSE: any survivor dies here

    returncode: int | None
    if timed_out:
        returncode = None
    elif exit_code == _STILL_ACTIVE:  # defensive: reap failed
        returncode = _JOB_TIMEOUT_EXIT_CODE
    else:
        returncode = exit_code
    completed = subprocess.CompletedProcess(
        args=cmd,
        returncode=returncode,
        stdout=stdout_buf.decode("utf-8", errors="replace"),
        stderr=stderr_buf.decode("utf-8", errors="replace"),
    )
    completed.sandboxed = job_assigned
    return completed


def _drain_pipe(handle, buffer: bytearray) -> int:
    """Read one non-blocking chunk from *handle* into *buffer*; return bytes read.

    Beyond ``MAX_OUTPUT_BYTES_PER_STREAM`` the bytes are still consumed but
    discarded, so the child never blocks on a full pipe and the parent cap
    holds.
    """
    try:
        _data, available, _left = win32pipe.PeekNamedPipe(handle, 0)
    except pywintypes.error:
        return 0  # broken pipe or closed handle
    if not available:
        return 0
    to_read = min(int(available), 65_536)
    try:
        _hr, data = win32file.ReadFile(handle, to_read)
    except pywintypes.error:
        return 0
    if len(buffer) < MAX_OUTPUT_BYTES_PER_STREAM:
        buffer.extend(data[: MAX_OUTPUT_BYTES_PER_STREAM - len(buffer)])
    return len(data)


def _pipe_writers_closed(handle) -> bool:
    """True when every write end of *handle* is closed (EOF readable)."""
    try:
        win32pipe.PeekNamedPipe(handle, 0)
    except pywintypes.error:
        return True
    return False


# ── degrade path (fail-open, audited) ────────────────────────────────────


def _decode(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


def _run_plain(
    cmd: list[str] | str, cwd: str | Path, policy: SandboxPolicy
) -> subprocess.CompletedProcess:
    """Interface-preserving fallback without any OS sandbox guarantees."""
    # Lists keep argv form (subprocess applies MSVCRT quoting on Windows and
    # execv-style args on POSIX); raw strings are used verbatim (Windows).
    run_arg: str | list[str] = (
        cmd if isinstance(cmd, str) else [str(part) for part in cmd]
    )
    try:
        finished = subprocess.run(
            run_arg,
            cwd=str(cwd),
            capture_output=True,
            timeout=policy.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        completed = subprocess.CompletedProcess(
            args=cmd,
            returncode=None,
            stdout=_decode(exc.stdout),
            stderr=_decode(exc.stderr),
        )
    else:
        completed = subprocess.CompletedProcess(
            args=cmd,
            returncode=finished.returncode,
            stdout=_decode(finished.stdout),
            stderr=_decode(finished.stderr),
        )
    completed.sandboxed = False
    return completed
