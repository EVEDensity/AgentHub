"""v1 missions/_lifecycle.py — Mission lifecycle — start, cancel, guidance."""
from __future__ import annotations

from app.api.v1.missions._deps import *

router = APIRouter()


@router.post("/{mission_id}/start")
async def start_mission(
    mission_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    return await _run_lifecycle_command(
        mission_id,
        command="start",
        user=user,
        repository=repository,
    )

@router.post("/{mission_id}/cancel")
async def cancel_mission(
    mission_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    return await _run_lifecycle_command(
        mission_id,
        command="cancel",
        user=user,
        repository=repository,
    )

@router.post("/{mission_id}/guidance")
async def add_mission_guidance(
    mission_id: str,
    request: MissionGuidanceRequest,
    user: CurrentUser,
    repository: MissionRepositoryDep,
) -> dict:
    """Push one run-time guidance entry into a Mission (P1-1).

    The entry is stored as a ``mission.guidance.added`` event; the desktop
    runner injects it into the model prompt before the next model call.
    """
    await _authorized_mission(mission_id, user=user, repository=repository)
    service = MissionService(repository)
    try:
        event = await service.add_mission_guidance(
            mission_id,
            content=request.content,
            actor=build_human_actor(user),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return event.to_public_dict()
