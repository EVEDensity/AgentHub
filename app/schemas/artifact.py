from __future__ import annotations

from pydantic import BaseModel


class ArtifactCreateRequest(BaseModel):
    session_id: str
    file_path: str
    content: str


class ArtifactResponse(BaseModel):
    id: str
    session_id: str
    file_path: str
    content: str
    version: int
    created_at: str
