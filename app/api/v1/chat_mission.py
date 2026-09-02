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
from app.domain import (
    ActorRef,
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


@router.post("/mission", status_code=202)
async def create_chat_mission(
    request: ChatMissionRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    session_events: SessionEventRepoDep,
    sessions: SessionRepoDep,
    resolver: BindingResolverDep,
    pending_repo: PendingRepoDep,
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

    # ── T3: Auto-create session when client doesn't provide one ────
    # Before T3 every chat_mission request needed a pre-existing
    # session_id.  Now we create one on-the-fly so single-shot chat
    # requests still emit a full session event stream.  Best-effort
    # like every other persistence step: session creation failure does
    # not block the Mission.
    session_id = request.session_id
    if not session_id:
        try:
            now_ts = now()
            chat_title = message.splitlines()[0][:80] or "Chat session"
            new_session = Session(
                id=f"sess-{uuid.uuid4().hex[:12]}",
                workspace_id=request.workspace_id,
                title=chat_title,
                status=SessionStatus.ACTIVE,
                created_by=build_human_actor(user),
                created_at=now_ts,
                updated_at=now_ts,
            )
            await sessions.add_session(new_session)
            session_id = new_session.id
        except Exception:  # noqa: BLE001 - observe, never block
            session_id = None

    async def _emit(
        event_type: SessionEventType,
        payload: dict | None = None,
        *,
        actor_override: ActorRef | None = None,
    ) -> None:
        if not session_id:
            return  # no session → no event log
        try:
            evt = SessionEvent(
                id=f"evt-{uuid.uuid4().hex[:16]}",
                session_id=session_id,
                event_type=event_type,
                actor=actor_override or build_human_actor(user),
                payload=payload or {},
                created_at=now(),
            )
            await session_events.add_session_event(evt)
        except Exception:  # noqa: BLE001 - observe, never block
            pass

    # Emit message.created immediately — this is the anchor event
    # for the whole chat_mission chain.
    await _emit(SessionEventType.MESSAGE_CREATED, payload={
        "content": message[:500],
        "has_archivist": any(m.lower() == "archivist" for m in _parse_mentions(message)),
    })

    # ── P1: @mention parsing & resolution ──────────────────────────
    mention_names = _parse_mentions(message)

    # ── T1-2: Special mentions (archivist) ──────────────────────────
    # These are NOT resolved against the Agent Catalog — the adapter
    # itself runs pre-processing and injects context before the Mission
    # is created.  We strip them from mention_names so resolution below
    # only sees real agent identifiers.
    special_hit = [m for m in mention_names if m.lower() in _SPECIAL_MENTIONS]
    mention_names = [m for m in mention_names if m.lower() not in _SPECIAL_MENTIONS]

    if mention_names or special_hit:
        await _emit(SessionEventType.MENTION_DETECTED, payload={
            "names": mention_names + special_hit,
            "resolved_count": None,  # filled after _resolve_mentions
        })

    enriched_objective = message
    archivist_receipts: list[dict] = []
    if "archivist" in [m.lower() for m in special_hit]:
        enriched_objective, archivist_receipts = await _preprocess_archivist(
            message, repository, request.workspace_id,
        )

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

    # ── T1-1 + T4: Rule engine evaluation ────────────────────────
    # Priority for rule source:
    #   1. Client-supplied ``rulesYaml`` (explicit, always wins)
    #   2. Auto-discovered ``.agenthub/rules.yaml`` (T4 hot-reload)
    #   3. No rules at all (opt-in default)
    rules: list[AgentRule] = []
    rules_yaml_error: str | None = None

    if request.rules_yaml:
        # Client-supplied — parse directly (no caching)
        try:
            rules = load_rules(request.rules_yaml)
        except RuleSyntaxError as exc:
            rules_yaml_error = str(exc)
    else:
        # T4: Auto-discover project rules.yaml with hot-reload cache.
        try:
            # Try workspace_root first (desktop/runner context), then cwd.
            ws_root = None
            try:
                from app.services.workspace_context import get_workspace_root
                ws_root = get_workspace_root()
            except Exception:  # noqa: BLE001 - workspace context optional
                pass
            rules_path = discover_rules_file(ws_root)
            if rules_path is not None:
                cache = get_or_create_rules_cache(rules_path)
                rules, rules_yaml_error = cache.get_rules()
        except Exception as exc:  # noqa: BLE001 - rules must never block
            rules_yaml_error = f"auto-load failed: {exc}"

    # Evaluate and emit rule.triggered events (best-effort).
    rules_hit: list[RuleHit] = []
    if rules and not rules_yaml_error:
        rules_hit = evaluate_rules(rules, message)
        for hit in rules_hit:
            await _emit(SessionEventType.RULE_TRIGGERED, payload={
                "rule_id": hit.rule.id,
                "description": hit.rule.description,
                "action_kind": hit.rule.action.kind,
                "requires_confirmation": hit.rule.action.require_confirmation,
            })

    # ── T5: Rule confirmation gate ─────────────────────────────────
    # If any matched rule has ``require_confirmation: true`` AND
    # ``action.kind: create_mission`` we pause here, persist a pending
    # record, and return 202 + pending status.  The frontend then shows
    # a confirmation dialog; the user's choice flows through
    # POST /chat/confirm or POST /chat/cancel.
    #
    # Rationale (multi-agent-collaboration.md §11): rules are
    # defensive by default.  Only an explicit ``require_confirmation:
    # false`` (owner-approved) goes straight to Mission creation.
    pending_create_mission = [
        h for h in rules_hit
        if h.rule.action.kind == "create_mission"
        and h.rule.action.require_confirmation
    ]
    if pending_create_mission:
        primary = pending_create_mission[0].rule
        pending_id = f"pc-{uuid.uuid4().hex[:12]}"
        now_ts = now()
        # Default expiry: 15 min — configurable later via rule.yaml
        from datetime import timedelta
        expires_at = now_ts + timedelta(minutes=15)

        try:
            pending = PendingConfirmation(
                id=pending_id,
                session_id=session_id,
                workspace_id=request.workspace_id,
                rule_id=primary.id,
                rule_description=primary.description,
                action_kind=primary.action.kind,
                target_agent=primary.action.target_agent,
                objective_template=primary.action.objective_template,
                message=message,
                request_payload={
                    "workspace_id": request.workspace_id,
                    "session_id": session_id,
                    "stream": request.stream,
                },
                status=PendingConfirmationStatus.PENDING,
                created_by=build_human_actor(user),
                expires_at=expires_at,
                created_at=now_ts,
            )
            await pending_repo.add_pending(pending)
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            # Pending storage failed → fall through to regular Mission
            # creation (same behavior as pre-T5).  Log-only on production.
            import logging
            logging.getLogger("agenthub.chat_mission").warning(
                "pending storage failed, proceeding with Mission: %s", exc,
            )
        else:
            return {
                "status": "pending",
                "pendingId": pending_id,
                "reason": "rule_requires_confirmation",
                "ruleId": primary.id,
                "ruleDescription": primary.description,
                "sessionId": session_id,
                "rulesHit": [
                    {
                        "ruleId": h.rule.id,
                        "description": h.rule.description,
                        "kind": h.rule.action.kind,
                        "targetAgent": h.rule.action.target_agent,
                        "requiresConfirmation": h.rule.action.require_confirmation,
                    }
                    for h in rules_hit
                ],
                "rulesYamlError": rules_yaml_error,
            }

    # Apply rule-driven overrides (best-effort; never fatal).
    # If a matched rule has ``kind: create_mission`` with an
    # ``objective_template``, enrich the objective using template
    # substitution.  ``{rule.id}`` and ``{rule.description}`` are
    # available in the template.
    if rules_hit:
        rule_overrides = [
            h for h in rules_hit
            if h.rule.action.kind == "create_mission"
            and h.rule.action.objective_template
        ]
        if rule_overrides:
            # Take the first matching rule's template; later rules would
            # make the objective noisy anyway.
            primary = rule_overrides[0].rule
            try:
                enriched_objective = primary.action.objective_template.format(
                    rule=primary,
                )
            except (KeyError, AttributeError):
                enriched_objective = primary.action.objective_template

    mission_id = f"mis-chat-{uuid.uuid4().hex[:12]}"
    title = message.splitlines()[0][:80] or "Chat mission"
    contract_id = f"contract-chat-{uuid.uuid4().hex[:12]}"

    service = MissionService(repository, session_event_repository=session_events)
    try:
        mission = await service.create_mission(
            mission_id=mission_id,
            workspace_id=request.workspace_id,
            title=title,
            objective=enriched_objective,
            source=MissionSource(
                type=MissionSourceType.CHAT,
                session_id=request.session_id,
                metadata={
                    "created_at": now().isoformat(),
                    "participants": resolved,
                    "unresolved_mentions": unresolved,
                    "special_mentions": special_hit,
                    "archivist": {
                        "query": (
                            re.sub(r"@archivist\b", "", message, flags=re.IGNORECASE).strip()
                            or "all missions"
                        ),
                        "receipts_count": len(archivist_receipts),
                    } if archivist_receipts else None,
                    "rules": {
                        "total": len(rules),
                        "hits": [h.rule.id for h in rules_hit],
                        "hit_requires_confirmation": any(
                            h.rule.action.require_confirmation for h in rules_hit
                        ),
                        "hit_auto_execute": any(
                            not h.rule.action.require_confirmation for h in rules_hit
                        ),
                    } if rules_hit else None,
                },
            ),
            contract=_build_chat_contract(contract_id),
            actor=build_human_actor(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await _emit(SessionEventType.MISSION_CREATED, payload={
        "mission_id": mission.id,
        "status": mission.status.value,
        "participants": resolved,
        "has_unresolved": bool(unresolved),
        "rules_hit": [h.rule.id for h in rules_hit],
    }, actor_override=ActorRef(type="adapter", id="chat_mission"))

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
            "special": special_hit,
        },
        "archivist": {
            "query": (
                re.sub(r"@archivist\b", "", message, flags=re.IGNORECASE).strip()
                or "all missions"
            ),
            "receipts": archivist_receipts[:5],  # top 5 receipts inline; rest live in Mission objective
        } if special_hit and any(m.lower() == "archivist" for m in special_hit) else None,
        "rulesHit": [
            {
                "ruleId": h.rule.id,
                "description": h.rule.description,
                "kind": h.rule.action.kind,
                "targetAgent": h.rule.action.target_agent,
                "requiresConfirmation": h.rule.action.require_confirmation,
            }
            for h in rules_hit
        ] or None,
        "rulesYamlError": rules_yaml_error,
    }


# ═══════════════════════════════════════════════════════════════════════
# T5: Rule confirmation gate — POST /chat/confirm and /chat/cancel
# ═══════════════════════════════════════════════════════════════════════


@router.post("/confirm", status_code=202)
async def confirm_pending(
    request: ConfirmPendingRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    session_events: SessionEventRepoDep,
    sessions: SessionRepoDep,
    resolver: BindingResolverDep,
    pending_repo: PendingRepoDep,
) -> dict:
    """Confirm a rule-triggered pending record → create the Mission.

    Fetches the pending record, transitions it to ``CONFIRMED``, then
    replays the message through the normal Mission-creation pipeline
    (mention resolution, archivist preprocessing, Mission lifecycle).
    Rule evaluation is **skipped** — the rule has already passed the
    user's confirmation gate, so we proceed straight to execution.
    """
    pending = await pending_repo.get_pending(request.pending_id)
    if pending is None:
        raise HTTPException(status_code=404, detail="pending not found")

    # Authorize
    authorize_workspace(user, pending.workspace_id)

    # Reject non-pending states
    if pending.status != PendingConfirmationStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"pending is already {pending.status.value}",
        )

    from datetime import datetime, timezone
    if pending.expires_at < datetime.now(timezone.utc):
        # Auto-expire on confirm attempt
        await pending_repo.resolve_pending(
            pending.id, PendingConfirmationStatus.EXPIRED,
        )
        raise HTTPException(status_code=410, detail="pending expired")

    # ── Transition to CONFIRMED ───────────────────────────────────
    resolved = await pending_repo.resolve_pending(
        pending.id, PendingConfirmationStatus.CONFIRMED,
    )

    message = pending.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="message is empty")

    session_id = pending.session_id

    async def _emit(
        event_type: SessionEventType,
        payload: dict | None = None,
        *,
        actor_override: ActorRef | None = None,
    ) -> None:
        if not session_id:
            return
        try:
            evt = SessionEvent(
                id=f"evt-{uuid.uuid4().hex[:16]}",
                session_id=session_id,
                event_type=event_type,
                actor=actor_override or build_human_actor(user),
                payload=payload or {},
                created_at=now(),
            )
            await session_events.add_session_event(evt)
        except Exception:  # noqa: BLE001 - observe, never block
            pass

    # Emit message.created if we have a session (it may have been created
    # by the original chat_mission call — the session_id is preserved).
    await _emit(SessionEventType.MESSAGE_CREATED, payload={
        "content": message[:500],
        "has_archivist": "@archivist" in message.lower(),
        "source": "rule_confirm",
    })

    # Emit a confirm-specific event so the SSE stream knows a rule was
    # approved (distinct from rule.triggered which was already emitted
    # by the original chat_mission call).
    await _emit(SessionEventType.DECISION_RECORDED, payload={
        "pending_id": pending.id,
        "rule_id": pending.rule_id,
        "resolution": "CONFIRMED",
    })

    # ── Mention parsing & resolution ───────────────────────────────
    mention_names = _parse_mentions(message)
    special_hit = [m for m in mention_names if m.lower() in _SPECIAL_MENTIONS]
    mention_names = [m for m in mention_names if m.lower() not in _SPECIAL_MENTIONS]

    if mention_names or special_hit:
        await _emit(SessionEventType.MENTION_DETECTED, payload={
            "names": mention_names + special_hit,
            "source": "rule_confirm",
        })

    enriched_objective = message
    archivist_receipts: list[dict] = []
    if "archivist" in [m.lower() for m in special_hit]:
        enriched_objective, archivist_receipts = await _preprocess_archivist(
            message, repository, pending.workspace_id,
        )

    # Rule-driven objective enrichment (already evaluated → apply directly)
    if pending.objective_template:
        try:
            enriched_objective = pending.objective_template.format(
                rule=type("_R", (), {"id": pending.rule_id, "description": pending.rule_description})(),
            )
        except (KeyError, AttributeError):
            enriched_objective = pending.objective_template

    resolved, unresolved = await _resolve_mentions(
        mention_names, pending.workspace_id, resolver,
    )
    if not resolved:
        default = await _pick_default_participant(pending.workspace_id, resolver)
        if default is not None:
            resolved = [default]

    # ── Create & start Mission ─────────────────────────────────────
    mission_id = f"mis-confirm-{uuid.uuid4().hex[:12]}"
    title = message.splitlines()[0][:80] or "Chat mission (confirmed)"
    contract_id = f"contract-confirm-{uuid.uuid4().hex[:12]}"

    service = MissionService(repository, session_event_repository=session_events)
    try:
        mission = await service.create_mission(
            mission_id=mission_id,
            workspace_id=pending.workspace_id,
            title=title,
            objective=enriched_objective,
            source=MissionSource(
                type=MissionSourceType.CHAT,
                session_id=session_id,
                metadata={
                    "created_at": now().isoformat(),
                    "participants": resolved,
                    "unresolved_mentions": unresolved,
                    "special_mentions": special_hit,
                    "rule_confirm": {
                        "pending_id": pending.id,
                        "rule_id": pending.rule_id,
                        "target_agent": pending.target_agent,
                    },
                },
            ),
            contract=_build_chat_contract(contract_id),
            actor=build_human_actor(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await _emit(SessionEventType.MISSION_CREATED, payload={
        "mission_id": mission.id,
        "status": mission.status.value,
        "participants": resolved,
        "has_unresolved": bool(unresolved),
        "rule_id": pending.rule_id,
    }, actor_override=ActorRef(type="adapter", id="chat_mission.confirm"))

    # Start + inline work units
    try:
        await service.start_mission(
            mission_id=mission_id,
            actor=build_human_actor(user),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await _inline_derive_work_units(mission_id)

    stream_url = f"/api/v1/missions/{mission_id}/events/stream?maxSeconds=0"

    return {
        "status": "confirmed",
        "pendingId": pending.id,
        "missionId": mission.id,
        "streamUrl": stream_url,
        "updatedAt": mission.updated_at.isoformat(),
        "mentions": {
            "resolved": resolved,
            "unresolved": unresolved,
            "special": special_hit,
        },
        "rule": {
            "id": pending.rule_id,
            "description": pending.rule_description,
            "targetAgent": pending.target_agent,
        },
    }


@router.post("/cancel", status_code=200)
async def cancel_pending(
    request: CancelPendingRequest,
    user: CurrentUser,
    pending_repo: PendingRepoDep,
) -> dict:
    """Cancel a pending rule-trigger record — no Mission is created."""
    pending = await pending_repo.get_pending(request.pending_id)
    if pending is None:
        raise HTTPException(status_code=404, detail="pending not found")

    authorize_workspace(user, pending.workspace_id)

    if pending.status != PendingConfirmationStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"pending is already {pending.status.value}",
        )

    await pending_repo.resolve_pending(
        pending.id, PendingConfirmationStatus.CANCELLED,
    )

    return {
        "status": "cancelled",
        "pendingId": pending.id,
        "ruleId": pending.rule_id,
    }
