"""v1 missions router — thin assembly layer."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.missions._artifacts import router as artifacts_router
from app.api.v1.missions._crud import router as crud_router
from app.api.v1.missions._decisions import router as decisions_router
from app.api.v1.missions._events_stream import router as events_stream_router
from app.api.v1.missions._lifecycle import router as lifecycle_router
from app.api.v1.missions._work_units import router as work_units_router

router = APIRouter(tags=["missions"])

# IMPORTANT include order must mirror the original missions.py top-down route
# declaration order. Static single-segment paths (e.g. GET /decisions) must
# come before parameterised single-segment paths (e.g. GET /{mission_id}),
# otherwise Starlette will greedily bind the parameter route first.
for sub_router in (
    decisions_router,
    crud_router,
    lifecycle_router,
    events_stream_router,
    artifacts_router,
    work_units_router,
):
    router.include_router(sub_router, prefix="/missions")
