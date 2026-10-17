"""Explicit operator-only in-process budgeted Harness composition.

Injected handlers are trusted operator code and must perform only local,
non-model operations. A name/capability allowlist does not sandbox Python code.
This factory is deliberately absent from default Desktop/CLI/service wiring.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from app.domain import Mission, MissionContract, WorkUnit
from app.domain.mission_budget import BudgetScope
from app.services.harness_service import FunctionCallingHarness, FunctionTool, HarnessError, HarnessRequest, HarnessResult
from app.services.mission_budget_service import MissionBudgetService
from app.services.runner_protocols import ClaimedWorkResolutionError

if TYPE_CHECKING:
    from app.services.strict_provider import StrictProviderProfile


_FORBIDDEN_TOOLS = frozenset({
    'delegate', 'delegate_to_agent', 'call_agent', 'ask_agent', 'spawn_agent',
    'spawn_subagent', 'run_model', 'call_model', 'ask_model', 'invoke_model',
    'llm_call', 'a2a_send', 'mcp_call',
})


def _tool_names(tools):
    if not isinstance(tools, tuple) or any(not isinstance(tool, FunctionTool) for tool in tools):
        raise ValueError('strict tools require an explicit tuple of FunctionTool values')
    if any(not isinstance(tool.name, str) or not tool.name for tool in tools):
        raise ValueError('strict tools require nonempty function names')
    return {tool.name for tool in tools}


def _tool_configuration(tools, allowed, capabilities):
    names = _tool_names(tools)
    if not isinstance(allowed, frozenset) or not isinstance(capabilities, Mapping):
        raise ValueError('strict tools require a frozen operator allowlist and capability mapping')
    if len(names) != len(tools) or not names <= set(allowed) or set(capabilities) != names:
        raise ValueError('strict tools require unique names, an operator allowlist and exact capability mapping')
    if any(not isinstance(value, str) or not value for value in capabilities.values()):
        raise ValueError('strict tool capabilities must be nonempty strings')
    if any(name.casefold().replace('-', '_') in _FORBIDDEN_TOOLS for name in names):
        raise ValueError('strict composition refuses known model or delegation tools')
    if any(value.casefold().startswith(('model.', 'agent.', 'a2a.', 'mcp.')) for value in capabilities.values()):
        raise ValueError('strict composition supports only local non-model tool capabilities')
    return dict(capabilities)


def _context_values(context, runner_id):
    try:
        mission = Mission.model_validate(context.get('mission'))
        contract = MissionContract.model_validate(context.get('contract'))
        unit = WorkUnit.model_validate(context.get('workUnit'))
    except (TypeError, ValidationError) as exc:
        raise ClaimedWorkResolutionError('strict budget context failed domain validation') from exc
    if mission.status.value != 'RUNNING' or unit.mission_id != mission.id:
        raise ClaimedWorkResolutionError('strict budget context requires the actual RUNNING Mission')
    if (contract.id, contract.version) != (mission.contract_id, mission.contract_version):
        raise ClaimedWorkResolutionError('strict budget Contract revision differs from Mission')
    _validate_execution_binding(mission, unit, runner_id)
    if context.get('checkpoint') is not None:
        raise ClaimedWorkResolutionError('strict budget composition does not support resume')
    return mission, contract, unit


def _validate_execution_binding(mission, unit, runner_id):
    if mission.source.type.value not in {'manual', 'chat'} or unit.kind != 'desktop.task':
        raise ClaimedWorkResolutionError('strict budget composition supports only manual/chat desktop tasks')
    if unit.parent_work_unit_id is not None or unit.assigned_adapter != 'function-calling' or not unit.assigned_agent_id:
        raise ClaimedWorkResolutionError('strict budget composition requires a bound local root executor')
    if unit.status.value not in {'LEASED', 'RUNNING'} or unit.attempt < 1 or unit.lease is None:
        raise ClaimedWorkResolutionError('strict budget context requires a positive actively leased attempt')
    if unit.lease.runner_id != runner_id or unit.lease.expires_at <= datetime.now(timezone.utc):
        raise ClaimedWorkResolutionError('strict budget context has another Runner or an expired lease')


def _authorize_tools(contract, unit, capabilities):
    required = set(unit.required_capabilities)
    if not required <= set(capabilities.values()):
        raise ClaimedWorkResolutionError('strict required capabilities lack an explicit operator tool binding')
    for capability in capabilities.values():
        grants = [grant for grant in contract.allowed_capabilities if grant.capability == capability]
        if capability not in required or len(grants) != 1:
            raise ClaimedWorkResolutionError('strict tool capability is not uniquely granted and required')
        if grants[0].scope:
            raise ClaimedWorkResolutionError('scoped strict tools require a dedicated capability binding; unsupported')


def _budgeted_model(scope, service, profile, api_key, *, limit_usd=None):
    from app.services.strict_provider import BudgetedModelPort

    return BudgetedModelPort(scope=scope, service=service, profile=profile, api_key=api_key, limit_usd=limit_usd)


class _StrictBudgetHarness:
    def __init__(self, harness, scope, *, time_limit, workspace_root, manifest):
        self._harness, self._scope = harness, scope
        self._time_limit, self._workspace_root = time_limit, workspace_root
        self._manifest = manifest

    @property
    def workspace_root(self) -> Path | None:
        return self._workspace_root

    @property
    def recovery_manifest(self) -> dict:
        return {**self._manifest, 'toolCapabilities': dict(self._manifest['toolCapabilities'])}

    async def execute(self, request: HarnessRequest) -> HarnessResult:
        if request.execution is None:
            raise HarnessError('strict budget execution requires a bound attempt identity')
        actual = (request.execution.mission_id, request.execution.work_unit_id, request.execution.attempt)
        expected = (self._scope.mission_id, self._scope.work_unit_id, self._scope.attempt)
        if actual != expected:
            raise HarnessError('strict budget execution differs from the privately bound attempt')
        if request.resume is not None or request.on_text_delta is not None:
            raise HarnessError('strict budget composition does not support resume or streaming callbacks')
        if not math.isfinite(request.timeout) or not 0 < request.timeout <= self._time_limit:
            raise HarnessError('strict budget timeout must fit the actual Contract time limit')
        return await self._harness.execute(request)


class StrictBudgetHarnessFactory:
    """Bind trusted claim context before execution; reserve only on model calls."""
    def __init__(self, *, runner_id: str, budget_service: MissionBudgetService,
                 profile: StrictProviderProfile, api_key: str, tools: tuple[FunctionTool, ...] = (),
                 allowed_tool_names: frozenset[str] = frozenset(), tool_capabilities: Mapping[str, str] | None = None,
                 max_iterations: int = 8, max_tool_calls: int = 32, max_total_tokens: int | None = None,
                 workspace_root: Path | None = None, limit_usd: Decimal | str | int | None = None):
        if not isinstance(runner_id, str) or not 1 <= len(runner_id) <= 255:
            raise ValueError('strict factory requires a server Runner identity')
        self._capabilities = _tool_configuration(tools, allowed_tool_names, tool_capabilities or {})
        self._runner_id, self._service, self._profile = runner_id, budget_service, profile
        self._api_key, self._tools = api_key, tools
        self._limit_usd = limit_usd
        self._iterations, self._calls, self._tokens = max_iterations, max_tool_calls, max_total_tokens
        self._workspace = workspace_root.resolve() if workspace_root is not None else None

    def build(self, context: Mapping[str, Any]):
        _mission, contract, unit = _context_values(context, self._runner_id)
        _authorize_tools(contract, unit, self._capabilities)
        scope = BudgetScope(mission_id=unit.mission_id, work_unit_id=unit.id, attempt=unit.attempt,
                            lease_id=unit.lease.id, runner_id=self._runner_id)
        model = _budgeted_model(scope, self._service, self._profile, self._api_key, limit_usd=self._limit_usd)
        harness = FunctionCallingHarness(model, list(self._tools), max_iterations=self._iterations,
                                         max_tool_calls=self._calls, max_total_tokens=self._tokens, model_retries=0)
        manifest = {'kind': 'operator-strict-budget-harness.v1', 'profileDigest': self._profile.digest,
                    'modelOperationScheme': 'harness:model:{iteration}:{purpose}', 'modelRetries': 0,
                    'resumeSupported': False, 'toolCapabilities': dict(self._capabilities)}
        return _StrictBudgetHarness(harness, scope, time_limit=contract.budgets.time_seconds,
                                    workspace_root=self._workspace, manifest=manifest)
