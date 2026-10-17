"""Explicit budgeted Native Harness composition with one sandbox capability.

The control plane owns authorization/finance; this factory creates no Mission
state. Only the pinned experimental shell profile is admitted. It is not wired
into default Desktop/CLI/Chat or the standalone Runner service.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import Decimal

from app.domain.mission_budget import BudgetScope
from app.services.harness_service import FunctionCallingHarness, HarnessError
from app.services.opensandbox_harness import SandboxHarnessLimits, SandboxNativeHarness
from app.services.opensandbox_protocol import WORKLOAD_IMAGE
from app.services.runner.strict_budget import _context_values
from app.services.runner_protocols import ClaimedWorkResolutionError
from app.services.strict_provider import BudgetedModelPort

SANDBOX_CAPABILITY = 'sandbox.execute'
SANDBOX_PROFILE = 'opensandbox-shell.v1'


def sandbox_capability_scope() -> dict[str, str]:
    """Return a fresh scope; no mutable module-level grant is shared."""
    return {'profile': SANDBOX_PROFILE, 'image': WORKLOAD_IMAGE}


def _authorize(contract, unit) -> None:
    if tuple(unit.required_capabilities) != (SANDBOX_CAPABILITY,):
        raise ClaimedWorkResolutionError('sandbox Native profile requires exactly sandbox.execute')
    grants = [grant for grant in contract.allowed_capabilities if grant.capability == SANDBOX_CAPABILITY]
    if len(grants) != 1 or dict(grants[0].scope) != sandbox_capability_scope():
        raise ClaimedWorkResolutionError('sandbox capability requires the exact operator profile and pinned image')
    if tuple(contract.repository_scopes):
        raise ClaimedWorkResolutionError('sandbox shell profile does not implement host repository scopes')


class _ContractBoundHarness:
    def __init__(self, harness, time_limit):
        self._harness, self._time_limit = harness, time_limit

    async def execute(self, request):
        if type(request.timeout) not in (int, float) or not math.isfinite(request.timeout):
            raise HarnessError('sandbox execution requires a finite timeout')
        if not 0 < request.timeout <= self._time_limit:
            raise HarnessError('sandbox execution timeout exceeds its actual Contract')
        if request.on_text_delta is not None:
            raise HarnessError('sandbox Native profile does not support streaming')
        return await self._harness.execute(request)


class BudgetedOpenSandboxHarnessFactory:
    """Privately bind exact claim identity, provider accounting and shell grant."""
    def __init__(self, *, runner_id: str, backend, budget_service, profile, api_key: str,
                 limits: SandboxHarnessLimits | None = None, job_id: str | None = None,
                 max_iterations: int = 4, max_total_tokens: int | None = None,
                 limit_usd: Decimal | str | int | None = None):
        if not isinstance(runner_id, str) or not 1 <= len(runner_id) <= 255:
            raise ValueError('sandbox factory requires a server Runner identity')
        if type(max_iterations) is not int or not 1 <= max_iterations <= 32:
            raise ValueError('sandbox Native iterations must be in [1,32]')
        if limits is not None and not isinstance(limits, SandboxHarnessLimits):
            raise ValueError('sandbox factory requires explicit SandboxHarnessLimits')
        if max_total_tokens is not None and (type(max_total_tokens) is not int or max_total_tokens < 1):
            raise ValueError('sandbox token telemetry limit must be a positive integer')
        self._runner_id, self._backend, self._service = runner_id, backend, budget_service
        self._profile, self._api_key, self._limit = profile, api_key, limit_usd
        self._limits = limits or SandboxHarnessLimits()
        self._job_id, self._iterations, self._tokens = job_id, max_iterations, max_total_tokens

    def build(self, context):
        if not isinstance(context, Mapping):
            raise ClaimedWorkResolutionError('sandbox Native context must be a mapping')
        _mission, contract, unit = _context_values(context, self._runner_id)
        _authorize(contract, unit)
        scope = BudgetScope(mission_id=unit.mission_id, work_unit_id=unit.id,
            attempt=unit.attempt, runner_id=self._runner_id, lease_id=unit.lease.id)
        model = BudgetedModelPort(scope, self._service, self._profile, self._api_key, limit_usd=self._limit)

        def native(tools):
            return FunctionCallingHarness(model, list(tools), model_retries=0,
                max_iterations=self._iterations, max_tool_calls=self._limits.max_commands,
                max_total_tokens=self._tokens)

        from app.services.harness_checkpoint import HarnessExecutionContext
        execution = HarnessExecutionContext(scope.mission_id, scope.work_unit_id, scope.attempt)
        harness = SandboxNativeHarness(execution=execution, backend=self._backend, native_factory=native,
            limits=self._limits, job_id=self._job_id)
        return _ContractBoundHarness(harness, contract.budgets.time_seconds)
