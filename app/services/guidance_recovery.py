"""Private, bounded-by-image guidance cursor for one recoverable execution."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_GUIDANCE_EVENTS = 4096
MAX_GUIDANCE_STATE_BYTES = 512 * 1024


def format_guidance_block(guidance: Sequence[str]) -> str:
    lines = "\n".join(f"- {item}" for item in guidance)
    return f"[用户补充指导 · 运行中注入]\n{lines}"


class _StrictGuidance(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class GuidanceInjection(_StrictGuidance):
    event_ids: list[str] = Field(min_length=1)
    contents: list[str] = Field(min_length=1, repr=False)
    block: str = Field(repr=False)

    @model_validator(mode="after")
    def check_block(self):
        if len(self.event_ids) != len(self.contents) or len(set(self.event_ids)) != len(self.event_ids):
            raise ValueError("guidance injection event identities are invalid")
        if any(not item.strip() or item != item.strip() for item in self.contents):
            raise ValueError("guidance injection content is invalid")
        if self.block != format_guidance_block(self.contents):
            raise ValueError("guidance injection block differs from its events")
        return self


class GuidanceResumeState(_StrictGuidance):
    version: int = Field(default=1, ge=1, le=1)
    mission_id: str = Field(min_length=1)
    work_unit_id: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    after_sequence: int = Field(default=0, ge=0)
    consumed_event_ids: list[str] = Field(default_factory=list, repr=False)
    injections: list[GuidanceInjection] = Field(default_factory=list, repr=False)

    @model_validator(mode="after")
    def check_consumption(self):
        ids = self.consumed_event_ids
        if any(not item for item in ids) or len(ids) != len(set(ids)):
            raise ValueError("guidance cursor event identities are invalid")
        if bool(self.after_sequence) != bool(ids):
            raise ValueError("guidance cursor sequence and consumed identities differ")
        injected = [event_id for batch in self.injections for event_id in batch.event_ids]
        if len(injected) != len(set(injected)) or not set(injected).issubset(ids):
            raise ValueError("guidance injections are not a unique consumed event subset")
        return self


def _check_state_bound(state: GuidanceResumeState) -> None:
    if len(state.consumed_event_ids) > MAX_GUIDANCE_EVENTS:
        raise ValueError("guidance cursor exceeds 4096 events")
    if len(state.model_dump_json().encode("utf-8")) > MAX_GUIDANCE_STATE_BYTES:
        raise ValueError("guidance state exceeds 512 KiB")


def _next_state(state: GuidanceResumeState, events: list[Mapping[str, Any]], claimed: list[bool]) -> GuidanceResumeState:
    ids = [event["event_id"] for event in events]
    if len(set(ids)) != len(ids) or set(ids).intersection(state.consumed_event_ids):
        raise ValueError("guidance ledger reuses a consumed event identity")
    entries = [_guidance_content(event) for event in events]
    injected_ids = [event_id for event_id, content, owned in zip(ids, entries, claimed) if content and owned]
    contents = [content for content, owned in zip(entries, claimed) if content and owned]
    injections = list(state.injections)
    if contents:
        injections.append(GuidanceInjection(event_ids=injected_ids, contents=contents,
                                            block=format_guidance_block(contents)))
    return GuidanceResumeState(**{
        **state.model_dump(), "after_sequence": events[-1]["sequence"],
        "consumed_event_ids": [*state.consumed_event_ids, *ids], "injections": injections,
    })


def _mission_page(events: Any, mission_id: str, after_sequence: int) -> list[Mapping[str, Any]]:
    if not isinstance(events, list) or any(not isinstance(event, Mapping) for event in events):
        raise ValueError("guidance ledger page is malformed")
    page = []
    for event in events:
        aggregate = event.get("aggregate_type")
        if aggregate not in {"mission", "work_unit"}:
            raise ValueError("guidance ledger event has no aggregate identity")
        if aggregate != "mission":
            continue  # HTTP feed also includes bounded work-unit event windows.
        if event.get("aggregate_id") != mission_id:
            raise ValueError("guidance event belongs to another mission")
        sequence, event_id = event.get("sequence"), event.get("event_id")
        if type(sequence) is not int or sequence <= after_sequence or not isinstance(event_id, str) or not event_id:
            raise ValueError("guidance ledger event has invalid cursor identity")
        page.append(event)
    page.sort(key=lambda event: event["sequence"])
    if len({event["sequence"] for event in page}) != len(page):
        raise ValueError("guidance ledger repeats a sequence")
    return page


class GuidanceRecoveryCursor:
    def __init__(self, source: Any, execution: Any) -> None:
        if not all(callable(getattr(source, name, None)) for name in
                   ("read_events", "consume_event", "restore_consumed_event_ids")):
            raise ValueError("guidance source has no strict recovery reader")
        self.source = source
        self.limit = getattr(source, "event_limit", None)
        if type(self.limit) is not int or not 1 <= self.limit <= 200:
            raise ValueError("guidance recovery reader has no bounded event page")
        self.state = GuidanceResumeState(mission_id=execution.mission_id,
            work_unit_id=execution.work_unit_id, attempt=execution.attempt)

    def restore(self, state: GuidanceResumeState | None) -> None:
        if state is None or any(getattr(state, name) != getattr(self.state, name)
                                for name in ("mission_id", "work_unit_id", "attempt")):
            raise ValueError("guidance state is missing or belongs to another execution")
        _check_state_bound(state)
        self.state = state.model_copy(deep=True)
        self.source.restore_consumed_event_ids(state.consumed_event_ids)

    async def _read_pending_events(self) -> list[Mapping[str, Any]]:
        events = []
        cursor = self.state.after_sequence
        while True:
            raw = await self.source.read_events(self.state.mission_id, after_sequence=cursor)
            page = _mission_page(raw, self.state.mission_id, cursor)
            if len(page) > self.limit:
                raise ValueError("guidance ledger exceeds its bounded event page")
            events.extend(page)
            if events:
                # Bound ingestion before claiming IDs or invoking a model. Checking
                # all pending contents also bounds events already claimed elsewhere.
                _check_state_bound(_next_state(self.state, events, [True] * len(events)))
            if len(page) < self.limit:
                return events
            cursor = page[-1]["sequence"]

    async def pending_guidance(self) -> tuple[str, ...]:
        events = await self._read_pending_events()
        if not events:
            return ()
        claimed = [self.source.consume_event(event["event_id"]) for event in events]
        previous_batches = len(self.state.injections)
        self.state = _next_state(self.state, events, claimed)
        return tuple(self.state.injections[-1].contents) if len(self.state.injections) > previous_batches else ()


def _guidance_content(event: Mapping[str, Any]) -> str | None:
    if event.get("event_type") != "mission.guidance.added":
        return None
    payload = event.get("payload")
    content = payload.get("content") if isinstance(payload, Mapping) else None
    if not isinstance(content, str) or not content.strip():
        raise ValueError("guidance ledger event has no valid content")
    return content.strip()
