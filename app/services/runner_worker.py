from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Protocol

from app.services.runner_service import RunnerWorkspacePollResult
from app.services.workspace_admission_service import WorkspaceClaimStatus


class ClaimingRunnerPort(Protocol):
    async def claim_ready_and_run(
        self,
        workspace_id: str,
        *,
        lease_seconds: int = 300,
        artifact_kind: str = "test-result",
        media_type: str = "text/plain",
    ) -> RunnerWorkspacePollResult: ...


@dataclass(frozen=True, slots=True)
class RunnerWorkerSnapshot:
    running: bool = False
    ready: bool = False
    stop_requested: bool = False
    polls: int = 0
    claimed: int = 0
    idle_polls: int = 0
    capacity_saturated_polls: int = 0
    failed_polls: int = 0
    consecutive_failures: int = 0
    current_delay_seconds: float = 0.0
    last_error_type: str | None = None
    last_claim_status: WorkspaceClaimStatus | None = None
    last_poll_at: str | None = None

    def to_public_dict(self) -> dict[str, object]:
        return {
            "running": self.running,
            "ready": self.ready,
            "stopRequested": self.stop_requested,
            "polls": self.polls,
            "claimed": self.claimed,
            "idlePolls": self.idle_polls,
            "capacitySaturatedPolls": self.capacity_saturated_polls,
            "failedPolls": self.failed_polls,
            "consecutiveFailures": self.consecutive_failures,
            "currentDelaySeconds": self.current_delay_seconds,
            "lastErrorType": self.last_error_type,
            "lastClaimStatus": (
                self.last_claim_status.value
                if self.last_claim_status is not None
                else None
            ),
            "lastPollAt": self.last_poll_at,
        }


class RunnerWorker:
    """Poll one workspace without owning queue or lifecycle truth."""

    def __init__(
        self,
        runner: ClaimingRunnerPort,
        *,
        workspace_id: str,
        lease_seconds: int = 300,
        idle_delay_seconds: float = 0.5,
        max_delay_seconds: float = 10.0,
    ) -> None:
        if not workspace_id.strip():
            raise ValueError("workspace_id must be non-empty")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        if idle_delay_seconds <= 0:
            raise ValueError("idle_delay_seconds must be positive")
        if max_delay_seconds < idle_delay_seconds:
            raise ValueError("max_delay_seconds must not be lower than idle delay")
        self._runner = runner
        self._workspace_id = workspace_id
        self._lease_seconds = lease_seconds
        self._idle_delay_seconds = idle_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        self._stop_event = asyncio.Event()
        self._snapshot = RunnerWorkerSnapshot(
            current_delay_seconds=idle_delay_seconds
        )

    @property
    def snapshot(self) -> RunnerWorkerSnapshot:
        return self._snapshot

    def request_stop(self) -> None:
        self._stop_event.set()
        self._snapshot = replace(self._snapshot, stop_requested=True)

    async def run(self) -> None:
        if self._snapshot.running:
            raise RuntimeError("Runner worker is already running")
        self._snapshot = replace(
            self._snapshot,
            running=True,
            ready=False,
            stop_requested=self._stop_event.is_set(),
        )
        delay = self._idle_delay_seconds
        try:
            while not self._stop_event.is_set():
                delay = await self._poll_once(delay)
                self._snapshot = replace(
                    self._snapshot,
                    current_delay_seconds=delay,
                )
                if await self._wait_for_stop(delay):
                    break
        finally:
            self._snapshot = replace(
                self._snapshot,
                running=False,
                ready=False,
                stop_requested=self._stop_event.is_set(),
            )

    async def _poll_once(self, previous_delay: float) -> float:
        self._snapshot = replace(
            self._snapshot,
            polls=self._snapshot.polls + 1,
            last_poll_at=datetime.now(timezone.utc).isoformat(),
        )
        try:
            result = await self._runner.claim_ready_and_run(
                self._workspace_id,
                lease_seconds=self._lease_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - poll supervisor must stay alive
            delay = min(
                max(previous_delay, self._idle_delay_seconds) * 2,
                self._max_delay_seconds,
            )
            self._snapshot = replace(
                self._snapshot,
                ready=False,
                failed_polls=self._snapshot.failed_polls + 1,
                consecutive_failures=self._snapshot.consecutive_failures + 1,
                last_error_type=type(exc).__name__,
            )
            return delay

        if result.claim_status != WorkspaceClaimStatus.CLAIMED:
            delay = min(
                max(previous_delay, self._idle_delay_seconds) * 2,
                self._max_delay_seconds,
            )
            self._snapshot = replace(
                self._snapshot,
                ready=True,
                idle_polls=(
                    self._snapshot.idle_polls
                    + (result.claim_status == WorkspaceClaimStatus.IDLE)
                ),
                capacity_saturated_polls=(
                    self._snapshot.capacity_saturated_polls
                    + (
                        result.claim_status
                        == WorkspaceClaimStatus.CAPACITY_SATURATED
                    )
                ),
                consecutive_failures=0,
                last_error_type=None,
                last_claim_status=result.claim_status,
            )
            return delay

        self._snapshot = replace(
            self._snapshot,
            ready=True,
            claimed=self._snapshot.claimed + 1,
            consecutive_failures=0,
            last_error_type=None,
            last_claim_status=result.claim_status,
        )
        return self._idle_delay_seconds

    async def _wait_for_stop(self, delay: float) -> bool:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
        except TimeoutError:
            return False
        return True


__all__ = [
    "ClaimingRunnerPort",
    "RunnerWorker",
    "RunnerWorkerSnapshot",
]
