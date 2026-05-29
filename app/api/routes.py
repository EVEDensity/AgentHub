from __future__ import annotations

from fastapi import APIRouter

from app.api import agent, auth, canvas, chat, files, git, im, memory, settings, skills, system, tasks, websocket
from app.api.admin import router as admin_router

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(memory.router)
api_router.include_router(im.router)
api_router.include_router(auth.router)
api_router.include_router(canvas.router)
api_router.include_router(chat.router)
api_router.include_router(admin_router)
api_router.include_router(agent.router)
api_router.include_router(files.router)
api_router.include_router(git.router)
api_router.include_router(tasks.router)
api_router.include_router(websocket.router)
api_router.include_router(skills.router)
api_router.include_router(settings.router)
