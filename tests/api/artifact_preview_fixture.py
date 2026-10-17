"""Hermetic preview ports; these fakes do not establish persistence evidence."""
from __future__ import annotations

import hashlib

from fastapi import FastAPI

from app.api.v1.missions import (
    get_mission_artifact_byte_exporter,
    get_mission_repository,
    router,
)
from app.services.auth_service import get_current_user
from tests.domain.factories import build_artifact, build_mission

HUMAN = {"id": "workspace-1", "role": "developer", "name": "Ada"}
PREVIEW_PATH = "/api/v1/missions/mis-1/artifacts/artifact-1/preview"


def text_artifact(content: bytes, **updates):
    digest = hashlib.sha256(content).hexdigest()
    return build_artifact(
        **{
            "digest": "sha256:" + digest,
            "content_address": "local:sha256/" + digest,
            "size_bytes": len(content),
            "media_type": "text/plain",
            **updates,
        }
    )


class PreviewRepository:
    def __init__(self, artifact):
        self.mission = build_mission()
        self.artifact = artifact
        self.reads = []

    async def get_mission(self, mission_id):
        self.reads.append(("mission", mission_id))
        return self.mission if self.mission and self.mission.id == mission_id else None

    async def get_artifact(self, artifact_id):
        self.reads.append(("artifact", artifact_id))
        return self.artifact if self.artifact and self.artifact.id == artifact_id else None


class PreviewExporter:
    def __init__(self, content):
        self.content = content
        self.calls = []
        self.error = None

    async def read_verified(self, artifact, *, max_bytes):
        self.calls.append((artifact.id, max_bytes))
        if self.error is not None:
            raise self.error
        return self.content


def preview_app(repository, exporter, *, user=HUMAN):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_mission_repository] = lambda: repository
    app.dependency_overrides[get_mission_artifact_byte_exporter] = lambda: exporter
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return app
