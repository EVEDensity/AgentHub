from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.init_db import now
from app.db.session import afetch_all, aexecute
from app.schemas.artifact import ArtifactCreateRequest, ArtifactResponse
from app.services.auth.service import get_current_user

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


@router.post("")
async def create_artifact(
    data: ArtifactCreateRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Register a file as an artifact.

    If the same ``(session_id, file_path)`` already exists, the version
    number is incremented automatically.
    """
    existing = await afetch_all(
        "SELECT version FROM artifacts WHERE session_id=$1 AND file_path=$2 "
        "ORDER BY version DESC LIMIT 1",
        data.session_id,
        data.file_path,
    )
    version = (existing[0]["version"] + 1) if existing else 1
    artifact_id = str(uuid.uuid4())

    await aexecute(
        "INSERT INTO artifacts(id, session_id, file_path, content, version, created_at) "
        "VALUES($1,$2,$3,$4,$5,$6)",
        artifact_id,
        data.session_id,
        data.file_path,
        data.content,
        version,
        now(),
    )
    return {"id": artifact_id, "version": version, "status": "created"}


@router.get("")
async def list_artifacts(
    session_id: str = Query(...),
) -> list[dict]:
    """List all artifacts for a session (latest version of each file only)."""
    rows = await afetch_all(
        "SELECT DISTINCT ON (file_path) id, session_id, file_path, content, version, created_at "
        "FROM artifacts WHERE session_id=$1 ORDER BY file_path, version DESC",
        session_id,
    )
    return [dict(r) for r in rows]


@router.get("/{artifact_id}")
async def get_artifact(
    artifact_id: str,
) -> dict:
    """Get a single artifact by ID."""
    rows = await afetch_all(
        "SELECT id, session_id, file_path, content, version, created_at "
        "FROM artifacts WHERE id=$1",
        artifact_id,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return dict(rows[0])


@router.get("/file/versions")
async def list_artifact_versions(
    session_id: str = Query(...),
    file_path: str = Query(...),
) -> list[dict]:
    """List all versions of a single artifact file."""
    rows = await afetch_all(
        "SELECT id, session_id, file_path, content, version, created_at "
        "FROM artifacts WHERE session_id=$1 AND file_path=$2 ORDER BY version DESC",
        session_id,
        file_path,
    )
    return [dict(r) for r in rows]
