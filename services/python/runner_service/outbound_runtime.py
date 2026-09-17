from __future__ import annotations

from collections.abc import Sequence

import httpx

from app.services.a2a_outbound_composition import build_a2a_outbound_attempt_runner
from app.services.a2a_outbound_worker import A2AOutboundWorkspaceRunner
from app.services.artifact_store_service import ArtifactPublisher
from app.services.runner_service import MissionControlRunnerPort
from app.services.runner_worker import RunnerWorker

from .a2a_peers import LoadedA2ARunnerPeers
from .runtime import AsyncClosePort, RunnerServiceRuntime


def compose_a2a_outbound_runtime_candidate(
    control: MissionControlRunnerPort,
    *,
    publisher: ArtifactPublisher,
    peer_http: httpx.AsyncClient,
    peers: LoadedA2ARunnerPeers,
    runner_id: str,
    workspace_id: str,
    assigned_agent_id: str,
    source_agent_url: str,
    owned_closeables: Sequence[AsyncClosePort] = (),
    lease_seconds: int = 300,
    idle_delay_seconds: float = 0.5,
    max_delay_seconds: float = 10.0,
    shutdown_timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 1.0,
    heartbeat_interval_seconds: float | None = None,
    cancellation_timeout_seconds: float = 5.0,
) -> RunnerServiceRuntime:
    """Compose an isolated outbound process candidate without activating it."""

    if not isinstance(peer_http, httpx.AsyncClient):
        raise TypeError("peer_http must be an httpx.AsyncClient")
    if peer_http.is_closed:
        raise ValueError("peer_http must be open")
    if not isinstance(peers, LoadedA2ARunnerPeers):
        raise TypeError("peers must be loaded A2A Runner peers")
    if shutdown_timeout_seconds <= 0:
        raise ValueError("shutdown_timeout_seconds must be positive")
    transferred = tuple(owned_closeables)
    if any(not callable(getattr(item, "aclose", None)) for item in transferred):
        raise TypeError("owned_closeables must provide aclose")
    resource_ids = [id(item) for item in (*transferred, peer_http)]
    if len(resource_ids) != len(set(resource_ids)):
        raise ValueError("outbound runtime resources must be unique")

    attempt_runner = build_a2a_outbound_attempt_runner(
        control,
        publisher=publisher,
        http_client=peer_http,
        trust_policy=peers.trust_policy,
        credential_provider=peers.credential_provider,
        runner_id=runner_id,
        source_agent_url=source_agent_url,
        poll_interval_seconds=poll_interval_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        cancellation_timeout_seconds=cancellation_timeout_seconds,
    )
    outbound_runner = A2AOutboundWorkspaceRunner(
        control,
        attempt_runner,
        runner_id=runner_id,
        assigned_agent_id=assigned_agent_id,
    )
    worker = RunnerWorker(
        outbound_runner,
        workspace_id=workspace_id,
        lease_seconds=lease_seconds,
        idle_delay_seconds=idle_delay_seconds,
        max_delay_seconds=max_delay_seconds,
    )
    return RunnerServiceRuntime(
        worker=worker,
        shutdown_timeout_seconds=shutdown_timeout_seconds,
        closeables=(*transferred, peer_http),
    )


__all__ = ["compose_a2a_outbound_runtime_candidate"]
