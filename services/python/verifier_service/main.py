from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status

from .config import VerifierServiceSettings
from .runtime import VerifierServiceRuntime, build_verifier_runtime

RuntimeFactory = Callable[[VerifierServiceSettings], VerifierServiceRuntime]


def create_app(
    settings: VerifierServiceSettings | None = None,
    *,
    runtime_factory: RuntimeFactory = build_verifier_runtime,
) -> FastAPI:
    """Create the operational surface without loading secrets at import time."""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        configuration = settings or VerifierServiceSettings()  # type: ignore[call-arg]
        runtime = runtime_factory(configuration)
        application.state.verifier_runtime = runtime
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
        title="agenthub-verifier-service",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @application.get("/healthz")
    async def healthz(response: Response) -> dict[str, object]:
        runtime = getattr(application.state, "verifier_runtime", None)
        healthy = isinstance(runtime, VerifierServiceRuntime) and runtime.healthy
        if not healthy:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "ok" if healthy else "unhealthy",
            "service": "agenthub-verifier-service",
        }

    @application.get("/readyz")
    async def readyz(response: Response) -> dict[str, object]:
        runtime = getattr(application.state, "verifier_runtime", None)
        ready = isinstance(runtime, VerifierServiceRuntime) and runtime.ready
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        payload: dict[str, object] = {
            "status": "ready" if ready else "not-ready",
            "service": "agenthub-verifier-service",
        }
        if isinstance(runtime, VerifierServiceRuntime):
            payload["worker"] = runtime.snapshot.to_public_dict()
        return payload

    return application


app = create_app()


__all__ = ["app", "create_app"]
