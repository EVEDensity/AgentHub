"""v1 missions/_decisions.py — Decision inbox, listing, resolution."""
from __future__ import annotations

from app.api.v1.missions._deps import *

router = APIRouter()


@router.get("/decisions")
async def list_workspace_decisions(
    workspace_id: WorkspaceId,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    decision_status: DecisionStatusFilter = "PENDING",
    mission_id: DecisionMissionIdFilter = None,
    reason_code: DecisionReasonFilter = None,
    limit: DecisionLimit = 100,
    offset: DecisionOffset = 0,
) -> dict:
    authorize_workspace(user, workspace_id)
    _authorize_human_decision_access(user)
    decisions = await repository.list_workspace_decisions(
        workspace_id,
        status=(None if decision_status == "ALL" else DecisionStatus(decision_status)),
        mission_id=mission_id,
        reason_code=reason_code,
        limit=limit,
        offset=offset,
    )
    return {"decisions": [decision.to_public_dict() for decision in decisions]}

@router.get("/{mission_id}/decisions")
async def list_decisions(
    mission_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    limit: DecisionLimit = 100,
    offset: DecisionOffset = 0,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    decisions = await repository.list_decisions(
        mission_id,
        limit=limit,
        offset=offset,
    )
    return {"decisions": [decision.to_public_dict() for decision in decisions]}

@router.post("/{mission_id}/decisions/{decision_id}/resolve")
async def resolve_decision(
    mission_id: str,
    decision_id: str,
    request: DecisionResolutionRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    _authorize_human_decision_access(user)
    await _authorized_mission(mission_id, user=user, repository=repository)
    service = MissionService(repository)
    try:
        decision, work_unit, mission = await service.resolve_decision(
            mission_id,
            decision_id,
            expected_version=request.expected_version,
            resolution=DecisionResolution(request.resolution),
            rationale=request.rationale,
            actor=build_human_actor(user),
        )
    except DecisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Decision not found") from exc
    except (DecisionConflictError, WorkUnitNotFoundError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "decision": decision.to_public_dict(),
        "workUnit": work_unit.to_public_dict(),
        "mission": mission.to_public_dict(),
    }
