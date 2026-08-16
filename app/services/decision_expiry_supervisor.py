"""Process-local supervision for durable Decision expiry."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol


class DecisionExpiryOutcomePort(Protocol):
    @property
    def expired(self) -> bool: ...


class DecisionExpiryCommandPort(Protocol):
    async def expire_next_decision(self) -> DecisionExpiryOutcomePort: ...


class DecisionExpiryPollStatus(str, Enum):
    EXPIRED = "expired"
    IDLE = "idle"


@dataclass(frozen=True, slots=True)
class DecisionExpirySupervisorSnapshot:
    running: bool = False
    ready: bool = False
    stop_requested: bool = False
    polls: int = 0
    expired: int = 0
    idle_polls: int = 0
    failed_polls: int = 0
    consecutive_failures: int = 0
    current_delay_seconds: float = 0.0
    last_error_type: str | None = None
    last_poll_status: DecisionExpiryPollStatus | None = None
    last_poll_at: str | None = None
    last_success_at: str | None = None

    def to_public_dict(self) -> dict[str, object]:
        return {
            "running": self.running,
            "ready": self.ready,
            "stopRequested": self.stop_requested,
            "polls": self.polls,
            "expired": self.expired,
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
            "lastSuccessAt": self.last_success_at,
        }


class DecisionExpirySupervisor:
    """Drain expired Decisions while Mission Control owns all durable state."""

    def __init__(
        self,
        command: DecisionExpiryCommandPort,
        *,
        idle_delay_seconds: float = 0.5,
        max_delay_seconds: float = 10.0,
    ) -> None:
        if idle_delay_seconds <= 0:
            raise ValueError("idle_delay_seconds must be positive")
        if max_delay_seconds < idle_delay_seconds:
            raise ValueError("max_delay_seconds must not be lower than idle delay")
        self._command = command
        self._idle_delay_seconds = idle_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        self._stop_event = asyncio.Event()
        self._snapshot = DecisionExpirySupervisorSnapshot()

    @property
    def snapshot(self) -> DecisionExpirySupervisorSnapshot:
        return self._snapshot

    def request_stop(self) -> None:
        self._stop_event.set()
        self._snapshot = replace(self._snapshot, stop_requested=True)

    async def run(self) -> None:
        if self._snapshot.running:
            raise RuntimeError("Decision expiry supervisor is already running")
        self._snapshot = replace(
            self._snapshot,
            running=True,
            ready=False,
            stop_requested=self._stop_event.is_set(),
        )
        delay = 0.0
        try:
            while not self._stop_event.is_set():
                delay = await self._poll_once(delay)
                self._snapshot = replace(
                    self._snapshot,
                    current_delay_seconds=delay,
                )
                if delay > 0 and await self._wait_for_stop(delay):
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
            outcome = await self._command.expire_next_decision()
            if not isinstance(outcome.expired, bool):
                raise TypeError("Decision expiry command returned an invalid outcome")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - supervisor must remain available
            delay = self._next_delay(previous_delay)
            self._snapshot = replace(
                self._snapshot,
                ready=False,
                failed_polls=self._snapshot.failed_polls + 1,
                consecutive_failures=self._snapshot.consecutive_failures + 1,
                last_error_type=type(exc).__name__,
            )
            return delay

        if not outcome.expired:
            delay = self._next_delay(previous_delay)
            self._snapshot = replace(
                self._snapshot,
                ready=True,
                idle_polls=self._snapshot.idle_polls + 1,
                consecutive_failures=0,
                last_error_type=None,
                last_poll_status=DecisionExpiryPollStatus.IDLE,
                last_success_at=datetime.now(timezone.utc).isoformat(),
            )
            return delay

        self._snapshot = replace(
            self._snapshot,
            ready=True,
            expired=self._snapshot.expired + 1,
            consecutive_failures=0,
            last_error_type=None,
            last_poll_status=DecisionExpiryPollStatus.EXPIRED,
            last_success_at=datetime.now(timezone.utc).isoformat(),
        )
        return 0.0

    def _next_delay(self, previous_delay: float) -> float:
        if previous_delay <= 0:
            return self._idle_delay_seconds
        return min(previous_delay * 2, self._max_delay_seconds)

    async def _wait_for_stop(self, delay: float) -> bool:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
        except TimeoutError:
            return False
        return True


__all__ = [
    "DecisionExpiryCommandPort",
    "DecisionExpiryOutcomePort",
    "DecisionExpiryPollStatus",
    "DecisionExpirySupervisor",
    "DecisionExpirySupervisorSnapshot",
]
