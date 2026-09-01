from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    agent,
    artifacts,
    auth,
    canvas,
    chat,
    files,
    git,
    im,
    settings,
    skills,
    system,
    tasks,
    user,
    workspace,
)
from app.api.admin import router as admin_router
from app.api.v1 import router as v1_router

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(im.router)
api_router.include_router(auth.router)
api_router.include_router(user.router)
api_router.include_router(canvas.router)
api_router.include_router(chat.router)
api_router.include_router(admin_router)
api_router.include_router(agent.router)
api_router.include_router(files.router)
api_router.include_router(git.router)
api_router.include_router(workspace.router)
api_router.include_router(tasks.router)
api_router.include_router(skills.router, prefix="/api")
api_router.include_router(settings.router)
api_router.include_router(artifacts.router)
api_router.include_router(v1_router)
