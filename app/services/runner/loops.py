"""Derivation and unattended-verification loop helpers (split module).

- derivation: exactly one ``desktop.task`` WorkUnit per RUNNING manual Mission;
- verification: ``VERIFY:`` / ``RUN:`` acceptance-command extraction and
  workspace execution used by the controller's unattended verifier loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.domain import (
    ActorRef,
    ActorType,
    MissionSourceType,
    MissionStatus,
    OutputSpec,
)
from app.repositories import MissionRepository
from app.services.mission_service import (
    DESKTOP_TASK_WORK_UNIT_KIND,
    MissionService,
)
from app.services.runner.settings import (
    DESKTOP_ADAPTER_TYPE,
    DESKTOP_AGENT_ID,
    DESKTOP_RUNNER_LABEL,
    RUN_COMMAND_MARKER,
    VERIFY_COMMAND_MARKER,
    VERIFY_COMMAND_OUTPUT_TAIL_CHARS,
)

logger = logging.getLogger("agenthub.desktop_local_runner")

# ── Mission → WorkUnit derivation ────────────────────────────────────────


class DesktopMissionSourcePort(Protocol):
    """The durable Mission projections the derivation needs."""

    async def running_manual_missions(self, workspace_id: str) -> Sequence[Any]: ...

    async def has_work_unit_kind(self, mission_id: str, kind: str) -> bool: ...

    async def create_desktop_task_work_unit(self, mission_id: str) -> str: ...


class MissionControlDesktopMissionSource:
    """Derivation adapter over the in-process Mission repository."""

    def __init__(self, repository_factory: Any = MissionRepository) -> None:
        self._repository_factory = repository_factory

    async def running_manual_missions(self, workspace_id: str) -> Sequence[Any]:
        repository = self._repository_factory()
        missions = await repository.list_missions(workspace_id, limit=200)
        # Accept both MANUAL (CLI/desktop runner) and CHAT (web chat)
        # sources — both produce RUNNING Missions that need work unit
        # derivation.  A2A and API sources have their own derivation path.
        accepted_sources = {MissionSourceType.MANUAL, MissionSourceType.CHAT}
        return [
            mission
            for mission in missions
            if mission.status == MissionStatus.RUNNING
            and mission.source.type in accepted_sources
        ]

    async def has_work_unit_kind(self, mission_id: str, kind: str) -> bool:
        repository = self._repository_factory()
        work_units = await repository.list_work_units(mission_id)
        return any(unit.kind == kind for unit in work_units)

    async def create_desktop_task_work_unit(self, mission_id: str) -> str:
        repository = self._repository_factory()
        service = MissionService(repository)
        work_unit = await service.create_work_unit(
            mission_id,
            work_unit_id=None,
            kind=DESKTOP_TASK_WORK_UNIT_KIND,
            dependencies=[],
            input_refs=[],
            expected_outputs=[OutputSpec(kind="text", required=False)],
            required_capabilities=[],
            assigned_adapter=DESKTOP_ADAPTER_TYPE,
            actor=ActorRef(
                type=ActorType.SERVICE,
                id=DESKTOP_RUNNER_LABEL,
                display_name="Desktop Local Runner",
            ),
            assigned_agent_id=DESKTOP_AGENT_ID,
        )
        return work_unit.id


async def derive_desktop_task_work_units(
    mission_source: DesktopMissionSourcePort,
    *,
    workspace_id: str,
) -> list[str]:
    """Create exactly one ``desktop.task`` WorkUnit per eligible Mission.

    A Mission is eligible when it is RUNNING, desktop-created (manual
    source) and has no ``desktop.task`` WorkUnit yet — including failed
    ones, so derivation never retries doomed Missions on its own.
    """
    derived: list[str] = []
    for mission in await mission_source.running_manual_missions(workspace_id):
        if await mission_source.has_work_unit_kind(
            str(mission.id), DESKTOP_TASK_WORK_UNIT_KIND
        ):
            continue
        work_unit_id = await mission_source.create_desktop_task_work_unit(
            str(mission.id)
        )
        logger.info(
            "desktop runner derived WorkUnit %s for Mission %s",
            work_unit_id,
            mission.id,
        )
        derived.append(work_unit_id)
    return derived


# ── Unattended verification ──────────────────────────────────────────────


def extract_verify_commands(objective: str) -> tuple[str, ...]:
    """Return the acceptance commands declared as ``VERIFY: <command>`` lines.

    Any objective line starting with the marker (leading whitespace allowed)
    declares one workspace command; empty commands are ignored.
    """
    return _extract_marker_commands(objective, VERIFY_COMMAND_MARKER)


def extract_run_commands(objective: str) -> tuple[str, ...]:
    """Return the shell commands declared as ``RUN: <command>`` lines (P1-3).

    Declared commands are executed by the unattended verifier during
    acceptance only; the ``command_execute`` tool never runs shell directly.
    """
    return _extract_marker_commands(objective, RUN_COMMAND_MARKER)


def _extract_marker_commands(objective: str, marker: str) -> tuple[str, ...]:
    commands: list[str] = []
    for line in objective.splitlines():
        stripped = line.strip()
        if not stripped.startswith(marker):
            continue
        command = stripped[len(marker) :].strip()
        if command:
            commands.append(command)
    return tuple(commands)


@dataclass(frozen=True)
class VerifyCommandOutcome:
    """Result of one acceptance command run in the workspace."""

    command: str
    exit_code: int | None
    output: str
    timed_out: bool


async def run_verify_command(
    command: str,
    *,
    cwd: Path,
    timeout_seconds: float,
    sandbox_enabled: bool | None = None,
) -> VerifyCommandOutcome:
    """Run one acceptance command in the workspace, capturing merged output.

    The command inherits the runner process environment; on timeout the whole
    process tree is killed and the outcome is reported as a non-zero result
    with whatever output was produced so far.

    ``sandbox_enabled`` opts the run into the OS-level sandbox facade
    (Job Object + restricted token on Windows, bwrap on Linux); ``None``
    resolves the default switch (on, ``AGENTHUB_DESKTOP_LOCAL_RUNNER_SANDBOX=0``
    disables).  ``False`` keeps the original plain-subprocess execution.
    """
    if _sandbox_active(sandbox_enabled):
        # The sandbox runner is a blocking synchronous API; keep the event
        # loop free by running it in the default executor.
        return await asyncio.get_running_loop().run_in_executor(
            None,
            _run_verify_command_sandboxed,
            command,
            cwd,
            timeout_seconds,
        )
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except NotImplementedError:
        # Event loops without subprocess support (e.g. the Windows selector
        # loop used by some uvicorn configs) fall back to a threaded sync run
        # so the verification loop never blocks.
        return await asyncio.get_running_loop().run_in_executor(
            None,
            _run_verify_command_sync,
            command,
            cwd,
            timeout_seconds,
        )
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        await _kill_process_tree(process)
        try:
            stdout, _ = await process.communicate()
        except ProcessLookupError:
            stdout = b""
        return VerifyCommandOutcome(
            command=command,
            exit_code=None,
            output=(stdout or b"").decode("utf-8", errors="replace"),
            timed_out=True,
        )
    return VerifyCommandOutcome(
        command=command,
        exit_code=process.returncode,
        output=stdout.decode("utf-8", errors="replace"),
        timed_out=False,
    )


def _run_verify_command_sync(
    command: str,
    cwd: Path,
    timeout_seconds: float,
) -> VerifyCommandOutcome:
    import subprocess

    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or b""
        return VerifyCommandOutcome(
            command=command,
            exit_code=None,
            output=output.decode("utf-8", errors="replace"),
            timed_out=True,
        )
    return VerifyCommandOutcome(
        command=command,
        exit_code=completed.returncode,
        output=(completed.stdout or b"").decode("utf-8", errors="replace"),
        timed_out=False,
    )


async def _kill_process_tree(process: asyncio.subprocess.Process) -> None:
    """Terminate the verify command; on Windows kill the whole tree first.

    ``Process.kill()`` only terminates the shell, leaving grandchildren alive
    while they still hold the output pipe; ``taskkill /T`` takes the tree
    down so ``communicate()`` can finish promptly.
    """
    if sys.platform == "win32" and process.returncode is None:
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/F",
            "/T",
            "/PID",
            str(process.pid),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(killer.wait(), timeout=10)
            return
        except TimeoutError:
            pass
    process.kill()


def _sandbox_active(flag: bool | None) -> bool:
    """Resolve the OS-sandbox switch: explicit flag wins, else the env default."""
    if flag is not None:
        return bool(flag)
    from app.services.runner.sandbox import sandbox_enabled as resolve

    return resolve()


def _verify_command_invocation(command: str) -> list[str] | str:
    """Build the sandbox invocation for one shell command string.

    POSIX keeps an argv list (``sh -c`` receives its argument verbatim).
    Windows returns a raw command line string: ``cmd /d /s /c "<command>"``.
    The command's own quotes must reach cmd.exe unescaped — cmd does not
    honor MSVCRT ``\\"`` — and the ``/s`` switch makes cmd strip the outer
    quotes, so the inner command line stays intact.
    """
    if sys.platform == "win32":
        shell = os.environ.get("COMSPEC", "cmd.exe")
        return f'"{shell}" /d /s /c "{command}"'
    return ["/bin/sh", "-c", command]


def _run_verify_command_sandboxed(
    command: str,
    cwd: Path,
    timeout_seconds: float,
) -> VerifyCommandOutcome:
    from app.services.runner import sandbox

    invocation = _verify_command_invocation(command)
    policy = sandbox.build_sandbox_policy(cwd, timeout_seconds)
    completed = sandbox.run_sandboxed(invocation, str(cwd), policy)
    return VerifyCommandOutcome(
        command=command,
        exit_code=completed.returncode,
        output=completed.stdout + completed.stderr,
        timed_out=completed.returncode is None,
    )


def _verify_command_failure_summary(
    outcome: VerifyCommandOutcome,
    *,
    label: str = "verify command",
) -> str:
    if outcome.timed_out:
        reason = f"{label} timed out"
    else:
        reason = f"{label} failed with exit code {outcome.exit_code}"
    tail = outcome.output[-VERIFY_COMMAND_OUTPUT_TAIL_CHARS:]
    return f"{reason}: {outcome.command}\n--- output tail ---\n{tail}"
