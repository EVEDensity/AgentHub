"""Replaceable test-process port with bounded trusted-local execution.

The local backend is not a hostile-code sandbox. Same-user processes may access
host files; a production backend needs separate identities and filesystem/network
isolation. No Runner token or signing key is passed to the test environment.
"""
from __future__ import annotations

import asyncio
import os
import signal
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .windows_job import attach_job


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int | None
    output: bytes
    timed_out: bool
    cancelled: bool
    limit_exceeded: bool
    elapsed_seconds: float


class TestProcessBackend(Protocol):
    async def run(self, argv: tuple[str, ...], *, cwd: Path, environment: dict[str, str],
                  timeout_seconds: float, max_output_bytes: int, monitored_root: Path,
                  max_workdir_bytes: int, cancel_event: asyncio.Event | None = None) -> ProcessResult: ...


def _kill_posix_group(group_id: int) -> None:
    try:
        os.killpg(group_id, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _close_owned_group(process: subprocess.Popen, job) -> None:
    if job is not None:
        job.close()
    elif os.name != 'nt':
        # start_new_session=True made this PID our owned process-group ID.
        # Descendants can outlive a normally exited root without holding stdout.
        _kill_posix_group(process.pid)


def _kill_tree(process: subprocess.Popen, job=None) -> None:
    if job is not None:
        job.terminate()
    elif os.name == 'nt':
        executable = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/taskkill.exe'
        subprocess.run([str(executable), '/PID', str(process.pid), '/T', '/F'],
                       capture_output=True, timeout=10, check=False)
    else:
        _kill_posix_group(process.pid)
    if process.poll() is None:
        process.kill()


def _within_budget(root: Path, maximum: int) -> bool:
    total, count = 0, 0
    for directory, children, files in os.walk(root, followlinks=False):
        for name in (*children, *files):
            try:
                metadata = (Path(directory) / name).lstat()
            except FileNotFoundError:
                continue
            count += 1
            if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400:
                return False
            total += metadata.st_size if stat.S_ISREG(metadata.st_mode) else 0
            if count > 5000 or total > maximum:
                return False
    return True


def _read_output(stream, maximum: int, output: bytearray, exceeded: threading.Event) -> None:
    try:
        while chunk := stream.read(8192):
            remaining = max(0, maximum - len(output))
            output.extend(chunk[:remaining])
            if len(chunk) > remaining:
                exceeded.set()
    finally:
        stream.close()


def _drain_reader(process, job, reader: threading.Thread) -> bool:
    # A surviving child holding stdout cannot keep the producer waiting forever.
    reader.join(timeout=1)
    if not reader.is_alive():
        return False
    _kill_tree(process, job)
    reader.join(timeout=2)
    return True


def _run_local(argv, cwd, environment, timeout_seconds, max_output_bytes,
               monitored_root, max_workdir_bytes, cancel) -> ProcessResult:
    started = time.monotonic()
    options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    process = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **options)
    try:
        job = attach_job(process.pid)
    except BaseException:
        _kill_tree(process)
        process.wait(timeout=10)
        raise
    output, exceeded = bytearray(), threading.Event()
    reader = threading.Thread(target=_read_output, args=(process.stdout, max_output_bytes, output, exceeded), daemon=True)
    reader.start()
    timed_out = limited = False
    try:
        while process.poll() is None:
            timed_out = time.monotonic() - started >= timeout_seconds
            limited = exceeded.is_set() or not _within_budget(monitored_root, max_workdir_bytes)
            if timed_out or limited or cancel.is_set():
                _kill_tree(process, job)
                break
            time.sleep(0.025)
        process.wait(timeout=10)
        timed_out = timed_out or time.monotonic() - started >= timeout_seconds
        limited = limited or not _within_budget(monitored_root, max_workdir_bytes)
        limited = any((limited, _drain_reader(process, job, reader)))
        return ProcessResult(process.returncode, bytes(output), timed_out, cancel.is_set(),
                             limited or exceeded.is_set(), time.monotonic() - started)
    finally:
        if process.poll() is None:
            _kill_tree(process, job)
            process.wait(timeout=10)
        _close_owned_group(process, job)


class TrustedLocalProcessBackend:
    """Explicit opt-in, bounded local process execution; no strong isolation claim."""
    async def run(self, argv: tuple[str, ...], *, cwd: Path, environment: dict[str, str],
                  timeout_seconds: float, max_output_bytes: int, monitored_root: Path,
                  max_workdir_bytes: int, cancel_event: asyncio.Event | None = None) -> ProcessResult:
        cancel = threading.Event()
        worker = asyncio.create_task(asyncio.to_thread(_run_local, argv, cwd, environment,
                                     timeout_seconds, max_output_bytes, monitored_root, max_workdir_bytes, cancel))
        try:
            while not worker.done():
                if cancel_event is not None and cancel_event.is_set():
                    cancel.set()
                await asyncio.wait({worker}, timeout=0.025)
            return await worker
        except asyncio.CancelledError:
            cancel.set()
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
            worker.result()
            raise
