"""Run-time guidance injection for the desktop local runner (P1-1).

Users can push extra guidance into a RUNNING Mission through the Mission
API (``POST /missions/{id}/guidance`` → ``mission.guidance.added`` event).
The runner consumes it without stopping the execution: before every model
call the request-scoped :class:`GuidanceInjectingModel` asks its
:class:`GuidanceSourcePort` for unconsumed guidance and appends it to the
prompt. Each guidance entry is injected exactly once — consumption is
tracked per mission by event id (the runner-side view of the append-only
event ledger). Recoverable desktop executions also retain their private
ledger cursor and actual injected blocks in the admitted resume image.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Any, Protocol

import httpx

from app.repositories import MissionRepository
from app.services.guidance_recovery import (
    GuidanceRecoveryCursor,
    GuidanceResumeState,
    format_guidance_block,
)
from app.services.harness_service import (
    FunctionResult,
    HarnessRequest,
    ModelPort,
    ModelResponse,
)
from app.services.model_contract import Message, ModelRequest, ModelStreamEvent

logger = logging.getLogger("agenthub.desktop_guidance")

GUIDANCE_EVENT_TYPE = "mission.guidance.added"
GUIDANCE_CONTENT_KEY = "content"

class GuidanceSourcePort(Protocol):
    """Unconsumed mission guidance, bounded to one runner identity."""

    async def pending_guidance(self, mission_id: str) -> tuple[str, ...]: ...


class InMemoryGuidanceSource:
    """Guidance source for tests and local dry runs."""

    def __init__(self, guidance_by_mission: Mapping[str, Sequence[str]]) -> None:
        self._guidance = {
            mission_id: tuple(items)
            for mission_id, items in guidance_by_mission.items()
        }
        self.consumed: set[str] = set()

    async def pending_guidance(self, mission_id: str) -> tuple[str, ...]:
        if mission_id in self.consumed:
            return ()
        self.consumed.add(mission_id)
        return self._guidance.get(mission_id, ())


def _collect_pending_guidance(
    events: Iterable[Mapping[str, Any]],
    consumed_event_ids: set[str],
) -> tuple[str, ...]:
    """Consume guidance events once by event id and return their contents.

    Shared by the HTTP and in-process sources; ``consumed_event_ids`` may be
    a controller-level ledger shared across every runner worker (P3-1c), so
    N workers never inject the same guidance entry twice.
    """
    pending: list[str] = []
    for event in events:
        event_id = str(event.get("event_id") or event.get("eventId") or "")
        if not event_id or event_id in consumed_event_ids:
            continue
        consumed_event_ids.add(event_id)
        if event.get("event_type") != GUIDANCE_EVENT_TYPE:
            continue
        content = (event.get("payload") or {}).get(GUIDANCE_CONTENT_KEY)
        if isinstance(content, str) and content.strip():
            pending.append(content.strip())
    return tuple(pending)


class _GuidanceLedgerSource:
    _consumed_event_ids: set[str]
    _event_limit: int

    @property
    def event_limit(self) -> int:
        return self._event_limit

    def consume_event(self, event_id: str) -> bool:
        if event_id in self._consumed_event_ids:
            return False
        self._consumed_event_ids.add(event_id)
        return True

    def restore_consumed_event_ids(self, event_ids: Sequence[str]) -> None:
        self._consumed_event_ids.update(event_ids)


class MissionControlGuidanceSource(_GuidanceLedgerSource):
    """HTTP adapter over the Mission events feed for guidance events.

    Consumption is tracked in memory by ``event_id``: every guidance event
    is injected once per runner process and never replayed afterwards.
    Failures degrade to "no guidance" — guidance delivery must never break
    an execution loop.
    """

    def __init__(
        self,
        base_url: str,
        *,
        access_token: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        event_limit: int = 200,
        consumed_event_ids: set[str] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token
        self._http_client = http_client
        self._event_limit = event_limit
        self._consumed_event_ids = (
            consumed_event_ids if consumed_event_ids is not None else set()
        )

    async def pending_guidance(self, mission_id: str) -> tuple[str, ...]:
        try:
            events = await self._list_events(mission_id)
        except Exception as exc:  # noqa: BLE001 - guidance is best-effort
            logger.warning(
                "guidance fetch failed for mission %s: %s", mission_id, exc
            )
            return ()
        return _collect_pending_guidance(events, self._consumed_event_ids)

    async def _list_events(self, mission_id: str) -> list[Mapping[str, Any]]:
        events = await self.read_events(mission_id, after_sequence=0)
        return [event for event in events if isinstance(event, Mapping)]

    async def read_events(self, mission_id: str, *, after_sequence: int) -> list[Mapping[str, Any]]:
        headers = (
            {"Authorization": f"Bearer {self._access_token}"}
            if self._access_token
            else {}
        )
        url = (
            f"{self._base_url}/api/v1/missions/{mission_id}/events"
            f"?afterSequence={after_sequence}&limit={self._event_limit}"
        )
        if self._http_client is not None:
            response = await self._http_client.get(url, headers=headers)
        else:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()
        events = payload.get("events") if isinstance(payload, Mapping) else None
        if not isinstance(events, list):
            raise ValueError("guidance events response is malformed")
        return events


class InProcessGuidanceSource(_GuidanceLedgerSource):
    """Read the guidance ledger directly from the in-process Mission repository.

    The desktop runner shares the Mission Control process and database, so
    the HTTP round-trip can be skipped: guidance events are read through the
    same repository the Mission API writes to. Consumption bookkeeping is
    shared through ``consumed_event_ids`` — pass one controller-level set to
    every worker's source (P3-1c). Failures degrade to "no guidance" exactly
    like the HTTP source.
    """

    def __init__(
        self,
        repository_factory: Any = MissionRepository,
        *,
        consumed_event_ids: set[str] | None = None,
        event_limit: int = 200,
    ) -> None:
        self._repository_factory = repository_factory
        self._consumed_event_ids = (
            consumed_event_ids if consumed_event_ids is not None else set()
        )
        self._event_limit = event_limit

    async def pending_guidance(self, mission_id: str) -> tuple[str, ...]:
        try:
            normalized = await self.read_events(mission_id, after_sequence=0)
        except Exception as exc:  # noqa: BLE001 - guidance is best-effort
            logger.warning(
                "guidance fetch failed for mission %s: %s", mission_id, exc
            )
            return ()
        return _collect_pending_guidance(normalized, self._consumed_event_ids)

    async def read_events(self, mission_id: str, *, after_sequence: int) -> list[Mapping[str, Any]]:
        repository = self._repository_factory()
        events = await repository.list_events(mission_id, after_sequence=after_sequence,
                                             limit=self._event_limit)
        return [
            event.to_public_dict() if not isinstance(event, Mapping) else event
            for event in events
        ]


class GuidanceInjectingModel:
    """ModelPort wrapper that injects unconsumed guidance before each call.

    The wrapper mirrors the historical ModelPort call shapes: it forwards
    ``tools_enabled=False`` only for the no-tools summary round, so model
    stubs implementing the plain two-argument signature keep working.
    """

    def __init__(
        self,
        inner: ModelPort,
        source: GuidanceSourcePort,
        *,
        mission_id: str,
    ) -> None:
        self._inner = inner
        self._source = source
        self._mission_id = mission_id
        self.injected_blocks: list[str] = []
        self._recovery_cursor: GuidanceRecoveryCursor | None = None
        self._retry_request: ModelRequest | HarnessRequest | None = None
        self._retry_effective_request: ModelRequest | HarnessRequest | None = None

    def enable_recovery(self, execution: Any) -> None:
        self._recovery_cursor = GuidanceRecoveryCursor(self._source, execution)

    def snapshot_guidance(self) -> GuidanceResumeState:
        if self._recovery_cursor is None:
            raise ValueError("guidance model has no private recovery cursor")
        return self._recovery_cursor.state.model_copy(deep=True)

    def restore_guidance(self, state: GuidanceResumeState | None) -> None:
        if self._recovery_cursor is None:
            raise ValueError("guidance model has no private recovery cursor")
        self._recovery_cursor.restore(state)
        self.injected_blocks = [batch.block for batch in self._recovery_cursor.state.injections]
        self._clear_retry_request()

    async def _pending_guidance(self) -> tuple[str, ...]:
        if self._recovery_cursor is not None:
            return await self._recovery_cursor.pending_guidance()
        return await self._source.pending_guidance(self._mission_id)

    async def complete(
        self,
        request: ModelRequest | HarnessRequest,
        *legacy_args: object,
        **legacy_kwargs: object,
    ) -> ModelResponse:
        request = await self._prepare_request(request)
        response = await self._call_inner(request, legacy_args, legacy_kwargs)
        self._clear_retry_request()
        return response

    async def _prepare_request(self, request: ModelRequest | HarnessRequest):
        if request is self._retry_request:
            return self._retry_effective_request
        original = request
        guidance = await self._pending_guidance()
        if guidance:
            block = format_guidance_block(guidance)
            self.injected_blocks.append(block)
            if isinstance(request, ModelRequest):
                request = replace(
                    request,
                    messages=request.messages
                    + (Message(role="system", content=block, source_id="guidance"),),
                )
            else:
                request = replace(request, code=f"{request.code}\n\n{block}")
        self._retry_request, self._retry_effective_request = original, request
        return request

    def _clear_retry_request(self) -> None:
        self._retry_request = self._retry_effective_request = None

    async def _call_inner(self, request: ModelRequest | HarnessRequest,
                          legacy_args: tuple[object, ...], legacy_kwargs: Mapping[str, object]) -> ModelResponse:
        if not isinstance(request, ModelRequest):
            return await self._inner.complete(  # type: ignore[call-arg]
                request, *legacy_args, **legacy_kwargs
            )
        if legacy_args or legacy_kwargs:
            raise TypeError("canonical guidance call does not accept legacy arguments")
        if _uses_legacy_signature(self._inner.complete):
            legacy_request = HarnessRequest(
                code="\n\n".join(message.content for message in request.messages),
                language=str(request.metadata.get("language") or "text"),
                timeout=request.timeout_seconds,
            )
            return await self._inner.complete(legacy_request, ())  # type: ignore[call-arg]
        return await self._inner.complete(request)

    def stream(self, request: ModelRequest) -> Any:
        """Forward canonical events; guidance remains a typed system message."""
        return self._stream_canonical(request)

    async def _stream_canonical(self, request: ModelRequest) -> Any:
        request = await self._prepare_request(request)
        stream_method = getattr(self._inner, "stream", None)
        if callable(stream_method) and not _uses_legacy_signature(stream_method):
            async for event in stream_method(request):
                yield event
            self._clear_retry_request()
            return
        response = await self._call_inner(request, (), {})
        self._clear_retry_request()
        if response.content:
            yield ModelStreamEvent(kind="text_delta", text=response.content)
        for call in response.tool_calls:
            yield ModelStreamEvent(kind="tool_call", tool_call=call)
        yield ModelStreamEvent(
            kind="completed",
            usage={
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "cost": response.usage.cost,
            },
        )


def _uses_legacy_signature(method: Any) -> bool:
    try:
        return "tool_results" in inspect.signature(method).parameters
    except (TypeError, ValueError):
        return False


__all__ = [
    "GUIDANCE_CONTENT_KEY",
    "GUIDANCE_EVENT_TYPE",
    "GuidanceInjectingModel",
    "GuidanceSourcePort",
    "InMemoryGuidanceSource",
    "InProcessGuidanceSource",
    "MissionControlGuidanceSource",
    "format_guidance_block",
]
