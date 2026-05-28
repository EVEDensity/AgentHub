"""Master admin router — aggregates all admin sub-module routers."""

from fastapi import APIRouter

from app.api.admin import analytics, audit, chat_defaults, models, roles, users, workflows

router = APIRouter(prefix="/api/admin", tags=["admin"])

router.include_router(models.router)
router.include_router(roles.router)
router.include_router(chat_defaults.router)
router.include_router(workflows.router)
router.include_router(users.router)
router.include_router(audit.router)
router.include_router(analytics.router)
