"""Compatibility exports for Runner orchestration, values and transport.

This module exists so that ``from app.services.runner_service import X``
continues to work. Immutable ports/results live in ``runner_protocols``, the
Mission Control HTTP adapter lives in ``runner_client``, pure context compilation
and scoped resolution live in ``runner_model_context``/``runner_model_resolver``,
and lease-supervised attempt execution remains in ``_runner_service_impl``.
"""

from __future__ import annotations

from app.services._runner_service_impl import (
    A2AInboundClaimedWorkResolver,
    assert_claimed_work_unit,
    ClaimedHarnessFactoryPort,
    ClaimedWorkExecution,
    ClaimedWorkResolutionError,
    ClaimedWorkResolver,
    compile_mission_fork_context,
    DesktopTaskClaimedWorkResolver,
    KindAwareClaimedWorkResolver,
    MissionControlRunnerClient,
    MissionControlRunnerPort,
    MissionForkClaimedWorkResolver,
    parse_workspace_claim_status,
    RunnerControlError,
    RunnerExecutionError,
    RunnerExecutionInput,
    RunnerError,
    RunnerHeartbeatError,
    RunnerRunResult,
    RunnerWorkspacePollResult,
    SandboxPort,
    WorkUnitRunner,
)

__all__ = [
    "A2AInboundClaimedWorkResolver",
    "assert_claimed_work_unit",
    "ClaimedHarnessFactoryPort",
    "ClaimedWorkExecution",
    "ClaimedWorkResolutionError",
    "ClaimedWorkResolver",
    "compile_mission_fork_context",
    "DesktopTaskClaimedWorkResolver",
    "KindAwareClaimedWorkResolver",
    "MissionControlRunnerClient",
    "MissionControlRunnerPort",
    "MissionForkClaimedWorkResolver",
    "parse_workspace_claim_status",
    "RunnerControlError",
    "RunnerExecutionError",
    "RunnerExecutionInput",
    "RunnerError",
    "RunnerHeartbeatError",
    "RunnerRunResult",
    "RunnerWorkspacePollResult",
    "SandboxPort",
    "WorkUnitRunner",
]
