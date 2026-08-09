from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.services.harness_service import FunctionCall, FunctionResult, ModelUsage


class HarnessError(RuntimeError):
    """Raised when a Harness cannot execute a bounded WorkUnit request."""


@dataclass(frozen=True)
class HarnessExecutionContext:
    """Stable correlation fields for one leased WorkUnit attempt."""

    mission_id: str
    work_unit_id: str
    attempt: int

    def __post_init__(self) -> None:
        if not self.mission_id or not self.work_unit_id:
            raise ValueError("Harness execution ids must be non-empty")
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int):
            raise TypeError("Harness execution attempt must be a positive integer")
        if self.attempt < 1:
            raise ValueError("Harness execution attempt must be a positive integer")


class HarnessEventType(str, Enum):
    EXECUTION_STARTED = "harness.execution.started"
    ITERATION_STARTED = "harness.iteration.started"
    MODEL_STARTED = "harness.model.started"
    MODEL_COMPLETED = "harness.model.completed"
    TOOL_STARTED = "harness.tool.started"
    TOOL_COMPLETED = "harness.tool.completed"
    BUDGET_EXHAUSTED = "harness.budget.exhausted"
    EXECUTION_COMPLETED = "harness.execution.completed"
    EXECUTION_FAILED = "harness.execution.failed"


@dataclass(frozen=True)
class HarnessEvent:
    """Content-free execution event safe for observability and audit adapters."""

    sequence: int
    event_type: HarnessEventType
    execution: HarnessExecutionContext | None
    duration_ms: int
    iteration: int
    tool_calls: int
    usage: ModelUsage
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_success: bool | None = None
    budget: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class HarnessCheckpoint:
    """Request-scoped loop state captured alongside one execution event."""

    sequence: int
    phase: HarnessEventType
    execution: HarnessExecutionContext | None
    iteration: int
    tool_calls: int
    usage: ModelUsage
    tool_results: tuple[FunctionResult, ...]
    terminal: bool = False
    failure_reason: str | None = None


class HarnessCheckpointPort(Protocol):
    """Atomically records a request-scoped checkpoint and its event."""

    async def record(
        self,
        checkpoint: HarnessCheckpoint,
        event: HarnessEvent,
    ) -> None: ...


class InMemoryHarnessCheckpointPort:
    """Single-execution checkpoint journal for tests and local supervision."""

    def __init__(self) -> None:
        self._records: list[tuple[HarnessCheckpoint, HarnessEvent]] = []

    @property
    def checkpoints(self) -> tuple[HarnessCheckpoint, ...]:
        return tuple(checkpoint for checkpoint, _ in self._records)

    @property
    def events(self) -> tuple[HarnessEvent, ...]:
        return tuple(event for _, event in self._records)

    @property
    def latest(self) -> HarnessCheckpoint | None:
        return self._records[-1][0] if self._records else None

    async def record(
        self,
        checkpoint: HarnessCheckpoint,
        event: HarnessEvent,
    ) -> None:
        if checkpoint.sequence != event.sequence:
            raise HarnessError("Harness checkpoint and event sequence must match")
        if checkpoint.execution != event.execution:
            raise HarnessError("Harness checkpoint and event context must match")
        if self._records and event.event_type is HarnessEventType.EXECUTION_STARTED:
            raise HarnessError("In-memory Harness checkpoint port is request-scoped")
        expected_sequence = len(self._records) + 1
        if event.sequence != expected_sequence:
            raise HarnessError("Harness checkpoint sequence must be contiguous")
        self._records.append((checkpoint, event))


class _HarnessRecorder:
    def __init__(
        self,
        port: HarnessCheckpointPort | None,
        execution: HarnessExecutionContext | None,
        started_at: float,
    ) -> None:
        self._port = port
        self._execution = execution
        self._started_at = started_at
        self._sequence = 0

    async def record(
        self,
        event_type: HarnessEventType,
        *,
        iteration: int,
        tool_calls: int,
        usage: ModelUsage,
        tool_results: tuple[FunctionResult, ...],
        tool_call: FunctionCall | None = None,
        tool_success: bool | None = None,
        budget: str | None = None,
        reason: str | None = None,
        terminal: bool = False,
    ) -> None:
        if self._port is None:
            return
        self._sequence += 1
        duration_ms = int((time.monotonic() - self._started_at) * 1000)
        event = HarnessEvent(
            sequence=self._sequence,
            event_type=event_type,
            execution=self._execution,
            duration_ms=duration_ms,
            iteration=iteration,
            tool_calls=tool_calls,
            usage=usage,
            tool_call_id=tool_call.id if tool_call is not None else None,
            tool_name=tool_call.name if tool_call is not None else None,
            tool_success=tool_success,
            budget=budget,
            reason=reason,
        )
        checkpoint = HarnessCheckpoint(
            sequence=self._sequence,
            phase=event_type,
            execution=self._execution,
            iteration=iteration,
            tool_calls=tool_calls,
            usage=usage,
            tool_results=tool_results,
            terminal=terminal,
            failure_reason=reason if terminal else None,
        )
        try:
            await self._port.record(checkpoint, event)
        except HarnessError:
            raise
        except Exception as exc:
            raise HarnessError("Harness checkpoint recording failed") from exc


__all__ = [
    "HarnessCheckpoint",
    "HarnessCheckpointPort",
    "HarnessError",
    "HarnessEvent",
    "HarnessEventType",
    "HarnessExecutionContext",
    "InMemoryHarnessCheckpointPort",
]
