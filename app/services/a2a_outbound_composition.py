from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import httpx

from app.services.a2a_outbound_result import A2AOutboundResultImporter
from app.services.a2a_outbound_runner import (
    A2AOutboundClaimedWork,
    A2AOutboundClaimedWorkResolver,
    A2AOutboundClaimIdentity,
    parse_a2a_outbound_claim,
)
from app.services.a2a_outbound_supervisor import (
    A2AOutboundSupervisionResult,
    A2AOutboundSupervisor,
)
from app.services.a2a_outbound_transport import (
    A2APeerCredentialProviderPort,
    StatelessA2AHTTPTransport,
)
from app.services.a2a_peer_route_service import (
    A2AAgentCardRouteResolver,
    A2AAgentCardTrustPolicy,
)
from app.services.artifact_store_service import ArtifactPublisher
from app.services.runner_service import MissionControlRunnerPort, RunnerExecutionError


class A2AOutboundAttemptError(RunnerExecutionError):
    """Raised when a claimed outbound attempt cannot be resolved honestly."""


class A2AOutboundAttemptRunner:
    """Resolve and supervise one already claimed outbound A2A WorkUnit."""

    def __init__(
        self,
        control: MissionControlRunnerPort,
        resolver: A2AOutboundClaimedWorkResolver,
        supervisor: A2AOutboundSupervisor,
        *,
        runner_id: str,
    ) -> None:
        if not isinstance(runner_id, str) or not runner_id.strip():
            raise ValueError("runner_id must be non-empty")
        self._control = control
        self._resolver = resolver
        self._supervisor = supervisor
        self._runner_id = runner_id

    async def run_claimed(
        self,
        claimed_work_unit: Mapping[str, Any],
        *,
        lease_seconds: int = 300,
    ) -> A2AOutboundSupervisionResult:
        """Execute one claim without owning polling or HTTP-client lifecycle."""

        if not 1 <= lease_seconds <= 3_600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        identity = parse_a2a_outbound_claim(
            claimed_work_unit,
            runner_id=self._runner_id,
        )
        try:
            work = await self._resolver.resolve(claimed_work_unit)
            _assert_resolved_identity(work, identity)
        except asyncio.CancelledError:
            try:
                await self._record_resolution_failure(
                    identity,
                    "outbound A2A resolution canceled",
                )
            except A2AOutboundAttemptError:
                # Cancellation remains authoritative; lease expiry is the fallback.
                pass
            raise
        except Exception as exc:
            await self._record_resolution_failure(
                identity,
                f"outbound A2A resolution failed: {type(exc).__name__}",
            )
            raise A2AOutboundAttemptError(
                "outbound A2A claimed context resolution failed"
            ) from exc
        return await self._supervisor.supervise(work, lease_seconds=lease_seconds)

    async def _record_resolution_failure(
        self,
        identity: A2AOutboundClaimIdentity,
        reason: str,
    ) -> None:
        try:
            failed = await self._control.fail_work_unit(
                identity.mission_id,
                identity.work_unit_id,
                runner_id=self._runner_id,
                lease_id=identity.lease_id,
                reason=reason[:2_000],
            )
        except Exception as exc:
            raise A2AOutboundAttemptError(
                "Mission Control could not record outbound resolution failure"
            ) from exc
        if not isinstance(failed, Mapping):
            raise A2AOutboundAttemptError(
                "Mission Control returned an invalid outbound failure response"
            )
        if (
            failed.get("id") != identity.work_unit_id
            or failed.get("missionId") != identity.mission_id
            or failed.get("status") != "FAILED"
            or failed.get("attempt") != identity.attempt
            or failed.get("lease") is not None
        ):
            raise A2AOutboundAttemptError(
                "Mission Control returned an inconsistent outbound failure response"
            )


def build_a2a_outbound_attempt_runner(
    control: MissionControlRunnerPort,
    *,
    publisher: ArtifactPublisher,
    http_client: httpx.AsyncClient,
    trust_policy: A2AAgentCardTrustPolicy,
    credential_provider: A2APeerCredentialProviderPort,
    runner_id: str,
    source_agent_url: str,
    max_command_bytes: int = 32_768,
    max_execution_timeout_seconds: float = 3_600.0,
    card_timeout_seconds: float = 10.0,
    card_max_response_bytes: int = 1 << 20,
    card_max_redirects: int = 3,
    transport_timeout_seconds: float = 30.0,
    transport_max_request_bytes: int = 64 * 1_024,
    transport_max_response_bytes: int = 1 << 20,
    transport_max_redirects: int = 3,
    poll_interval_seconds: float = 1.0,
    heartbeat_interval_seconds: float | None = None,
    cancellation_timeout_seconds: float = 5.0,
) -> A2AOutboundAttemptRunner:
    """Compose strict outbound execution without a Harness or runtime dispatch."""

    if not isinstance(http_client, httpx.AsyncClient):
        raise TypeError("http_client must be an httpx.AsyncClient")
    if http_client.is_closed:
        raise ValueError("http_client must be open")
    if not isinstance(trust_policy, A2AAgentCardTrustPolicy):
        raise TypeError("trust_policy must be an A2AAgentCardTrustPolicy")
    if trust_policy.allow_unsigned_cards or not trust_policy.require_pinned_keys:
        raise ValueError("outbound Runner requires signed and pinned Agent Cards")
    if not callable(getattr(credential_provider, "bearer_for", None)):
        raise TypeError("credential_provider must resolve origin-bound credentials")

    route_resolver = A2AAgentCardRouteResolver(
        trust_policy=trust_policy,
        http_client=http_client,
        timeout_seconds=card_timeout_seconds,
        max_response_bytes=card_max_response_bytes,
        max_redirects=card_max_redirects,
    )
    transport = StatelessA2AHTTPTransport(
        route_resolver,
        credential_provider=credential_provider,
        http_client=http_client,
        timeout_seconds=transport_timeout_seconds,
        max_request_bytes=transport_max_request_bytes,
        max_response_bytes=transport_max_response_bytes,
        max_redirects=transport_max_redirects,
    )
    result_importer = A2AOutboundResultImporter(
        control,
        publisher,
        runner_id=runner_id,
    )
    supervisor = A2AOutboundSupervisor(
        control,
        transport,
        result_importer,
        runner_id=runner_id,
        poll_interval_seconds=poll_interval_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        cancellation_timeout_seconds=cancellation_timeout_seconds,
    )
    resolver = A2AOutboundClaimedWorkResolver(
        control,
        runner_id=runner_id,
        source_agent_url=source_agent_url,
        max_request_bytes=max_command_bytes,
        max_timeout_seconds=max_execution_timeout_seconds,
    )
    return A2AOutboundAttemptRunner(
        control,
        resolver,
        supervisor,
        runner_id=runner_id,
    )


def _assert_resolved_identity(
    work: A2AOutboundClaimedWork,
    identity: A2AOutboundClaimIdentity,
) -> None:
    if not isinstance(work, A2AOutboundClaimedWork):
        raise A2AOutboundAttemptError("outbound resolver returned invalid work")
    if (
        work.mission_id != identity.mission_id
        or work.work_unit_id != identity.work_unit_id
        or work.attempt != identity.attempt
        or work.lease_id != identity.lease_id
    ):
        raise A2AOutboundAttemptError("outbound resolver changed the claim fence")


__all__ = [
    "A2AOutboundAttemptError",
    "A2AOutboundAttemptRunner",
    "build_a2a_outbound_attempt_runner",
]
