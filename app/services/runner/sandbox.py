"""Platform sandbox facade for the desktop local runner.

``get_sandbox_runner()`` returns the platform module exposing the unified
``run_sandboxed(cmd, cwd, policy) -> subprocess.CompletedProcess`` interface
(with a dynamic ``sandboxed: bool`` attribute on the result):

- Windows → :mod:`app.services.runner.sandbox_windows`
  (Job Object + restricted token);
- Linux/macOS → :mod:`app.services.runner.sandbox_unix`
  (bwrap where available, plain-subprocess degrade otherwise — each module
  documents its own coverage limits).

The OS sandbox is enabled by default; set
``AGENTHUB_DESKTOP_LOCAL_RUNNER_SANDBOX=0`` to fall back to the original
plain subprocess execution.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping
from typing import Any

from app.services.runner.settings import SANDBOX_ENV

# Module protocol shared by sandbox_windows / sandbox_unix.
SandboxRunnerModule = Any


def sandbox_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Resolve the sandbox switch: default on, ``0`` disables (plain subprocess)."""
    environment = os.environ if env is None else env
    return environment.get(SANDBOX_ENV, "1").strip() == "1"


def get_sandbox_runner() -> SandboxRunnerModule:
    """Return the platform sandbox module (imports it lazily)."""
    if sys.platform == "win32":
        from app.services.runner import sandbox_windows

        return sandbox_windows
    from app.services.runner import sandbox_unix

    return sandbox_unix


def build_sandbox_policy(
    workspace_root: str | Any,
    timeout_seconds: float,
    *,
    memory_mb: int | None = None,
    cpu_rate: int | None = None,
    disable_network: bool = True,
) -> Any:
    """Build the platform ``SandboxPolicy`` for one command run."""
    module = get_sandbox_runner()
    return module.SandboxPolicy(
        workspace_root=workspace_root,
        timeout_seconds=timeout_seconds,
        memory_mb=memory_mb,
        cpu_rate=cpu_rate,
        disable_network=disable_network,
    )


def run_sandboxed(
    cmd: list[str] | str, cwd: str | Any, policy: Any
) -> subprocess.CompletedProcess:
    """Dispatch one sandboxed run through the platform module.

    ``cmd`` is an argv list, or a raw command-line string (Windows ``cmd /c``
    wrappers; see ``sandbox_windows.run_sandboxed``).
    """
    return get_sandbox_runner().run_sandboxed(cmd, cwd, policy)
