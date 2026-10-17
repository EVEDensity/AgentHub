"""Operator-only strict model budget plus one scoped anonymous browser tool."""

from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import Decimal

from app.domain.mission_budget import BudgetScope
from app.services.browser_read import BrowserNativeHarness, LocalBrowserReadBackend
from app.services.harness_types import HarnessError
from app.services.runner.strict_budget import _context_values
from app.services.runner_protocols import ClaimedWorkResolutionError
from app.services.strict_provider import BudgetedModelPort

BROWSER_CAPABILITY = "browser.read"


def browser_capability_scope(profile) -> dict:
    return profile.scope()


def _authorize(contract, unit, profile):
    if tuple(unit.required_capabilities) != (BROWSER_CAPABILITY,):
        raise ClaimedWorkResolutionError(
            "browser Native profile requires exactly browser.read"
        )
    grants = [
        grant
        for grant in contract.allowed_capabilities
        if grant.capability == BROWSER_CAPABILITY
    ]
    if len(grants) != 1 or dict(grants[0].scope) != browser_capability_scope(profile):
        raise ClaimedWorkResolutionError(
            "browser capability requires the exact operator profile and origins"
        )
    if contract.repository_scopes:
        raise ClaimedWorkResolutionError(
            "browser profile does not implement host repository scopes"
        )


class _ContractBoundHarness:
    def __init__(self, harness, time_limit):
        self._harness, self._time_limit = harness, time_limit

    async def execute(self, request):
        if (
            type(request.timeout) not in (int, float)
            or not math.isfinite(request.timeout)
            or not 0 < request.timeout <= self._time_limit
        ):
            raise HarnessError("browser execution timeout exceeds its actual Contract")
        return await self._harness.execute(request)


class BudgetedBrowserReadHarnessFactory:
    def __init__(
        self,
        *,
        runner_id: str,
        backend: LocalBrowserReadBackend,
        budget_service,
        profile,
        api_key: str,
        max_iterations: int = 4,
        max_total_tokens: int | None = None,
        limit_usd: Decimal | str | int | None = None,
    ):
        if not isinstance(runner_id, str) or not 1 <= len(runner_id) <= 255:
            raise ValueError("browser factory requires a server Runner identity")
        if type(max_iterations) is not int or not 1 <= max_iterations <= 32:
            raise ValueError("browser Native iterations must be in [1,32]")
        if max_total_tokens is not None and (
            type(max_total_tokens) is not int or max_total_tokens < 1
        ):
            raise ValueError("browser token telemetry limit must be a positive integer")
        self._runner_id, self._backend, self._service = (
            runner_id,
            backend,
            budget_service,
        )
        self._profile, self._api_key, self._limit = profile, api_key, limit_usd
        self._iterations, self._tokens = max_iterations, max_total_tokens

    def build(self, context):
        if not isinstance(context, Mapping):
            raise ClaimedWorkResolutionError("browser Native context must be a mapping")
        _mission, contract, unit = _context_values(context, self._runner_id)
        _authorize(contract, unit, self._backend.profile)
        scope = BudgetScope(
            mission_id=unit.mission_id,
            work_unit_id=unit.id,
            attempt=unit.attempt,
            runner_id=self._runner_id,
            lease_id=unit.lease.id,
        )
        model = BudgetedModelPort(
            scope, self._service, self._profile, self._api_key, limit_usd=self._limit
        )

        def native(tools):
            from app.services.harness_service import FunctionCallingHarness

            return FunctionCallingHarness(
                model,
                list(tools),
                model_retries=0,
                max_iterations=self._iterations,
                max_tool_calls=self._backend.profile.max_reads,
                max_total_tokens=self._tokens,
            )

        from app.services.harness_checkpoint import HarnessExecutionContext

        execution = HarnessExecutionContext(
            scope.mission_id, scope.work_unit_id, scope.attempt
        )
        harness = BrowserNativeHarness(
            execution=execution,
            backend=self._backend,
            native_factory=native,
            contract_id=contract.id,
            contract_version=contract.version,
        )
        return _ContractBoundHarness(harness, contract.budgets.time_seconds)
