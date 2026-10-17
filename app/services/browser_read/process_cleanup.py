"""Linux-only cleanup for exact private environment ownership markers.

This is cooperative trusted-process cleanup, not a hostile-process boundary.
No PID supplied by the Node worker is used as permission to signal a process.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .profile import BrowserReadError

OWNER_ENV = "AGENTHUB_BROWSER_OWNER"


@dataclass(frozen=True, slots=True)
class OwnedProcess:
    pid: int
    group: int
    start_time: str


def _marker(owner: str) -> bytes:
    if str(uuid.UUID(owner)) != owner:
        raise BrowserReadError("browser cleanup requires a canonical private UUID")
    return f"{OWNER_ENV}={owner}".encode("ascii")


def _identity(directory: Path) -> tuple[int, str, str]:
    value = (directory / "stat").read_bytes()
    fields = value[value.rfind(b")") + 2 :].split()
    return int(fields[2]), fields[19].decode("ascii"), fields[0].decode("ascii")


def _matches(directory: Path, marker: bytes) -> bool:
    with (directory / "environ").open("rb") as stream:
        content = stream.read(1_048_577)
    return len(content) <= 1_048_576 and marker in content.split(b"\x00")


def _processes(owner: str, proc_root: Path = Path("/proc")) -> tuple[OwnedProcess, ...]:
    marker, result = _marker(owner), []
    for count, directory in enumerate(proc_root.iterdir(), 1):
        if count > 65_536:
            raise BrowserReadError("browser process inventory exceeds its bound")
        if not directory.name.isdecimal():
            continue
        try:
            if directory.stat().st_uid != os.geteuid():
                continue
            group, started, state = _identity(directory)
            if state not in {"Z", "X"} and _matches(directory, marker):
                result.append(OwnedProcess(int(directory.name), group, started))
        except (FileNotFoundError, ProcessLookupError):
            continue
    return tuple(result)


def owned_processes(owner: str) -> tuple[OwnedProcess, ...]:
    """Content-free inspection, also useful to the explicitly enabled Linux gate."""
    return _processes(owner)


def _same_process(process: OwnedProcess, marker: bytes) -> bool:
    directory = Path("/proc") / str(process.pid)
    try:
        group, started, state = _identity(directory)
        return (
            (group, started) == (process.group, process.start_time)
            and state not in {"Z", "X"}
            and _matches(directory, marker)
        )
    except (FileNotFoundError, ProcessLookupError):
        return False


def _signal_owned(owner: str) -> None:
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise BrowserReadError("browser cleanup requires Linux pidfd support")
    marker = _marker(owner)
    for process in owned_processes(owner):
        try:
            descriptor = os.pidfd_open(process.pid)
        except ProcessLookupError:
            continue
        try:
            if _same_process(process, marker):
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
        except ProcessLookupError:
            continue
        finally:
            os.close(descriptor)


async def cleanup_owned_processes(owner: str, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            await asyncio.to_thread(_signal_owned, owner)
            if not await asyncio.to_thread(owned_processes, owner):
                return True
        except (OSError, ValueError, BrowserReadError):
            return False
        await asyncio.sleep(min(0.025, max(0.0, deadline - time.monotonic())))
    return False


def _consume(task):
    if not task.cancelled():
        task.exception()


async def confirmed_cleanup(owner: str, timeout_seconds: float) -> bool:
    """Repeated caller cancellation cannot cancel the tracked cleanup task."""
    deadline = time.monotonic() + timeout_seconds
    task = asyncio.create_task(cleanup_owned_processes(owner, timeout_seconds))
    task.add_done_callback(_consume)
    interrupted = False
    while not task.done() and time.monotonic() < deadline:
        try:
            await asyncio.wait({task}, timeout=max(0.0, deadline - time.monotonic()))
        except asyncio.CancelledError:
            interrupted = True
    if interrupted:
        raise asyncio.CancelledError
    return task.done() and not task.cancelled() and task.result()
