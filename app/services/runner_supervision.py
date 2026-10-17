"""Attempt fences and bounded supervision through terminal command ACKs."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import TypeVar

from app.services.runner_protocols import RunnerControlError, RunnerHeartbeatError

T = TypeVar("T")
logger = logging.getLogger("agenthub.runner")
CANCEL_DRAIN_SECONDS = 0.25
TERMINAL_ACK_SECONDS = 0.5


class RunnerTerminalAckUncertainError(RunnerControlError):
    """A dispatched terminal command has no confirmed Mission Control ACK."""


@dataclass
class AttemptFence:
    deadline: float
    aborted: bool = False
    terminal_kind: str | None = None

    def abort(self) -> None:
        self.aborted = True

    def ensure_active(self) -> None:
        if self.aborted:
            raise asyncio.CancelledError("Runner attempt was aborted")
        if asyncio.get_running_loop().time() >= self.deadline:
            raise TimeoutError("Runner attempt deadline expired")

    def dispatch_terminal(self, kind: str) -> None:
        self.ensure_active()
        self.terminal_kind = kind


def _consume_result(task: asyncio.Future) -> None:
    if not task.cancelled():
        task.exception()


async def _drain(tasks: tuple[asyncio.Future, ...], timeout: float) -> None:
    done, pending = await asyncio.wait(tasks, timeout=timeout)
    for task in done:
        _consume_result(task)
    for task in pending:
        # Cancellation is cooperative. The fence prevents a late task from
        # dispatching more commands even when its port ignores cancellation.
        task.add_done_callback(_consume_result)
    if pending:
        logger.warning("Runner cancellation cleanup exceeded %.2fs", timeout)


async def _terminal_ack(task: asyncio.Future[T], grace: float) -> T:
    done, _ = await asyncio.wait((task,), timeout=grace)
    if not done:
        raise RunnerTerminalAckUncertainError(
            "terminal command ACK timed out; Mission Control state is unknown"
        )
    try:
        return task.result()
    except (asyncio.CancelledError, Exception) as exc:
        raise RunnerTerminalAckUncertainError(
            "terminal command has no confirmed ACK; Mission Control state is unknown"
        ) from exc


async def bounded_cleanup(operation: Awaitable[None], *, timeout: float = CANCEL_DRAIN_SECONDS) -> None:
    task = asyncio.ensure_future(operation)
    done, _ = await asyncio.wait((task,), timeout=timeout)
    if done:
        _consume_result(task)
        return
    task.cancel()
    task.add_done_callback(_consume_result)
    logger.warning("Runner final state flush exceeded %.2fs", timeout)


async def _watch(attempt, heartbeat, fence, ack_grace):
    try:
        async with asyncio.timeout_at(fence.deadline):
            done, _ = await asyncio.wait((attempt, heartbeat), return_when=asyncio.FIRST_COMPLETED)
    except TimeoutError:
        fence.abort()
        if fence.terminal_kind is not None:
            return await _terminal_ack(attempt, ack_grace)
        raise
    if attempt in done:
        return attempt.result()
    error = RunnerHeartbeatError("heartbeat supervisor was cancelled") if heartbeat.cancelled() else heartbeat.exception()
    fence.abort()
    if fence.terminal_kind is not None:
        # A successful response is the authoritative ACK of the exact
        # lease-fenced command/refs. A heartbeat alone proves no ACK.
        return await _terminal_ack(attempt, ack_grace)
    raise RunnerHeartbeatError("lease heartbeat failed") from error


async def supervise(operation: Awaitable[T], heartbeat: Awaitable[None], *, timeout: float,
                    fence: AttemptFence | None = None, drain_timeout: float = CANCEL_DRAIN_SECONDS,
                    ack_grace: float = TERMINAL_ACK_SECONDS) -> T:
    fence = fence or AttemptFence(asyncio.get_running_loop().time() + timeout)
    attempt_task = asyncio.ensure_future(operation)
    heartbeat_task = asyncio.create_task(heartbeat)
    try:
        return await _watch(attempt_task, heartbeat_task, fence, ack_grace)
    finally:
        fence.abort()
        for task in (attempt_task, heartbeat_task):
            if not task.done():
                task.cancel()
        await _drain((attempt_task, heartbeat_task), drain_timeout)
