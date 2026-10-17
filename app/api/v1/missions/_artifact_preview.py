"""Human-only read of one registered Mission Artifact's verified text."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.v1.missions._deps import CurrentUser, MissionRepositoryDep, _authorized_mission
from app.schemas.artifact_preview import ArtifactTextPreview
from app.services.artifact_integrity_service import ArtifactByteExporter, build_artifact_byte_verifier
from app.services.artifact_preview_service import ArtifactPreviewError, read_artifact_text_preview

router = APIRouter()
_PREVIEW_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def get_mission_artifact_byte_exporter() -> ArtifactByteExporter:
    return build_artifact_byte_verifier()


MissionArtifactByteExporterDep = Annotated[
    ArtifactByteExporter, Depends(get_mission_artifact_byte_exporter),
]


@router.get(
    "/{mission_id}/artifacts/{artifact_id}/preview",
    response_model=ArtifactTextPreview,
)
async def preview_mission_artifact(
    mission_id: str,
    artifact_id: str,
    user: CurrentUser,
    repository: MissionRepositoryDep,
    exporter: MissionArtifactByteExporterDep,
    response: Response,
) -> ArtifactTextPreview:
    try:
        if user.get("role") in {"runner", "verifier", "agent", "service"}:
            raise HTTPException(403, "Human Artifact preview access required")
        await _authorized_mission(mission_id, user=user, repository=repository)
        artifact = await repository.get_artifact(artifact_id)
        if artifact is None or artifact.mission_id != mission_id:
            raise HTTPException(404, "Artifact not found")
        result = await read_artifact_text_preview(artifact, exporter=exporter)
    except ArtifactPreviewError as exc:
        raise HTTPException(exc.status_code, exc.detail, headers=_PREVIEW_HEADERS) from exc
    except HTTPException as exc:
        exc.headers = {**(exc.headers or {}), **_PREVIEW_HEADERS}
        raise
    response.headers.update(_PREVIEW_HEADERS)
    return result
