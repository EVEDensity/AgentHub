"""Run-time guidance injection for the desktop local runner (P1-1).

Users can push extra guidance into a RUNNING Mission through the Mission
API (``POST /missions/{id}/guidance`` → ``mission.guidance.added`` event).
The runner consumes it without stopping the execution: before every model
call the request-scoped :class:`GuidanceInjectingModel` asks its
:class:`GuidanceSourcePort` for unconsumed guidance and appends it to the
prompt. Each guidance entry is injected exactly once — consumption is
tracked per mission by event id (the runner-side view of the append-only
event ledger), so a retry of the same entry never happens within one
runner process.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, Protocol

import httpx

from app.services.harness_service import (
    FunctionResult,
    HarnessRequest,
    ModelPort,
    ModelResponse,
)

logger = logging.getLogger("agenthub.desktop_guidance")

GUIDANCE_EVENT_TYPE = "mission.guidance.added"
GUIDANCE_CONTENT_KEY = "content"

_GUIDANCE_BLOCK_HEADER = "[用户补充指导 · 运行中注入]"


def format_guidance_block(guidance: Sequence[str]) -> str:
    """Render unconsumed guidance entries as one prompt block."""
    lines = "\n".join(f"- {item}" for item in guidance)
    return f"{_GUIDANCE_BLOCK_HEADER}\n{lines}"


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


class MissionControlGuidanceSource:
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
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token
        self._http_client = http_client
        self._event_limit = event_limit
        self._consumed_event_ids: set[str] = set()

    async def pending_guidance(self, mission_id: str) -> tuple[str, ...]:
        try:
            events = await self._list_events(mission_id)
        except Exception as exc:  # noqa: BLE001 - guidance is best-effort
            logger.warning(
                "guidance fetch failed for mission %s: %s", mission_id, exc
            )
            return ()
        pending: list[str] = []
        for event in events:
            event_id = str(event.get("event_id") or event.get("eventId") or "")
            if not event_id or event_id in self._consumed_event_ids:
                continue
            self._consumed_event_ids.add(event_id)
            if event.get("event_type") != GUIDANCE_EVENT_TYPE:
                continue
            content = (event.get("payload") or {}).get(GUIDANCE_CONTENT_KEY)
            if isinstance(content, str) and content.strip():
                pending.append(content.strip())
        return tuple(pending)

    async def _list_events(self, mission_id: str) -> list[Mapping[str, Any]]:
        headers = (
            {"Authorization": f"Bearer {self._access_token}"}
            if self._access_token
            else {}
        )
        url = (
            f"{self._base_url}/api/v1/missions/{mission_id}/events"
            f"?afterSequence=0&limit={self._event_limit}"
        )
        if self._http_client is not None:
            response = await self._http_client.get(url, headers=headers)
        else:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers)
        if response.is_error:
            return []
        payload = response.json()
        events = payload.get("events") if isinstance(payload, Mapping) else None
        if not isinstance(events, list):
            return []
        return [event for event in events if isinstance(event, Mapping)]


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

    async def complete(
        self,
        request: HarnessRequest,
        tool_results: tuple[FunctionResult, ...],
        *,
        tools_enabled: bool = True,
    ) -> ModelResponse:
        guidance = await self._source.pending_guidance(self._mission_id)
        if guidance:
            block = format_guidance_block(guidance)
            self.injected_blocks.append(block)
            request = replace(request, code=f"{request.code}\n\n{block}")
        if tools_enabled:
            return await self._inner.complete(request, tool_results)
        return await self._inner.complete(request, tool_results, tools_enabled=False)


__all__ = [
    "GUIDANCE_CONTENT_KEY",
    "GUIDANCE_EVENT_TYPE",
    "GuidanceInjectingModel",
    "GuidanceSourcePort",
    "InMemoryGuidanceSource",
    "MissionControlGuidanceSource",
    "format_guidance_block",
]
