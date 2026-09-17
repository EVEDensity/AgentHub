"""Path operation handlers for chat_mission routes.

Three endpoints on router prefix ``/chat``.  Imports all public names
from ``_helpers`` — avoids re-declaring the import block.
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
    ActorType,
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

from app.api.v1.chat_mission._helpers import (
    ChatMissionRequest,
    ConfirmPendingRequest,
    CancelPendingRequest,
    router,
    _MENTION_RE,
    _SPECIAL_MENTIONS,
    _now_dt,
    _parse_mentions,
    _resolve_mentions,
    _pick_default_participant,
    _build_chat_contract,
    _inline_derive_work_units,
    _preprocess_archivist,
    get_mission_repository,
    get_session_event_repository,
    get_session_repository,
    get_agent_binding_resolver,
    get_pending_confirmation_repository,
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

    # ── T3: Auto-create session when client doesn't provide one ────
    # Before T3 every chat_mission request needed a pre-existing
    # session_id.  Now we create one on-the-fly so single-shot chat
    # requests still emit a full session event stream.  Best-effort
    # like every other persistence step: session creation failure does
    # not block the Mission.
    session_id = request.session_id
    if not session_id:
        try:
            _ts = _now_dt()
            chat_title = message.splitlines()[0][:80] or "Chat session"
            new_session = Session(
                id=f"sess-{uuid.uuid4().hex[:12]}",
                workspace_id=request.workspace_id,
                title=chat_title,
                status=SessionStatus.ACTIVE,
                created_by=build_human_actor(user),
                created_at=_ts,
                updated_at=_ts,
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
        mission = await service.start_mission(
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
                created_at=_now_dt(),
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
    if not resolved and not unresolved:
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
                    "created_at": now(),
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
        mission = await service.start_mission(
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


# ═══════════════════════════════════════════════════════════════════════
# Orchestration — DAG-based multi-Agent execution (enterprise feature)
# ═══════════════════════════════════════════════════════════════════════

# Late imports to avoid circular deps with _helpers.py
from app.services.orchestrator import (
    OrchestrationNode,
    OrchestrationPlan,
    OrchestratorService,
)
from app.services.orchestrator_gateway import _SyncRunnerGateway


class OrchestrateRequest(BaseModel):
    """Request body for :router.post:`/orchestrate`.

    Two modes supported:

    **Mode A — plan as data** (explicit DAG)::

        {
          "mode": "plan",
          "nodes": [
            {"id": "analyst", "agent_id": "planner",
             "objective": "列出需要修改的文件", "file_claims": ["docs/**"]},
            {"id": "frontend", "agent_id": "dev",
             "objective": "前端组件", "depends_on": ["analyst"],
             "file_claims": ["frontend/src/**"]},
            {"id": "backend", "agent_id": "dev",
             "objective": "后端 handler", "depends_on": ["analyst"],
             "file_claims": ["src/api/**"]},
            {"id": "integrate", "agent_id": "tester",
             "objective": "集成测试", "depends_on": ["frontend", "backend"]},
          ]
        }

    **Mode B — natural-language intent** (we derive a simple DAG)::

        {
          "mode": "intent",
          "objective": "先让分析师分析需求，然后前端和后端并行开发，最后集成测试"
        }
    """

    mode: str = "plan"  # "plan" | "intent"
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    objective: str | None = None


@router.post("/orchestrate", status_code=202)
async def orchestrate_mission(
    request: OrchestrateRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    session_events: SessionEventRepoDep,
) -> dict[str, Any]:
    """Launch a multi-Agent DAG.

    This is the entry point the VSCode extension calls when the user says
    "use DAG / run pipeline / parallelise frontend and backend", or when
    ``@orchestrator`` is mentioned.  The response includes the DAG summary
    (root nodes, join points, parallel groups) so the extension can render
    the topology before waiting for actual execution.
    """
    # 1. Parse or derive the plan
    if request.mode == "plan" and request.nodes:
        try:
            plan = OrchestrationPlan.from_dict({"nodes": request.nodes})
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    elif request.mode == "intent" and request.objective:
        plan = _derive_plan_from_intent(request.objective)
    else:
        raise HTTPException(
            status_code=422,
            detail="provide either nodes (mode=plan) or objective (mode=intent)",
        )

    # 2. Summary for the VSCode extension
    summary = {
        "root_nodes": list(plan.root_ids),
        "parallel_groups": [list(g) for g in plan.parallel_groups],
        "join_points": list(plan.join_ids),
        "node_count": len(plan.nodes),
    }

    # 3. Build gateway + orchestrator — but DON'T actually run in the
    #    request handler.  Real execution is fire-and-forget; the DAG
    #    engine needs event-loop access which we yield to the worker.
    #    We record the plan + emit an event so the extension sees it.
    mission_id = f"mis-dag-{len(plan.nodes)}n-{getattr(user, 'id', user.get('id', 'anon') if isinstance(user, dict) else 'anon')}"
    from app.domain import (
        ActorRef, ActorType, SessionEvent, SessionEventType,
    )
    from datetime import datetime, timezone

    dag_event = SessionEvent(
        id=f"evt-dag-plan-{mission_id}",
        session_id=f"sess-dag-{mission_id[-8:]}",  # synthetic; real session is a separate concern
        event_type=SessionEventType("rule.triggered"),  # closest existing type
        actor=ActorRef(type=ActorType.AGENT, id=f"orchestrator:{mission_id}"),
        payload={
            "_source": "orchestrator",
            "_dag_mission_id": mission_id,
            "_dag_mode": request.mode,
            "plan": [n.id for n in plan.nodes],
            "summary": summary,
        },
        created_at=datetime.now(timezone.utc),
    )
    try:
        await session_events.append(dag_event)
    except Exception:  # noqa: BLE001 - event write is best-effort
        pass

    return {
        "missionId": mission_id,
        "status": "DAG_PLANNED",
        "summary": summary,
        "mode": request.mode,
    }


def _derive_plan_from_intent(objective: str) -> OrchestrationPlan:
    """Derive a simple sequential-or-branching plan from natural language.

    This is a heuristic placeholder — enterprise users who need real
    DAG topology should send ``mode=plan`` with explicit nodes.  The
    intent-mode derivation is: detect keywords → build a two-wave DAG.
    """
    import re

    text = objective.lower()
    # Detect a "parallel" hint
    parallel_hint = bool(re.search(r"并行|同时|parallel|\|\|", text))

    analyst = OrchestrationNode(
        id="analyst",
        agent_id="planner",
        objective=f"分析需求: {objective[:80]}",
        file_claims=("docs/**",),
    )
    dev_front = OrchestrationNode(
        id="frontend",
        agent_id="dev",
        objective="前端实现",
        depends_on=("analyst",),
        file_claims=("frontend/**",),
    )
    dev_back = OrchestrationNode(
        id="backend",
        agent_id="dev",
        objective="后端实现",
        depends_on=("analyst",),
        file_claims=("src/api/**",),
    )
    integrate = OrchestrationNode(
        id="integrate",
        agent_id="tester",
        objective="集成测试 + 端到端验证",
        depends_on=("frontend", "backend"),
        file_claims=("tests/**",),
    )

    if parallel_hint:
        return OrchestrationPlan(nodes=(analyst, dev_front, dev_back, integrate))

    # No parallel hint → simple sequential
    dev_front_seq = OrchestrationNode(
        id="dev", agent_id="dev", objective="实现",
        depends_on=("analyst",), file_claims=("src/**", "frontend/**"),
    )
    return OrchestrationPlan(nodes=(analyst, dev_front_seq, integrate))