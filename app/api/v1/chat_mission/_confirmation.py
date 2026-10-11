"""Atomic confirmation adapter over transaction-bound Mission Control commands."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException

from app.api.v1.access import authorize_workspace
from app.api.v1.chat_mission._helpers import (
    _SPECIAL_MENTIONS,
    _apply_chat_rule_targets,
    _build_chat_contract,
    _now_dt,
    _parse_mentions,
    _pick_default_participant,
    _preprocess_archivist,
    _resolve_mentions,
)
from app.api.v1.session_access import require_session_workspace
from app.domain import (
    ActorRef,
    MissionSource,
    PendingConfirmationStatus,
    SessionEvent,
    SessionEventType,
)
from app.services.mission_service import MissionService, build_human_actor


@dataclass(frozen=True)
class _ChatInput:
    message: str
    objective: str
    participants: list[dict]
    special: list[str]


async def _require_pending(tx, pending_id: str, user: dict):
    pending = await tx.pendings.get_pending_for_update(pending_id)
    if pending is None:
        raise HTTPException(status_code=404, detail="pending not found")
    authorize_workspace(user, pending.workspace_id)
    await require_session_workspace(pending.session_id, pending.workspace_id, tx.sessions)
    if pending.status != PendingConfirmationStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"pending is already {pending.status.value}")
    return pending


async def _prepare_input(pending, repository, resolver) -> _ChatInput:
    message = pending.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="message is empty")
    mentions = _parse_mentions(message)
    special = [name for name in mentions if name.lower() in _SPECIAL_MENTIONS]
    names = [name for name in mentions if name.lower() not in _SPECIAL_MENTIONS]
    objective = message
    if "archivist" in [name.lower() for name in special]:
        objective, _ = await _preprocess_archivist(message, repository, pending.workspace_id)
    if pending.objective_template:
        try:
            rule = type("Rule", (), {"id": pending.rule_id, "description": pending.rule_description})()
            objective = pending.objective_template.format(rule=rule)
        except (KeyError, AttributeError):
            objective = pending.objective_template
    resolved, unresolved = await _resolve_mentions(names, pending.workspace_id, resolver)
    if not resolved and not unresolved:
        default = await _pick_default_participant(pending.workspace_id, resolver)
        if default is not None:
            resolved = [default]
    resolved, _ = await _apply_chat_rule_targets(
        resolved, unresolved, names, [pending.target_agent], pending.workspace_id, resolver,
    )
    return _ChatInput(message, objective, resolved, special)


async def _record_confirmation(tx, pending, mission, chat_input: _ChatInput) -> None:
    for event_type, payload in (
        (SessionEventType.DECISION_RECORDED, {"pending_id": pending.id, "rule_id": pending.rule_id, "resolution": "CONFIRMED"}),
        (SessionEventType.MISSION_CREATED, {"mission_id": mission.id, "status": mission.status.value,
                                          "participants": chat_input.participants, "rule_id": pending.rule_id}),
    ):
        await tx.session_events.add_session_event(SessionEvent(
            id=f"evt-{uuid.uuid4().hex[:16]}", session_id=pending.session_id, event_type=event_type,
            actor=ActorRef(type="adapter", id="chat_mission.confirm"), payload=payload, created_at=_now_dt(),
        ))


async def _create_confirmed_mission(tx, pending, user: dict, resolver) -> dict:
    chat_input = await _prepare_input(pending, tx.missions, resolver)
    confirmed = await tx.pendings.resolve_pending(pending.id, PendingConfirmationStatus.CONFIRMED)
    if confirmed is None:
        raise HTTPException(status_code=409, detail="pending confirmation lost its state transition")
    mission_id = f"mis-confirm-{pending.id}"
    service = MissionService(tx.missions, session_event_repository=tx.session_events, agent_binding_resolver=resolver)
    try:
        mission = await service.create_mission(
            mission_id=mission_id, workspace_id=pending.workspace_id,
            title=chat_input.message.splitlines()[0][:80] or "Chat mission (confirmed)", objective=chat_input.objective,
            source=MissionSource(type="chat", session_id=pending.session_id, metadata={
                "participants": chat_input.participants, "unresolved_mentions": [], "special_mentions": chat_input.special,
                "rule_confirm": {"pending_id": pending.id, "rule_id": pending.rule_id, "target_agent": pending.target_agent},
            }), contract=_build_chat_contract(f"contract-{mission_id}"), actor=build_human_actor(user),
        )
        mission = await service.start_mission(mission.id, actor=build_human_actor(user))
        unit = await service.create_chat_work_unit(mission.id, workspace_id=pending.workspace_id)
        await _record_confirmation(tx, pending, mission, chat_input)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "status": "confirmed", "pendingId": pending.id, "missionId": mission.id, "sessionId": pending.session_id,
        "streamUrl": f"/api/v1/missions/{mission.id}/events/stream?maxSeconds=0", "updatedAt": mission.updated_at.isoformat(),
        "dispatch": {"workUnitId": unit.id, "status": unit.status.value, "assignedAgentId": unit.assigned_agent_id,
                     "assignedAdapter": unit.assigned_adapter},
        "mentions": {"resolved": chat_input.participants, "unresolved": [], "special": chat_input.special},
        "rule": {"id": pending.rule_id, "description": pending.rule_description, "targetAgent": pending.target_agent},
    }


async def confirm_chat_pending(pending_id: str, *, user: dict, pending_repo, resolver) -> dict:
    expired = False
    async with pending_repo.transaction() as tx:
        pending = await _require_pending(tx, pending_id, user)
        if pending.expires_at <= datetime.now(UTC):
            expired = await tx.pendings.resolve_pending(pending.id, PendingConfirmationStatus.EXPIRED) is not None
            if not expired:
                raise HTTPException(status_code=409, detail="pending expiry lost its state transition")
        else:
            result = await _create_confirmed_mission(tx, pending, user, resolver)
    if expired:
        raise HTTPException(status_code=410, detail="pending expired")
    return result


async def cancel_chat_pending(pending_id: str, *, user: dict, pending_repo) -> dict:
    expired = False
    async with pending_repo.transaction() as tx:
        pending = await _require_pending(tx, pending_id, user)
        if pending.expires_at <= datetime.now(UTC):
            status = PendingConfirmationStatus.EXPIRED
            expired = True
        else:
            status = PendingConfirmationStatus.CANCELLED
        if await tx.pendings.resolve_pending(pending.id, status) is None:
            raise HTTPException(status_code=409, detail="pending cancellation lost its state transition")
    if expired:
        raise HTTPException(status_code=410, detail="pending expired")
    return {"status": "cancelled", "pendingId": pending.id, "ruleId": pending.rule_id}
