from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.core.config import ArtifactStoreSettings
from app.services.artifact_store_service import ContentAddressedArtifactPublisher
from app.services.mcp_tool_adapter import StatelessMCPClient
from app.services.runner_composition import build_a2a_inbound_runner
from app.services.runner_service import MissionControlRunnerClient
from app.services.runner_worker import RunnerWorker, RunnerWorkerSnapshot

from .bindings import LoggingMCPAuditPort, PerAttemptMCPBindingFactory
from .config import (
    RunnerServiceSettings,
    load_mcp_binding_manifest,
    read_secret_file,
)
from .gateway import OpenAICompatibleModelFactory


class RunnerWorkerPort(Protocol):
    @property
    def snapshot(self) -> RunnerWorkerSnapshot: ...

    async def run(self) -> None: ...

    def request_stop(self) -> None: ...


class AsyncClosePort(Protocol):
    async def aclose(self) -> None: ...


@dataclass(slots=True)
class RunnerServiceRuntime:
    """Own process resources around a non-persistent Runner worker."""

    worker: RunnerWorkerPort
    shutdown_timeout_seconds: float
    closeables: Sequence[AsyncClosePort] = ()
    _worker_task: asyncio.Task[None] | None = field(default=None, init=False)

    @property
    def snapshot(self) -> RunnerWorkerSnapshot:
        return self.worker.snapshot

    @property
    def healthy(self) -> bool:
        task = self._worker_task
        return task is not None and not task.done()

    @property
    def ready(self) -> bool:
        snapshot = self.snapshot
        return self.healthy and snapshot.running and snapshot.ready

    async def start(self) -> None:
        if self._worker_task is not None:
            raise RuntimeError("Runner service runtime is already started")
        self._worker_task = asyncio.create_task(
            self.worker.run(),
            name="agenthub-runner-worker",
        )
        await asyncio.sleep(0)
        if self._worker_task.done():
            await self._worker_task
            raise RuntimeError("Runner worker stopped during startup")

    async def stop(self) -> None:
        task = self._worker_task
        try:
            if task is not None:
                self.worker.request_stop()
                try:
                    await asyncio.wait_for(
                        asyncio.shield(task),
                        timeout=self.shutdown_timeout_seconds,
                    )
                except TimeoutError:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        finally:
            for closeable in reversed(tuple(self.closeables)):
                await closeable.aclose()


def build_runner_runtime(settings: RunnerServiceSettings) -> RunnerServiceRuntime:
    """Compose the strict single-Mission service without runtime fallbacks."""

    control_token = read_secret_file(settings.mission_control_token_file)
    model_token = read_secret_file(settings.model_gateway_token_file)
    mcp_token = read_secret_file(settings.mcp_token_file)
    manifest = load_mcp_binding_manifest(settings.mcp_bindings_file)

    timeout = httpx.Timeout(settings.http_timeout_seconds)
    limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
    control_http = httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=False,
    )
    model_http = httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=False,
    )
    mcp_http = httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=False,
    )

    control = MissionControlRunnerClient(
        settings.mission_control_url,
        access_token=control_token,
        http_client=control_http,
    )
    model_factory = OpenAICompatibleModelFactory(
        model_http,
        endpoint=settings.model_gateway_url,
        access_token=model_token,
        model=settings.model,
        timeout_seconds=settings.http_timeout_seconds,
        max_response_bytes=settings.max_model_response_bytes,
        max_output_tokens=settings.model_max_output_tokens,
        system_prompt=settings.system_prompt,
        prompt_token_cost=settings.prompt_token_cost,
        completion_token_cost=settings.completion_token_cost,
    )
    mcp_client = StatelessMCPClient(
        settings.mcp_endpoint,
        http_client=mcp_http,
        access_token=mcp_token,
        timeout=settings.http_timeout_seconds,
        audit=LoggingMCPAuditPort(),
    )
    binding_factory = PerAttemptMCPBindingFactory(mcp_client, manifest)
    publisher = ContentAddressedArtifactPublisher(
        ArtifactStoreSettings(
            backend="local",
            local_root=settings.artifact_local_root,
            publish_max_bytes=settings.max_artifact_bytes,
        )
    )
    runner = build_a2a_inbound_runner(
        control,
        publisher=publisher,
        model_factory=model_factory,
        binding_factory=binding_factory,
        runner_id=settings.runner_id,
        assigned_agent_id=settings.assigned_agent_id,
        assigned_adapter=settings.assigned_adapter,
        max_context_chars=settings.max_context_chars,
        max_timeout_seconds=settings.max_work_unit_timeout_seconds,
        max_iterations=settings.max_iterations,
        max_tool_calls=settings.max_tool_calls,
        max_total_tokens=settings.max_total_tokens,
        max_model_cost=settings.max_model_cost,
        heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
    )
    worker = RunnerWorker(
        runner,
        mission_id=settings.mission_id,
        lease_seconds=settings.lease_seconds,
        idle_delay_seconds=settings.idle_delay_seconds,
        max_delay_seconds=settings.max_delay_seconds,
    )
    return RunnerServiceRuntime(
        worker=worker,
        shutdown_timeout_seconds=settings.shutdown_timeout_seconds,
        closeables=(control_http, model_http, mcp_http),
    )


__all__ = ["RunnerServiceRuntime", "build_runner_runtime"]
