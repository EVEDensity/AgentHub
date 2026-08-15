from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.services.a2a_outbound_runner import (
    A2AOutboundClaimedWork,
    A2AOutboundTransportPort,
    A2ARemoteTaskSnapshot,
    A2ARemoteTaskState,
)
from app.services.runner_service import (
    MissionControlRunnerPort,
    RunnerExecutionError,
)


class A2AOutboundSupervisionError(RunnerExecutionError):
    """Raised when outbound supervision cannot preserve an honest lifecycle."""


class _HeartbeatSupervisionFailure(A2AOutboundSupervisionError):
    pass


class _RemotePollingTimeout(TimeoutError):
    def __init__(
        self,
        *,
        remote_task: A2ARemoteTaskSnapshot | None,
        poll_count: int,
    ) -> None:
        super().__init__("remote A2A polling timed out")
        self.remote_task = remote_task
        self.poll_count = poll_count


class A2AOutboundSupervisionOutcome(str, Enum):
    """Finite outcomes before remote result import exists."""

    RESULT_READY = "result_ready"
    REMOTE_FAILED = "remote_failed"
    REMOTE_CANCELED = "remote_canceled"
    INPUT_REQUIRED_UNSUPPORTED = "input_required_unsupported"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True, slots=True)
class A2AOutboundSupervisionResult:
    outcome: A2AOutboundSupervisionOutcome
    work_unit: dict[str, Any]
    remote_task: A2ARemoteTaskSnapshot | None
    poll_count: int
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if self.poll_count < 0:
            raise ValueError("poll_count must be non-negative")
        has_failure = self.failure_reason is not None
        if has_failure == (self.outcome == A2AOutboundSupervisionOutcome.RESULT_READY):
            raise ValueError("supervision outcome and failure reason are inconsistent")


class A2AOutboundSupervisor:
    """Supervise one resolved remote attempt without importing its result."""

    def __init__(
        self,
        control: MissionControlRunnerPort,
        transport: A2AOutboundTransportPort,
        *,
        runner_id: str,
        poll_interval_seconds: float = 1.0,
        heartbeat_interval_seconds: float | None = None,
        cancellation_timeout_seconds: float = 5.0,
    ) -> None:
        if not isinstance(runner_id, str) or not runner_id.strip():
            raise ValueError("runner_id must be non-empty")
        if not math.isfinite(poll_interval_seconds) or poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive and finite")
        if heartbeat_interval_seconds is not None and (
            not math.isfinite(heartbeat_interval_seconds)
            or heartbeat_interval_seconds <= 0
        ):
            raise ValueError(
                "heartbeat_interval_seconds must be positive and finite"
            )
        if (
            not math.isfinite(cancellation_timeout_seconds)
            or cancellation_timeout_seconds <= 0
        ):
            raise ValueError(
                "cancellation_timeout_seconds must be positive and finite"
            )
        self._control = control
        self._transport = transport
        self._runner_id = runner_id
        self._poll_interval_seconds = poll_interval_seconds
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._cancellation_timeout_seconds = cancellation_timeout_seconds

    async def supervise(
        self,
        work: A2AOutboundClaimedWork,
        *,
        lease_seconds: int = 300,
    ) -> A2AOutboundSupervisionResult:
        if not isinstance(work, A2AOutboundClaimedWork):
            raise TypeError("work must be an A2AOutboundClaimedWork")
        if not 1 <= lease_seconds <= 3_600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        started = await self._start(work)
        remote_task = asyncio.create_task(self._run_remote_lifecycle(work))
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(work, lease_seconds=lease_seconds)
        )
        try:
            try:
                done, _ = await asyncio.wait(
                    (remote_task, heartbeat_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if heartbeat_task in done:
                    heartbeat_error = heartbeat_task.exception()
                    if heartbeat_error is None:
                        heartbeat_error = A2AOutboundSupervisionError(
                            "outbound heartbeat stopped unexpectedly"
                        )
                    remote_task.cancel()
                    await _drain_task(remote_task)
                    await self._best_effort_remote_cancel(work)
                    await self._fail(
                        work,
                        "outbound A2A heartbeat supervision failed",
                    )
                    raise _HeartbeatSupervisionFailure(
                        "outbound A2A heartbeat supervision failed"
                    ) from heartbeat_error
                snapshot, poll_count = remote_task.result()
            except asyncio.CancelledError:
                remote_task.cancel()
                await _drain_task(remote_task)
                await self._best_effort_remote_cancel(work)
                with suppress(A2AOutboundSupervisionError):
                    await self._fail(work, "outbound A2A supervision cancelled")
                raise
            except _RemotePollingTimeout as exc:
                await self._best_effort_remote_cancel(work)
                reason = "remote A2A task exceeded the WorkUnit time budget"
                failed = await self._fail(work, reason)
                return A2AOutboundSupervisionResult(
                    outcome=A2AOutboundSupervisionOutcome.TIMED_OUT,
                    work_unit=failed,
                    remote_task=exc.remote_task,
                    poll_count=exc.poll_count,
                    failure_reason=reason,
                )
            except _HeartbeatSupervisionFailure:
                raise
            except Exception as exc:
                await self._best_effort_remote_cancel(work)
                await self._fail(
                    work,
                    f"outbound A2A transport failed: {type(exc).__name__}",
                )
                raise A2AOutboundSupervisionError(
                    "outbound A2A transport supervision failed"
                ) from exc
        finally:
            if not heartbeat_task.done():
                heartbeat_task.cancel()
            await _drain_task(heartbeat_task)

        if snapshot.state == A2ARemoteTaskState.COMPLETED:
            return A2AOutboundSupervisionResult(
                outcome=A2AOutboundSupervisionOutcome.RESULT_READY,
                work_unit=started,
                remote_task=snapshot,
                poll_count=poll_count,
            )
        outcome, reason = _remote_failure(snapshot.state)
        if snapshot.state == A2ARemoteTaskState.INPUT_REQUIRED:
            await self._best_effort_remote_cancel(work)
        failed = await self._fail(work, reason)
        return A2AOutboundSupervisionResult(
            outcome=outcome,
            work_unit=failed,
            remote_task=snapshot,
            poll_count=poll_count,
            failure_reason=reason,
        )

    async def _start(
        self,
        work: A2AOutboundClaimedWork,
    ) -> dict[str, Any]:
        try:
            started = await self._control.start_work_unit(
                work.mission_id,
                work.work_unit_id,
                runner_id=self._runner_id,
                lease_id=work.lease_id,
            )
            _assert_active_lease(
                started,
                work,
                runner_id=self._runner_id,
                expected_status="RUNNING",
            )
        except Exception as exc:
            raise A2AOutboundSupervisionError(
                "Mission Control rejected outbound WorkUnit start"
            ) from exc
        return dict(started)

    async def _run_remote_lifecycle(
        self,
        work: A2AOutboundClaimedWork,
    ) -> tuple[A2ARemoteTaskSnapshot, int]:
        poll_count = 0
        snapshot: A2ARemoteTaskSnapshot | None = None
        timeout = asyncio.timeout(work.timeout_seconds)
        try:
            async with timeout:
                snapshot = await self._transport.send(work.command)
                _assert_remote_snapshot(snapshot, work)
                while snapshot.state in {
                    A2ARemoteTaskState.SUBMITTED,
                    A2ARemoteTaskState.WORKING,
                }:
                    await asyncio.sleep(self._poll_interval_seconds)
                    snapshot = await self._transport.get(work.command.reference)
                    poll_count += 1
                    _assert_remote_snapshot(snapshot, work)
                return snapshot, poll_count
        except TimeoutError as exc:
            if not timeout.expired():
                raise
            raise _RemotePollingTimeout(
                remote_task=snapshot,
                poll_count=poll_count,
            ) from exc

    async def _heartbeat_loop(
        self,
        work: A2AOutboundClaimedWork,
        *,
        lease_seconds: int,
    ) -> None:
        interval = self._heartbeat_interval_seconds
        if interval is None:
            interval = min(max(lease_seconds / 3, 0.1), 30.0)
        while True:
            await asyncio.sleep(interval)
            renewed = await self._control.heartbeat_work_unit(
                work.mission_id,
                work.work_unit_id,
                runner_id=self._runner_id,
                lease_id=work.lease_id,
                lease_seconds=lease_seconds,
            )
            _assert_active_lease(
                renewed,
                work,
                runner_id=self._runner_id,
                expected_status="RUNNING",
            )

    async def _best_effort_remote_cancel(
        self,
        work: A2AOutboundClaimedWork,
    ) -> None:
        try:
            await asyncio.wait_for(
                self._transport.cancel(work.command.reference),
                timeout=self._cancellation_timeout_seconds,
            )
        except Exception:  # noqa: BLE001 - cancellation cannot replace primary failure
            return

    async def _fail(
        self,
        work: A2AOutboundClaimedWork,
        reason: str,
    ) -> dict[str, Any]:
        try:
            failed = await self._control.fail_work_unit(
                work.mission_id,
                work.work_unit_id,
                runner_id=self._runner_id,
                lease_id=work.lease_id,
                reason=reason[:2_000],
            )
        except Exception as exc:
            raise A2AOutboundSupervisionError(
                "Mission Control could not record outbound WorkUnit failure"
            ) from exc
        if not isinstance(failed, Mapping):
            raise A2AOutboundSupervisionError(
                "Mission Control returned an invalid WorkUnit failure response"
            )
        _assert_failed_work_unit(failed, work)
        return dict(failed)


def _assert_active_lease(
    payload: Mapping[str, Any],
    work: A2AOutboundClaimedWork,
    *,
    runner_id: str,
    expected_status: str,
) -> None:
    if not isinstance(payload, Mapping):
        raise A2AOutboundSupervisionError(
            "Mission Control returned an invalid WorkUnit response"
        )
    if payload.get("id") != work.work_unit_id:
        raise A2AOutboundSupervisionError("Mission Control changed the WorkUnit id")
    if payload.get("missionId") != work.mission_id:
        raise A2AOutboundSupervisionError("Mission Control changed the Mission id")
    if payload.get("status") != expected_status:
        raise A2AOutboundSupervisionError("Mission Control changed the WorkUnit status")
    if payload.get("attempt") != work.attempt:
        raise A2AOutboundSupervisionError("Mission Control changed the WorkUnit attempt")
    lease = payload.get("lease")
    if not isinstance(lease, Mapping):
        raise A2AOutboundSupervisionError("Mission Control response has no active lease")
    if lease.get("id") != work.lease_id or lease.get("runnerId") != runner_id:
        raise A2AOutboundSupervisionError("Mission Control changed the WorkUnit lease")


def _assert_failed_work_unit(
    payload: Mapping[str, Any],
    work: A2AOutboundClaimedWork,
) -> None:
    if (
        payload.get("id") != work.work_unit_id
        or payload.get("missionId") != work.mission_id
        or payload.get("status") != "FAILED"
        or payload.get("attempt") != work.attempt
        or payload.get("lease") is not None
    ):
        raise A2AOutboundSupervisionError(
            "Mission Control returned an inconsistent WorkUnit failure response"
        )


def _assert_remote_snapshot(
    snapshot: A2ARemoteTaskSnapshot,
    work: A2AOutboundClaimedWork,
) -> None:
    if not isinstance(snapshot, A2ARemoteTaskSnapshot):
        raise A2AOutboundSupervisionError(
            "A2A transport returned an invalid remote task snapshot"
        )
    if snapshot.task_id != work.command.reference.task_id:
        raise A2AOutboundSupervisionError("A2A transport changed the remote task id")


def _remote_failure(
    state: A2ARemoteTaskState,
) -> tuple[A2AOutboundSupervisionOutcome, str]:
    if state == A2ARemoteTaskState.FAILED:
        return (
            A2AOutboundSupervisionOutcome.REMOTE_FAILED,
            "remote A2A task reported failure",
        )
    if state == A2ARemoteTaskState.CANCELED:
        return (
            A2AOutboundSupervisionOutcome.REMOTE_CANCELED,
            "remote A2A task was canceled",
        )
    if state == A2ARemoteTaskState.INPUT_REQUIRED:
        return (
            A2AOutboundSupervisionOutcome.INPUT_REQUIRED_UNSUPPORTED,
            "remote A2A task requires unsupported interactive input",
        )
    raise A2AOutboundSupervisionError(
        f"remote A2A lifecycle stopped in unsupported state: {state.value}"
    )


async def _drain_task(task: asyncio.Task[Any]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        return
    except Exception:  # noqa: BLE001 - the caller already captured the task outcome
        return


__all__ = [
    "A2AOutboundSupervisionError",
    "A2AOutboundSupervisionOutcome",
    "A2AOutboundSupervisionResult",
    "A2AOutboundSupervisor",
]
