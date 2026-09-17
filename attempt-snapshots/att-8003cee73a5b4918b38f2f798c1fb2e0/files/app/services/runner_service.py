"""Compatibility shim — all real code lives in app/services/_runner_service_impl.py.

This module exists so that ``from app.services.runner_service import X``
continues to work.  New code should import from
``app.services._runner_service_impl`` or ``app.services.runner._service``
directly once the circular-import chain is resolved.
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