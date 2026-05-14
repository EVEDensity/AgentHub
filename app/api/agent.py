from __future__ import annotations

from fastapi import APIRouter

from app.db.session import dict_rows

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.get("/registry")
async def registry() -> list[dict]:
    return dict_rows("SELECT agent_id AS agentId,domain,status,adapter_type AS adapterType,risk_level AS riskLevel FROM agent_registry ORDER BY agent_id")
