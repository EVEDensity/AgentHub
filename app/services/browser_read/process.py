"""Linux browser process capture; cleanup uses verified pidfds, never killpg."""

from __future__ import annotations

import asyncio
import logging
import os
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from app.services.trusted_tests.process import ProcessResult

from .process_cleanup import OWNER_ENV, confirmed_cleanup
from .profile import BrowserReadError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkdirBudget:
    reason: str | None
    entries: int
    bytes: int


def _walk_error(error):
    raise error


def inspect_workdir(root: Path, maximum: int) -> WorkdirBudget:
    """Count links themselves without following Chromium Singleton* targets."""
    entries = total = 0
    try:
        root.lstat()
        for directory, children, files in os.walk(
            root, followlinks=False, onerror=_walk_error
        ):
            for name in (*children, *files):
                try:
                    metadata = (Path(directory) / name).lstat()
                except FileNotFoundError:
                    continue
                entries += 1
                if not stat.S_ISDIR(metadata.st_mode):
                    total += metadata.st_size
                if entries > 5000:
                    return WorkdirBudget("workdir_entries", entries, total)
                if total > maximum:
                    return WorkdirBudget("workdir_bytes", entries, total)
    except OSError:
        return WorkdirBudget("workdir_unreadable", entries, total)
    return WorkdirBudget(None, entries, total)


def _limited(process, output, maximum, root, workdir_maximum):
    exceeded = _capture(process.stdout, output, maximum)
    budget = inspect_workdir(root, workdir_maximum)
    if exceeded or budget.reason is not None:
        logger.warning(
            "browser_process_budget output_limited=%s workdir_reason=%s entries=%d bytes=%d",
            exceeded,
            budget.reason or "within_budget",
            budget.entries,
            budget.bytes,
        )
    return exceeded or budget.reason is not None


def _capture(stream, output: bytearray, maximum: int) -> bool:
    exceeded = False
    for _index in range(128):
        try:
            value = os.read(stream.fileno(), 8192)
        except BlockingIOError:
            break
        if not value:
            break
        room = max(0, maximum - len(output))
        output.extend(value[:room])
        exceeded = exceeded or len(value) > room
    return exceeded


class LinuxBrowserProcessBackend:
    """Synchronous Popen admission has no cancellation gap or detached late spawn.

    Polling filesystem/output budgets are detected limits, not OS resource quotas.
    Process creation itself is an OS call and cannot be preempted by asyncio.
    """

    async def run(
        self,
        argv,
        *,
        cwd,
        environment,
        timeout_seconds,
        max_output_bytes,
        monitored_root,
        max_workdir_bytes,
        cancel_event=None,
    ):
        owner = environment.get(OWNER_ENV)
        if not owner:
            raise BrowserReadError(
                "browser process requires a private ownership marker"
            )
        started = time.monotonic()
        process = _admit(argv, cwd, environment)
        output = bytearray()
        os.set_blocking(process.stdout.fileno(), False)
        timed_out = limited = cancelled = False
        try:
            timed_out, limited, cancelled = await _poll(
                process,
                output,
                started,
                timeout_seconds,
                max_output_bytes,
                monitored_root,
                max_workdir_bytes,
                cancel_event,
            )
        finally:
            try:
                confirmed = await confirmed_cleanup(owner, 5.0)
                limited = limited or _limited(
                    process, output, max_output_bytes, monitored_root, max_workdir_bytes
                )
            finally:
                process.stdout.close()
                process.poll()
        if cancelled:
            raise asyncio.CancelledError
        if limited or timed_out or not confirmed:
            logger.warning(
                "browser_process_result timed_out=%s limited=%s cleanup_confirmed=%s exit_code=%s",
                timed_out,
                limited,
                confirmed,
                process.returncode,
            )
        return ProcessResult(
            process.returncode,
            bytes(output),
            timed_out,
            False,
            limited or not confirmed,
            time.monotonic() - started,
        )


def _admit(argv, cwd, environment):
    failed = False
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError:
        failed = True
    if failed:
        raise BrowserReadError("browser process could not start")
    return process


async def _poll(
    process, output, started, timeout, maximum, root, workdir_maximum, cancel_event
):
    timed_out = limited = cancelled = False
    try:
        while process.poll() is None:
            limited = _limited(process, output, maximum, root, workdir_maximum)
            timed_out = time.monotonic() - started >= timeout
            cancelled = cancel_event is not None and cancel_event.is_set()
            if limited or timed_out or cancelled:
                break
            await asyncio.sleep(0.025)
    except asyncio.CancelledError:
        cancelled = True
    return timed_out, limited, cancelled
