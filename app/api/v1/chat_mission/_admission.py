"""Atomic direct chat admission using the existing transaction companions."""

import uuid

from fastapi import HTTPException

from app.api.v1.chat_mission._helpers import _now_dt
from app.api.v1.session_access import require_session_workspace
from app.domain import ActorRef, SessionEvent, SessionEventType
from app.services.mission_service import MissionService


async def admit_chat_mission(pending_repo, *, resolver, command: dict, rules_hit: list[str]):
    source = command["source"]
    workspace = command["workspace_id"]
    try:
        async with pending_repo.transaction() as tx:
            await require_session_workspace(source.session_id, workspace, tx.sessions)
            service = MissionService(tx.missions, session_event_repository=tx.session_events,
                                     agent_binding_resolver=resolver)
            mission = await service.create_mission(**command)
            mission = await service.start_mission(mission.id, actor=command["actor"])
            unit = await service.create_chat_work_unit(mission.id, workspace_id=workspace)
            await tx.session_events.add_session_event(SessionEvent(
                id=f"evt-{uuid.uuid4().hex[:16]}", session_id=source.session_id,
                event_type=SessionEventType.MISSION_CREATED,
                actor=ActorRef(type="adapter", id="chat_mission"), created_at=_now_dt(),
                payload={"mission_id": mission.id, "status": mission.status.value,
                         "participants": source.metadata["participants"], "has_unresolved": False,
                         "rules_hit": rules_hit},
            ))
        return mission, unit
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="chat admission persistence unavailable") from exc
