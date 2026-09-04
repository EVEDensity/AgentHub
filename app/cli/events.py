"""Versioned CLI streaming event helpers.

Transport adapters may expose legacy ``eventType``/``aggregateType`` fields,
while newer producers use ``type``.  This module gives every CLI renderer one
small, deterministic view and keeps cursor/deduplication rules in one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

CLI_EVENT_SCHEMA_VERSION = 1

_CANONICAL_TYPES = {
    "mission.lifecycle.created": "mission.created",
    "work_unit.lifecycle.leased": "work_unit.claimed",
    "work_unit.lifecycle.started": "work_unit.running",
    "harness.assistant.delta": "assistant.delta",
    "harness.assistant.completed": "assistant.completed",
    "harness.tool.started": "tool.started",
    "harness.tool.output": "tool.output",
    "harness.tool.completed": "tool.completed",
    "decision.lifecycle.requested": "decision.pending",
    "decision.lifecycle.resolved": "decision.resolved",
    "work_unit.checkpoint.recorded": "checkpoint.created",
    "artifact.lifecycle.registered": "artifact.registered",
    "mission.lifecycle.verifying": "verification.started",
    "work_unit.lifecycle.verified": "verification.completed",
    "mission.lifecycle.succeeded": "mission.completed",
    "mission.lifecycle.failed": "mission.failed",
    "mission.lifecycle.cancelled": "mission.cancelled",
    "mission.lifecycle.timeout": "mission.timeout",
    "sse.lifecycle.reconnecting": "sse.reconnecting",
    "sse.lifecycle.connected": "sse.connected",
    "sse.lifecycle.polling": "sse.polling",
}


@dataclass(frozen=True)
class CliEvent:
    event_id: str
    sequence: int
    event_type: str
    aggregate_type: str
    mission_id: str
    work_unit_id: str
    attempt: int
    payload: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def text_delta(self) -> str | None:
        if self.event_type not in {
            "assistant.delta", "message.delta", "text.delta", "model.output.delta"
        }:
            return None
        value = self.payload.get("text") or self.payload.get("delta") or self.payload.get("content")
        return str(value) if value else None

    @property
    def status(self) -> str | None:
        value = self.payload.get("status")
        return str(value) if value else None


def normalize_event(value: Mapping[str, Any]) -> CliEvent | None:
    """Normalize a public SSE event; malformed frames are ignored safely."""
    if not isinstance(value, Mapping):
        return None
    raw_event_type = str(value.get("type") or value.get("eventType") or "").strip()
    event_type = _CANONICAL_TYPES.get(raw_event_type, raw_event_type)
    if not event_type:
        return None
    payload_value = value.get("payload")
    payload = dict(payload_value) if isinstance(payload_value, Mapping) else dict(value)
    try:
        sequence = int(value.get("sequence") or 0)
        attempt = int(value.get("attempt") or payload.get("attempt") or 0)
    except (TypeError, ValueError):
        return None
    return CliEvent(
        event_id=str(value.get("eventId") or value.get("event_id") or ""),
        sequence=sequence,
        event_type=event_type,
        aggregate_type=str(value.get("aggregateType") or value.get("aggregate_type") or ""),
        mission_id=str(value.get("missionId") or value.get("mission_id") or ""),
        work_unit_id=str(value.get("workUnitId") or value.get("work_unit_id") or ""),
        attempt=attempt,
        payload=payload,
        raw=dict(value),
    )


class EventCursor:
    """Mission aggregate cursor plus event-id deduplication."""

    def __init__(self, sequence: int = 0) -> None:
        self.sequence = max(0, int(sequence))
        self._seen: set[str] = set()

    def accept(self, event: CliEvent) -> bool:
        if event.event_id and event.event_id in self._seen:
            return False
        if event.event_id:
            self._seen.add(event.event_id)
        if event.aggregate_type == "mission":
            self.sequence = max(self.sequence, event.sequence)
        return True


def reorder_events(events: Iterable[CliEvent]) -> list[CliEvent]:
    """Order a bounded SSE batch without mixing aggregate sequences.

    Mission aggregate events are ordered by their durable sequence. Events
    from WorkUnit/Decision aggregates retain arrival order because their
    sequence spaces are independent.
    """
    values = list(events)
    mission = sorted(
        ((index, event) for index, event in enumerate(values) if event.aggregate_type == "mission"),
        key=lambda item: (item[1].sequence, item[0]),
    )
    iterator = iter(mission)
    return [next(iterator)[1] if event.aggregate_type == "mission" else event for event in values]
