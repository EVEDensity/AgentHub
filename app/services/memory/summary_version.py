from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SummaryVersion:
    covered_sequence_start: int = 0
    covered_sequence_end: int = 0
    generated_at: float = 0.0
    source_event_id: str = ""


def should_accept_summary(current: SummaryVersion, incoming: SummaryVersion) -> bool:
    if incoming.source_event_id and incoming.source_event_id == current.source_event_id:
        return False
    if (
        incoming.covered_sequence_end > 0
        and current.covered_sequence_end > 0
        and incoming.covered_sequence_end <= current.covered_sequence_end
    ):
        return False
    if incoming.generated_at > 0 and current.generated_at > incoming.generated_at:
        return False
    return True
