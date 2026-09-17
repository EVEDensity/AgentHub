"""Pure helpers + Pydantic models + dep overrides for chat_mission.

Everything the handlers depend on that does not need a FastAPI path-op
decorator: mention parsing, default participant selection, contract
building, archivist preprocessing, dependency overrides for test injection.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.access import authorize_workspace
from app.db.init_db import now
from app.domain import (
    ActorRef,
    MissionContract,
    MissionSource,
    MissionSourceType,
    PendingConfirmation,
    PendingConfirmationStatus,
    Session,
    SessionEvent,
    SessionEventType,
    SessionStatus,
)
from app.repositories import (
    MissionRepository,
    PendingConfirmationRepository,
    SessionEventRepository,
    SessionRepository,
)
from app.services.agent_binding_service import (
    AgentBindingResolver,
    DatabaseAgentBindingResolver,
)
from app.services.auth_service import get_current_user
from app.services.mission_service import (
    MissionService,
    build_human_actor,
)
from app.services.receipts import (
    format_receipts_as_context,
    search_receipts_inprocess,
)
from app.services.rule_engine import (
    RuleHit,
    RuleSyntaxError,
    AgentRule,
    discover_rules_file,
    evaluate_rules,
    get_or_create_rules_cache,
    load_rules,
)

router = APIRouter(prefix="/chat", tags=["chat"])

# ── Special (non-catalog) mentions ────────────────────────────────
# These tokens are routed by the adapter itself — the adapter runs
# pre-processing and injects context before the Mission is created.
# They do NOT need to be registered in the Agent Catalog.
_SPECIAL_MENTIONS = frozenset({"archivist"})

# Matches ``@Identifier`` — identifier is alphanumeric + underscore/dash,
# must be preceded by whitespace or line-start.  This mirrors the
# frontend ``detectMentionTrigger`` semantics so both sides agree.
_MENTION_RE = re.compile(r"(?:^|\s)@([A-Za-z0-9_\-]+)")


def _now_dt() -> datetime:
    """Return the current UTC time as a timezone-aware datetime.

    ``app.db.init_db.now`` returns an ISO-format string, but AwareDatetime
    model fields require a real datetime object.  Use this helper for any
    field that feeds into a Pydantic model.
    """
    return datetime.now(timezone.utc)


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
    rules_yaml: str | None = Field(default=None, alias="rulesYaml")


class ConfirmPendingRequest(BaseModel):
    """Body for POST /chat/confirm (T5 rule confirmation gate)."""

    model_config = ConfigDict(populate_by_name=True)

    pending_id: str = Field(..., alias="pendingId")


class CancelPendingRequest(BaseModel):
    """Body for POST /chat/cancel (T5 rule confirmation gate)."""

    model_config = ConfigDict(populate_by_name=True)

    pending_id: str = Field(..., alias="pendingId")

def get_mission_repository() -> MissionRepository:
    return MissionRepository()


def get_session_event_repository() -> SessionEventRepository:
    """Return the default DB-backed SessionEventRepository.

    Split out for testability — tests inject a fake to observe which
    session events the endpoint emits.
    """
    return SessionEventRepository()


def get_session_repository() -> SessionRepository:
    """Return the default DB-backed SessionRepository (T3)."""
    return SessionRepository()


def get_agent_binding_resolver() -> AgentBindingResolver:
    """Return the default DB-backed resolver for Agent bindings.

    Split out so tests can override via dependency injection — keeps
    the endpoint pure for fast unit tests without a live DB.
    """
    return DatabaseAgentBindingResolver()


def get_pending_confirmation_repository() -> PendingConfirmationRepository:
    """Return the default DB-backed PendingConfirmationRepository (T5)."""
    return PendingConfirmationRepository()


CurrentUser = Annotated[dict, Depends(get_current_user)]
MissionRepositoryDep = Annotated[MissionRepository, Depends(get_mission_repository)]
SessionEventRepoDep = Annotated[SessionEventRepository, Depends(get_session_event_repository)]
SessionRepoDep = Annotated[SessionRepository, Depends(get_session_repository)]
BindingResolverDep = Annotated[AgentBindingResolver, Depends(get_agent_binding_resolver)]
PendingRepoDep = Annotated[
    PendingConfirmationRepository, Depends(get_pending_confirmation_repository)
]

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


def _build_chat_contract(contract_id: str) -> MissionContract:
    """Minimal contract for chat-originated Missions.

    Kept tiny on purpose — chat is a conversational surface, not a
    deterministic verification pipeline.  A proper contract builder will
    be extracted once we converge on Mission contract policies across
    all sources.
    """
    return MissionContract.model_validate({
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
    })


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


async def _preprocess_archivist(
    message: str,
    repo: MissionRepository,
    workspace_id: str,
) -> tuple[str, list[dict]]:
    """Run receipts pre-search for ``@archivist`` and return enriched context.

    Extracts the query from the message (everything after ``@archivist``),
    runs an in-process receipts search over the workspace's Mission
    history, and returns ``(enriched_objective, receipts_list)``.  The
    enriched objective prepends a markdown block describing the evidence
    trail so the downstream agent answers **with provenance**, not by
    free-associating from the history.

    Returns the original message unchanged if no receipts are found —
    the agent still answers but says "no matching records".
    """
    # Strip ``@archivist`` and surrounding whitespace → pure query
    query = re.sub(r"@archivist\b", "", message, flags=re.IGNORECASE).strip()
    if not query:
        query = "all missions"

    try:
        receipts = await search_receipts_inprocess(
            repo,
            workspace_id=workspace_id,
            query=query,
            limit=10,
            days=90,
        )
    except Exception:  # noqa: BLE001 - search failure is non-fatal
        receipts = []

    context_block = format_receipts_as_context(receipts, query=query)
    enriched = f"{context_block}\n\n---\n\n{message}" if receipts else message
    return enriched, receipts