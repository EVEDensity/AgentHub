from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status

from .config import DecisionExpiryServiceSettings
from .runtime import (
    DecisionExpiryServiceRuntime,
    build_decision_expiry_runtime,
)

RuntimeFactory = Callable[
    [DecisionExpiryServiceSettings],
    DecisionExpiryServiceRuntime,
]


def create_app(
    settings: DecisionExpiryServiceSettings | None = None,
    *,
    runtime_factory: RuntimeFactory = build_decision_expiry_runtime,
) -> FastAPI:
    """Create operational probes without initializing the database at import."""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        configuration = settings or DecisionExpiryServiceSettings()
        runtime = runtime_factory(configuration)
        application.state.decision_expiry_runtime = runtime
        try:
            await runtime.start()
        except BaseException:
            await runtime.stop()
            raise
        try:
            yield
        finally:
            await runtime.stop()

    application = FastAPI(
        title="agenthub-decision-expiry-service",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @application.get("/healthz")
    async def healthz(response: Response) -> dict[str, object]:
        runtime = getattr(application.state, "decision_expiry_runtime", None)
        healthy = (
            isinstance(runtime, DecisionExpiryServiceRuntime) and runtime.healthy
        )
        if not healthy:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "ok" if healthy else "unhealthy",
            "service": "agenthub-decision-expiry-service",
        }

    @application.get("/readyz")
    async def readyz(response: Response) -> dict[str, object]:
        runtime = getattr(application.state, "decision_expiry_runtime", None)
        ready = isinstance(runtime, DecisionExpiryServiceRuntime) and runtime.ready
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        payload: dict[str, object] = {
            "status": "ready" if ready else "not-ready",
            "service": "agenthub-decision-expiry-service",
        }
        if isinstance(runtime, DecisionExpiryServiceRuntime):
            payload["worker"] = runtime.snapshot.to_public_dict()
        return payload

    return application


app = create_app()


__all__ = ["app", "create_app"]
