from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.routes import api_router
from app.core.config import get_settings
from app.db.init_db import ainit_db

# Single import at module level — lazy-init, validated once.
_cfg = get_settings()


class _BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Content-Length exceeds *max_bytes* *before*
    reading the body, preventing memory-exhaustion attacks."""

    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > _cfg.max_body_mb * 1024 * 1024:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": (
                                f"Request body too large. "
                                f"Max {_cfg.max_body_mb} MB allowed."
                            )
                        },
                    )
            except ValueError:
                pass
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging
    _log = logging.getLogger("agenthub.startup")

    _log.info(
        "startup: env=%s cors_origins=%s body_limit_mb=%d orchestrator=%s",
        _cfg.env,
        _cfg.cors_origins,
        _cfg.max_body_mb,
        "enabled" if _cfg.orchestrator.preprocess_enabled else "disabled",
    )

    # ── Secret validation ────────────────────────────────────────────
    from app.services.secret_service import validate_secret
    validate_secret()

    # ── Database init ────────────────────────────────────────────────
    _log.info("startup: initializing PostgreSQL...")
    try:
        await ainit_db()
        _log.info("startup: PostgreSQL initialized")
    except Exception as exc:
        _log.error(
            "startup: PostgreSQL init FAILED — %s: %s",
            type(exc).__name__, exc,
        )
        _log.error(
            "startup: Check that PostgreSQL is running at the URL in your .env file.\n"
            "  - Run start.bat to auto-start PostgreSQL via Docker, or\n"
            "  - Start manually: docker start agenthub-pg\n"
            "  - Or use a Neon cloud free tier: https://neon.tech"
        )
        raise RuntimeError(
            "PostgreSQL connection failed. "
            "Start the database (e.g., 'docker start agenthub-pg') and retry."
        ) from exc

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


app = FastAPI(title=_cfg.app_name, version=_cfg.app_version, lifespan=lifespan)

# ── Middleware (last added wraps outermost) ──────────────────────────
app.add_middleware(_BodySizeLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cfg.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "AgentHub backend is running", "docs": "/docs", "version": _cfg.app_version}
