"""v1 missions/_artifacts.py — Artifact, evidence, and changed-file listing."""
from __future__ import annotations

from app.api.v1.missions._deps import *

router = APIRouter()


@router.get("/{mission_id}/changed-files")
async def list_mission_changed_files(
    mission_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    workspace_root: DesktopExecutionWorkspaceRootDep,
) -> dict:
    """Disclose the change set a desktop task produced in HEAD (G7).

    Only ``desktop.task`` Missions have a desktop execution workspace; other
    Missions report an empty change set.
    """
    await _authorized_mission(mission_id, user=user, repository=repository)
    work_units = await repository.list_work_units(mission_id)
    if not any(
        unit.kind == DESKTOP_TASK_WORK_UNIT_KIND for unit in work_units
    ):
        return {"files": []}
    return {"files": collect_desktop_changed_files(workspace_root)}

@router.get("/{mission_id}/evidence")
async def list_evidence(
    mission_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    limit: EvidenceLimit = 100,
    offset: EvidenceOffset = 0,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    evidence = await repository.list_evidence(
        mission_id,
        limit=limit,
        offset=offset,
    )
    return {"evidence": [item.to_public_dict() for item in evidence]}

@router.get("/{mission_id}/artifacts")
async def list_artifacts(
    mission_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    limit: ArtifactLimit = 100,
    offset: ArtifactOffset = 0,
) -> dict:
    await _authorized_mission(mission_id, user=user, repository=repository)
    artifacts = await repository.list_artifacts(
        mission_id,
        limit=limit,
        offset=offset,
    )
    return {"artifacts": [artifact.to_public_dict() for artifact in artifacts]}
