"""POSIX OS-level sandbox for restricted subprocess execution.

Unified interface with :mod:`app.services.runner.sandbox_windows`:
``run_sandboxed(cmd, cwd, policy) -> subprocess.CompletedProcess`` whose
``stdout``/``stderr`` are decoded text and whose ``returncode`` is ``None``
when the run was terminated by the timeout.  The returned object carries a
dynamic ``sandboxed: bool`` attribute so callers can audit whether the OS
sandbox layer was actually applied.

Coverage matrix:

- **Linux + bwrap available** — the command runs inside a bubblewrap
  sandbox: ``--ro-bind / /`` (root read-only), ``--bind <workspace_root>
  <workspace_root>`` (only the workspace stays writable), ``--dev /dev``,
  ``--proc /proc``, ``--unshare-net`` (hard network denial, applied when
  ``policy.disable_network``), ``--die-with-parent``, ``--new-session``.
  ``memory_mb``/``cpu_rate`` are accepted but *not* enforced by bwrap alone
  (kernel cgroup limits would need an external supervisor) — a documented
  gap of this path.
- **Linux without bwrap** — degrade to a plain subprocess with a warning;
  ``sandboxed`` is ``False``.
- **macOS** — degrades directly: ``sandbox-exec`` is deprecated/unsupported,
  so only the plain-subprocess fallback is offered.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("agenthub.runner.sandbox_unix")


@dataclass(frozen=True)
class SandboxPolicy:
    """Resource bounds for one sandboxed run (mirrored by sandbox_windows)."""

    workspace_root: str | Path
    timeout_seconds: float = 60.0
    # Accepted; enforced only by the Windows path / cgroups on this path.
    memory_mb: int | None = None
    # Accepted; not enforced on this path.
    cpu_rate: int | None = None
    # False → the bwrap run keeps the host network namespace.
    disable_network: bool = True


def _bwrap_available() -> bool:
    """True when the bubblewrap binary is on PATH (Linux decided by caller)."""
    return shutil.which("bwrap") is not None


def run_sandboxed(
    cmd: list[str], cwd: str | Path, policy: SandboxPolicy
) -> subprocess.CompletedProcess:
    """Run *cmd* under the platform sandbox or the degraded fallback."""
    if sys.platform.startswith("linux") and _bwrap_available():
        return _run_bwrap(cmd, cwd, policy)
    logger.warning(
        "POSIX sandbox unavailable (sys.platform=%r, bwrap=%s); degrading to "
        "plain subprocess (fail-open, sandboxed=False)",
        sys.platform,
        "yes" if _bwrap_available() else "no",
    )
    return _run_plain(cmd, cwd, policy)


def _bwrap_argv(cmd: list[str], policy: SandboxPolicy) -> list[str]:
    argv = [str(part) for part in cmd]
    network_flags = ["--unshare-net"] if policy.disable_network else []
    return [
        "bwrap",
        "--ro-bind", "/", "/",
        "--bind", str(policy.workspace_root), str(policy.workspace_root),
        "--dev", "/dev",
        "--proc", "/proc",
        *network_flags,
        "--die-with-parent",
        "--new-session",
        "--",
        *argv,
    ]


def _run_bwrap(
    cmd: list[str], cwd: str | Path, policy: SandboxPolicy
) -> subprocess.CompletedProcess:
    completed = _run_plain(_bwrap_argv(cmd, policy), cwd, policy)
    completed.args = list(cmd)
    completed.sandboxed = True
    return completed


def _run_plain(
    cmd: list[str], cwd: str | Path, policy: SandboxPolicy
) -> subprocess.CompletedProcess:
    """Interface-preserving fallback without any OS sandbox guarantees."""
    argv = [str(part) for part in cmd]
    try:
        finished = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            timeout=policy.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        completed = subprocess.CompletedProcess(
            args=list(argv),
            returncode=None,
            stdout=_decode(exc.stdout),
            stderr=_decode(exc.stderr),
        )
    else:
        completed = subprocess.CompletedProcess(
            args=list(argv),
            returncode=finished.returncode,
            stdout=_decode(finished.stdout),
            stderr=_decode(finished.stderr),
        )
    completed.sandboxed = False
    return completed


def _decode(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)
