from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import api_router
from app.config import APP_NAME, APP_VERSION
from app.db.init_db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging
    _log = logging.getLogger("agenthub.startup")

    init_db()

    # Register built-in tools for the tool-calling system
    try:
        from app.services.tools import register_builtin_tools
        count = register_builtin_tools()
        _log.info("startup: registered %d built-in tools", count)
    except Exception:
        _log.warning("startup: register_builtin_tools failed — tools will be unavailable", exc_info=True)

    # Initialize enhanced function-calling system
    # (permission manager, hook manager, streaming executor, etc.)
    try:
        from app.services.tools import initialize_tool_system
        streaming_executor = initialize_tool_system()
        app.state.streaming_executor = streaming_executor
        _log.info("startup: enhanced function-calling system initialized")
    except Exception:
        _log.warning(
            "startup: initialize_tool_system failed — agents will use simple parallel execution",
            exc_info=True,
        )

    yield


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "AgentHub backend is running", "docs": "/docs", "version": APP_VERSION}
