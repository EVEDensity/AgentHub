"""Process-local polling supervision for one independent verifier."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Protocol

from app.services.verifier_service import VerifierPollStatus


class VerifierPollResultPort(Protocol):
    status: VerifierPollStatus


class ControlledVerifierPort(Protocol):
    async def discover_and_verify(
        self,
        workspace_id: str,
    ) -> VerifierPollResultPort: ...


@dataclass(frozen=True, slots=True)
class VerifierWorkerSnapshot:
    running: bool = False
    ready: bool = False
    stop_requested: bool = False
    polls: int = 0
    verified: int = 0
    idle_polls: int = 0
    failed_polls: int = 0
    consecutive_failures: int = 0
    current_delay_seconds: float = 0.0
    last_error_type: str | None = None
    last_poll_status: VerifierPollStatus | None = None
    last_poll_at: str | None = None

    def to_public_dict(self) -> dict[str, object]:
        return {
            "running": self.running,
            "ready": self.ready,
            "stopRequested": self.stop_requested,
            "polls": self.polls,
            "verified": self.verified,
            "idlePolls": self.idle_polls,
            "failedPolls": self.failed_polls,
            "consecutiveFailures": self.consecutive_failures,
            "currentDelaySeconds": self.current_delay_seconds,
            "lastErrorType": self.last_error_type,
            "lastPollStatus": (
                self.last_poll_status.value
                if self.last_poll_status is not None
                else None
            ),
            "lastPollAt": self.last_poll_at,
        }


class VerifierWorker:
    """Poll one workspace while Mission Control owns durable serialization."""

    def __init__(
        self,
        verifier: ControlledVerifierPort,
        *,
        workspace_id: str,
        idle_delay_seconds: float = 0.5,
        max_delay_seconds: float = 10.0,
    ) -> None:
        if not workspace_id.strip():
            raise ValueError("workspace_id must be non-empty")
        if idle_delay_seconds <= 0:
            raise ValueError("idle_delay_seconds must be positive")
        if max_delay_seconds < idle_delay_seconds:
            raise ValueError("max_delay_seconds must not be lower than idle delay")
        self._verifier = verifier
        self._workspace_id = workspace_id
        self._idle_delay_seconds = idle_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        self._stop_event = asyncio.Event()
        self._snapshot = VerifierWorkerSnapshot(
            current_delay_seconds=idle_delay_seconds
        )

    @property
    def snapshot(self) -> VerifierWorkerSnapshot:
        return self._snapshot

    def request_stop(self) -> None:
        self._stop_event.set()
        self._snapshot = replace(self._snapshot, stop_requested=True)

    async def run(self) -> None:
        if self._snapshot.running:
            raise RuntimeError("Verifier worker is already running")
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
            result = await self._verifier.discover_and_verify(self._workspace_id)
            if not isinstance(result.status, VerifierPollStatus):
                raise TypeError("verifier poll returned an invalid status")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - supervisor must stay alive
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

        if result.status == VerifierPollStatus.IDLE:
            delay = min(
                max(previous_delay, self._idle_delay_seconds) * 2,
                self._max_delay_seconds,
            )
            self._snapshot = replace(
                self._snapshot,
                ready=True,
                idle_polls=self._snapshot.idle_polls + 1,
                consecutive_failures=0,
                last_error_type=None,
                last_poll_status=result.status,
            )
            return delay

        self._snapshot = replace(
            self._snapshot,
            ready=True,
            verified=self._snapshot.verified + 1,
            consecutive_failures=0,
            last_error_type=None,
            last_poll_status=result.status,
        )
        return self._idle_delay_seconds

    async def _wait_for_stop(self, delay: float) -> bool:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
        except TimeoutError:
            return False
        return True


__all__ = [
    "ControlledVerifierPort",
    "VerifierPollResultPort",
    "VerifierWorker",
    "VerifierWorkerSnapshot",
]
