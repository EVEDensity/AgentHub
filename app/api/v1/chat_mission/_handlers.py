"""Path operation handlers for chat_mission routes.

Three endpoints on router prefix ``/chat``.  Imports all public names
from ``_helpers`` — avoids re-declaring the import block.
"""

from __future__ import annotations

import re
import uuid
from datetime import timedelta
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.api.v1.access import authorize_workspace
from app.db.init_db import now
from app.domain import (
    ActorRef,
    MissionSource,
    MissionSourceType,
    PendingConfirmation,
    PendingConfirmationStatus,
    SessionEvent,
    SessionEventType,
)
from app.services.mission_service import (
    MissionService,
    build_human_actor,
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

from app.api.v1.chat_mission._helpers import (
    ChatMissionRequest,
    ConfirmPendingRequest,
    CancelPendingRequest,
    router,
    _SPECIAL_MENTIONS,
    _now_dt,
    _parse_mentions,
    _resolve_mentions,
    _pick_default_participant,
    _build_chat_contract,
    _apply_chat_rule_targets,
    _ensure_chat_session,
    _inline_derive_work_units,
    _preprocess_archivist,
    CurrentUser,
    MissionRepositoryDep,
    SessionEventRepoDep,
    SessionRepoDep,
    BindingResolverDep,
    PendingRepoDep,
)


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

    # Session identity must come from durable workspace ownership.
    session_id = await _ensure_chat_session(request.session_id, request.workspace_id, message, user, sessions)

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
                created_at=_now_dt(),
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
    # Only auto-pick default when NO @mention was written at all.
    if not resolved and not unresolved:
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

    resolved, unresolved = await _apply_chat_rule_targets(
        resolved, unresolved, mention_names,
        [hit.rule.action.target_agent for hit in rules_hit
         if hit.rule.action.kind == "create_mission" and hit.rule.action.target_agent],
        request.workspace_id, resolver,
    )

    # ── Subscribe trigger (reply_only) ─────────────────────────────
    # A rule with action.kind == "reply_only" means an Agent is
    # passively listening to the conversation and auto-replies when
    # its trigger matches.  We emit a MESSAGE_CREATED event with
    # actor=target_agent so the SSE stream surfaces the reply in-line.
    # This is the "订阅触发" path described in multi-agent-collab §3.
    reply_only_hits = [
        h for h in rules_hit if h.rule.action.kind == "reply_only"
    ]
    for hit in reply_only_hits:
        agent_name = hit.rule.action.target_agent or hit.rule.id
        agent_actor = ActorRef.model_validate({
            "type": "agent",
            "id": f"agent:{agent_name}",
            "displayName": agent_name,
        })
        reply_body = (
            f"规则 [{hit.rule.id}] 匹配：{hit.rule.description}。\n"
            f"（订阅触发 · 自动回复）"
        )
        await _emit(
            SessionEventType.MESSAGE_CREATED,
            payload={
                "content": reply_body,
                "rule_id": hit.rule.id,
                "auto_generated": True,
                "trigger": "subscribe",
            },
            actor_override=agent_actor,
        )

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
        _ts = _now_dt()
        # Default expiry: 15 min — configurable later via rule.yaml
        expires_at = _ts + timedelta(minutes=15)

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
                created_at=_ts,
            )
            await pending_repo.add_pending(pending)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="chat confirmation persistence unavailable") from exc
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

    from app.api.v1.chat_mission._admission import admit_chat_mission

    mission, work_unit = await admit_chat_mission(
        pending_repo, resolver=resolver, rules_hit=[hit.rule.id for hit in rules_hit],
        command=dict(
            mission_id=mission_id,
            workspace_id=request.workspace_id,
            title=title,
            objective=enriched_objective,
            source=MissionSource(
                type=MissionSourceType.CHAT,
                session_id=session_id,
                metadata={
                    "created_at": now(),
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
    )

    stream_url = (
        f"/api/v1/missions/{mission_id}/events/stream?maxSeconds=0"
    )

    return {
        "missionId": mission.id,
        "sessionId": session_id,
        "dispatch": {"workUnitId": work_unit.id, "status": work_unit.status.value,
                     "assignedAgentId": work_unit.assigned_agent_id, "assignedAdapter": work_unit.assigned_adapter},
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
    request: ConfirmPendingRequest, user: CurrentUser, repository: MissionRepositoryDep,
    session_events: SessionEventRepoDep, sessions: SessionRepoDep, resolver: BindingResolverDep,
    pending_repo: PendingRepoDep,
) -> dict:
    """Confirm and dispatch one Mission atomically behind the pending row lock."""
    from app.api.v1.chat_mission._confirmation import confirm_chat_pending
    try:
        return await confirm_chat_pending(request.pending_id, user=user, pending_repo=pending_repo, resolver=resolver)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="chat confirmation persistence unavailable") from exc


@router.post("/cancel", status_code=200)
async def cancel_pending(request: CancelPendingRequest, user: CurrentUser, pending_repo: PendingRepoDep) -> dict:
    """Cancel or expire a pending rule without creating work."""
    from app.api.v1.chat_mission._confirmation import cancel_chat_pending
    return await cancel_chat_pending(request.pending_id, user=user, pending_repo=pending_repo)


# Multi-Agent execution stays unavailable until it creates durable Missions.
class OrchestrateRequest(BaseModel):
    """Compatibility request shape for the unsupported orchestration command."""

    mode: str = "plan"
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    objective: str | None = None


@router.post("/orchestrate", status_code=501)
async def orchestrate_mission(request: OrchestrateRequest, user: CurrentUser) -> dict[str, Any]:
    """Reject execution without inventing a Mission ID or result event."""
    raise HTTPException(
        status_code=501,
        detail="multi-Agent orchestration is not implemented; use /chat/mission with one catalog executor",
    )
