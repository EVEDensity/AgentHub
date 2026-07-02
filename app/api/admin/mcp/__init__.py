"""Management Control Panel (MCP) — centralized operations dashboard.

Sub-modules:
  dashboard   System overview metrics aggregation
  agents      Agent live monitor (status, stats, cancel)
  sessions    Session manager (list, detail, force-close, cleanup)
  tasks       Task & workflow monitor (running tasks, DAG progress)
  config      System configuration management
  tools       Tool analytics & permission rules
  alerts      Alert rules & history
  database    Database health & storage management
"""

from fastapi import APIRouter

from app.api.admin.mcp import agents, alerts, config, dashboard, database, sessions, tasks, tools

router = APIRouter(prefix="/mcp", tags=["admin-mcp"])

router.include_router(dashboard.router)
router.include_router(agents.router)
router.include_router(sessions.router)
router.include_router(tasks.router)
router.include_router(config.router)
router.include_router(tools.router)
router.include_router(alerts.router)
router.include_router(database.router)
