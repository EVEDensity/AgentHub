from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status

from .config import RunnerServiceSettings
from .runtime import RunnerServiceRuntime, build_runner_runtime

RuntimeFactory = Callable[[RunnerServiceSettings], RunnerServiceRuntime]


def create_app(
    settings: RunnerServiceSettings | None = None,
    *,
    runtime_factory: RuntimeFactory = build_runner_runtime,
) -> FastAPI:
    """Create the operational surface without loading secrets at import time."""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        configuration = settings or RunnerServiceSettings()  # type: ignore[call-arg]
        runtime = runtime_factory(configuration)
        application.state.runner_runtime = runtime
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
        title="agenthub-runner-service",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @application.get("/healthz")
    async def healthz(response: Response) -> dict[str, object]:
        runtime = getattr(application.state, "runner_runtime", None)
        healthy = isinstance(runtime, RunnerServiceRuntime) and runtime.healthy
        if not healthy:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "ok" if healthy else "unhealthy",
            "service": "agenthub-runner-service",
        }

    @application.get("/readyz")
    async def readyz(response: Response) -> dict[str, object]:
        runtime = getattr(application.state, "runner_runtime", None)
        ready = isinstance(runtime, RunnerServiceRuntime) and runtime.ready
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        payload: dict[str, object] = {
            "status": "ready" if ready else "not-ready",
            "service": "agenthub-runner-service",
        }
        if isinstance(runtime, RunnerServiceRuntime):
            payload["worker"] = runtime.snapshot.to_public_dict()
        return payload

    return application


app = create_app()


__all__ = ["app", "create_app"]
