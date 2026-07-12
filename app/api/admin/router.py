"""Master admin router — aggregates all admin sub-module routers."""

from fastapi import APIRouter

from app.api.admin import analytics, audit, chat_defaults, models, rbac, roles, tools, users, workflows
from app.api.admin.mcp import router as mcp_router

router = APIRouter(prefix="/api/admin", tags=["admin"])

router.include_router(models.router)
router.include_router(roles.router)
router.include_router(rbac.router)
router.include_router(chat_defaults.router)
router.include_router(workflows.router)
router.include_router(tools.router)
router.include_router(users.router)
router.include_router(audit.router)
router.include_router(analytics.router)
router.include_router(mcp_router)
