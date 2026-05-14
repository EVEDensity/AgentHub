from __future__ import annotations

from fastapi import APIRouter

from app.api import admin, agent, auth, chat, git, im, system, tasks, websocket

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(im.router)
api_router.include_router(auth.router)
api_router.include_router(chat.router)
api_router.include_router(admin.router)
api_router.include_router(agent.router)
api_router.include_router(git.router)
api_router.include_router(tasks.router)
api_router.include_router(websocket.router)
