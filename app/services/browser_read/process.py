"""Linux browser process capture; cleanup uses verified pidfds, never killpg."""

from __future__ import annotations

import asyncio
import os
import subprocess
import time

from app.services.trusted_tests.process import ProcessResult, _within_budget

from .process_cleanup import OWNER_ENV, confirmed_cleanup
from .profile import BrowserReadError


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
                limited = (
                    limited
                    or _capture(process.stdout, output, max_output_bytes)
                    or not _within_budget(monitored_root, max_workdir_bytes)
                )
            finally:
                process.stdout.close()
                process.poll()
        if cancelled:
            raise asyncio.CancelledError
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
            limited = _capture(process.stdout, output, maximum) or not _within_budget(
                root, workdir_maximum
            )
            timed_out = time.monotonic() - started >= timeout
            cancelled = cancel_event is not None and cancel_event.is_set()
            if limited or timed_out or cancelled:
                break
            await asyncio.sleep(0.025)
    except asyncio.CancelledError:
        cancelled = True
    return timed_out, limited, cancelled
