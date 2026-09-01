"""Chat-to-Mission adapter (P0 web chat migration slice, ADR-0108).

Thin endpoint that turns one chat message into a running Mission:
create + start + (inline work unit derivation if no agent is pre-selected)
in one HTTP round-trip.  The caller then opens the SSE stream and renders
events as they arrive — no legacy orchestrator, no WebSocket session.

P1 mention routing (ADR-0109): parse ``@AgentName`` tokens from the
message, resolve each against the workspace Agent Catalog, and record
them as Mission participants so the SSE consumer knows which agents
will contribute.

P0 full migration (this slice): even plain chat messages without a
@mention now route through this adapter.  When no agent is named the
workspace default is picked as the single participant so the Mission
has a clear executor.
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.access import authorize_workspace
from app.db.init_db import now
from app.repositories import MissionRepository
from app.services.agent_binding_service import (
    AgentBindingResolver,
    DatabaseAgentBindingResolver,
)
from app.services.auth_service import get_current_user
from app.services.mission_service import (
    MissionService,
    build_human_actor,
)

router = APIRouter(prefix="/chat", tags=["chat"])

# Matches ``@Identifier`` — identifier is alphanumeric + underscore/dash,
# must be preceded by whitespace or line-start.  This mirrors the
# frontend ``detectMentionTrigger`` semantics so both sides agree.
_MENTION_RE = re.compile(r"(?:^|\s)@([A-Za-z0-9_\-]+)")


class ChatMissionRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda s: s.replace("_", ""),
        extra="forbid",
        populate_by_name=True,
    )

    message: str = Field(min_length=1, max_length=8000)
    workspace_id: str = Field(default="local-admin", alias="workspaceId")
    session_id: str | None = Field(default=None, alias="sessionId")
    stream: bool = True


def get_mission_repository() -> MissionRepository:
    return MissionRepository()


def get_agent_binding_resolver() -> AgentBindingResolver:
    """Return the default DB-backed resolver for Agent bindings.

    Split out so tests can override via dependency injection — keeps
    the endpoint pure for fast unit tests without a live DB.
    """
    return DatabaseAgentBindingResolver()


CurrentUser = Annotated[dict, Depends(get_current_user)]
MissionRepositoryDep = Annotated[MissionRepository, Depends(get_mission_repository)]
BindingResolverDep = Annotated[AgentBindingResolver, Depends(get_agent_binding_resolver)]


def _parse_mentions(message: str) -> list[str]:
    """Return unique agent identifiers found as ``@Name`` tokens."""
    seen: set[str] = set()
    result: list[str] = []
    for m in _MENTION_RE.finditer(message):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


async def _resolve_mentions(
    names: list[str],
    workspace_id: str,
    resolver: AgentBindingResolver,
) -> tuple[list[dict], list[dict]]:
    """Resolve @mention identifiers to Agent Catalog bindings.

    Returns ``(resolved, unresolved)`` where each entry is a dict
    describing the agent.  ``unresolved`` entries are mention tokens
    that didn't match any enabled Agent binding in the workspace —
    the caller surfaces them so the UI can show a hint.
    """
    if not names:
        return [], []

    try:
        all_enabled = await resolver.list_enabled(scope_id=workspace_id)
    except Exception:  # noqa: BLE001 - treat resolver failure as "none found"
        return [], [{"name": n, "reason": "resolver unavailable"} for n in names]

    # Build lookup by agent_id (case-insensitive for fuzzy matching)
    by_id = {b["agent_id"].lower(): b for b in all_enabled}
    # Also match by any display/domain field present
    by_alias: dict[str, dict] = {}
    for b in all_enabled:
        for key in ("domain", "display_name", "name"):
            val = b.get(key)
            if val:
                by_alias[str(val).lower()] = b

    resolved: list[dict] = []
    unresolved: list[dict] = []
    seen_ids: set[str] = set()

    for name in names:
        key = name.lower()
        binding = by_id.get(key) or by_alias.get(key)
        if binding is None:
            unresolved.append({"name": name, "reason": "not found in workspace catalog"})
            continue
        agent_id = binding["agent_id"]
        if agent_id in seen_ids:
            continue
        seen_ids.add(agent_id)
        resolved.append({
            "agentId": agent_id,
            "adapterType": binding.get("adapter_type", "unknown"),
            "capabilities": binding.get("capabilities", []),
        })

    return resolved, unresolved


async def _pick_default_participant(
    workspace_id: str,
    resolver: AgentBindingResolver,
) -> dict | None:
    """Return the first enabled Agent binding as default participant.

    Used when the chat message has no ``@mention`` — every Mission needs
    at least one executor.  Returns ``None`` if the workspace has no
    enabled agents at all (the Mission still starts but work unit
    derivation will surface an empty executor error).
    """
    try:
        all_enabled = await resolver.list_enabled(scope_id=workspace_id)
    except Exception:  # noqa: BLE001
        return None
    if not all_enabled:
        return None
    first = all_enabled[0]
    return {
        "agentId": first["agent_id"],
        "adapterType": first.get("adapter_type", "unknown"),
        "capabilities": first.get("capabilities", []),
    }


def _build_chat_contract(contract_id: str) -> dict[str, Any]:
    """Minimal contract for chat-originated Missions.

    Kept tiny on purpose — chat is a conversational surface, not a
    deterministic verification pipeline.  A proper contract builder will
    be extracted once we converge on Mission contract policies across
    all sources.
    """
    return {
        "id": contract_id,
        "version": 1,
        "repositoryScopes": [],
        "allowedCapabilities": [],
        "budgets": {"timeSeconds": 600, "modelCost": 5, "retries": 1},
        "acceptanceCriteria": [
            {
                "id": "chat-response",
                "kind": "manual",
                "description": "Chat Mission acceptance — agent responds to the user's message.",
                "required": True,
                "configuration": {},
            }
        ],
        "decisionGates": [],
        "forbiddenActions": [],
    }


async def _inline_derive_work_units(mission_id: str) -> None:
    """Best-effort inline work unit derivation for chat Missions.

    Chat Missions need work units immediately so the SSE stream carries
    meaningful events.  The desktop runner's derivation loop (which also
    handles chat-source Missions now that the filter has been widened)
    will pick this up on its next tick, but running it inline eliminates
    the idle gap between Mission start and the first ``work_unit.started``
    event.
    """
    try:
        from app.services.runner.loops import (
            DesktopLocalMissionSource,
            derive_desktop_task_work_units,
        )

        source = DesktopLocalMissionSource()
        await derive_desktop_task_work_units(
            source,
            workspace_id="__any__",  # derive_desktop_task_work_units filters by source.type in running_manual_missions
        )
    except Exception:  # noqa: BLE001 - derivation failure is non-fatal
        # The desktop runner loop will retry on its next interval.
        pass


@router.post("/mission", status_code=202)
async def create_chat_mission(
    request: ChatMissionRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    resolver: BindingResolverDep,
) -> dict:
    """Create and start a Mission from one chat message.

    Full P0 migration: **every** chat message (with or without
    ``@mention``) routes through this adapter.  ``@mention`` tokens
    are resolved against the workspace Agent Catalog; messages
    without mentions fall back to the workspace's default Agent.

    Returns ``missionId``, ``streamUrl``, and the mention resolution
    result.  The caller opens the SSE stream at
    ``GET /api/v1/missions/{missionId}/events/stream`` to consume the
    event ledger as it arrives.
    """
    authorize_workspace(user, request.workspace_id)

    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="message is required")

    # ── P1: @mention parsing & resolution ──────────────────────────
    mention_names = _parse_mentions(message)
    resolved, unresolved = await _resolve_mentions(
        mention_names,
        request.workspace_id,
        resolver,
    )

    # ── P0: No mention → pick default agent as participant ──────────
    if not resolved:
        default = await _pick_default_participant(
            request.workspace_id, resolver
        )
        if default is not None:
            resolved = [default]

    mission_id = f"mis-chat-{uuid.uuid4().hex[:12]}"
    title = message.splitlines()[0][:80] or "Chat mission"
    contract_id = f"contract-chat-{uuid.uuid4().hex[:12]}"

    service = MissionService(repository)
    try:
        mission = await service.create_mission(
            mission_id=mission_id,
            workspace_id=request.workspace_id,
            title=title,
            objective=message,
            source={
                "type": "chat",
                "session_id": request.session_id,
                "created_at": now().isoformat(),
                "participants": resolved,
                "unresolved_mentions": unresolved,
            },
            contract=_build_chat_contract(contract_id),
            actor=build_human_actor(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Start immediately — the web chat surface expects a running mission.
    try:
        await service.start_mission(
            mission_id=mission_id,
            actor=build_human_actor(user),
        )
    except Exception as exc:  # noqa: BLE001 - start failures surface cleanly
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # ── P0: Inline work unit derivation ─────────────────────────────
    # Creates a desktop.task WorkUnit immediately so the SSE stream
    # has meaningful events (work_unit.started, evidence.recorded...)
    # instead of just mission.lifecycle.started.  Best-effort; the
    # desktop runner's loop will retry on its interval if this fails.
    await _inline_derive_work_units(mission_id)

    stream_url = (
        f"/api/v1/missions/{mission_id}/events/stream?maxSeconds=0"
    )

    return {
        "missionId": mission.id,
        "status": mission.status.value,
        "streamUrl": stream_url,
        "updatedAt": mission.updated_at.isoformat(),
        "mentions": {
            "resolved": resolved,
            "unresolved": unresolved,
        },
    }
