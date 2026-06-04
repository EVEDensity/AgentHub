from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import api_router
from app.config import APP_NAME, APP_VERSION
from app.db.init_db import ainit_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging
    _log = logging.getLogger("agenthub.startup")

    # ── Database init ────────────────────────────────────────────────
    _log.info("startup: initializing PostgreSQL...")
    try:
        await ainit_db()
        _log.info("startup: PostgreSQL initialized")
    except Exception:
        _log.exception("startup: PostgreSQL init FAILED")
        raise

    # Register built-in tools for the tool-calling system
    try:
        from app.services.tools import register_builtin_tools
        count = register_builtin_tools()
        _log.info("startup: registered %d built-in tools", count)
    except Exception:
        _log.warning("startup: register_builtin_tools failed — tools will be unavailable", exc_info=True)

    # Initialize enhanced function-calling system
    try:
        from app.services.tools import initialize_tool_system
        streaming_executor = await initialize_tool_system()
        app.state.streaming_executor = streaming_executor
        _log.info("startup: enhanced function-calling system initialized")
    except Exception:
        _log.warning(
            "startup: initialize_tool_system failed — agents will use simple parallel execution",
            exc_info=True,
        )

    yield

    # ── Shutdown: close DB pool ─────────────────────────────────────
    try:
        from app.db.session import aclose_pool
        await aclose_pool()
        _log.info("shutdown: PostgreSQL pool closed")
    except Exception:
        _log.warning("shutdown: failed to close PostgreSQL pool", exc_info=True)

    # ── Shutdown: close shared HTTP client ──────────────────────────
    try:
        from app.services.adapter_manager import close_http_client
        await close_http_client()
        _log.info("shutdown: shared HTTP client closed")
    except Exception:
        _log.warning("shutdown: failed to close HTTP client", exc_info=True)


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
