from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from pydantic import ValidationError

from app.domain import MissionContract, WorkUnit
from app.services.artifact_store_service import ArtifactPublisher
from app.services.capability_tools import (
    CapabilityResolutionError,
    CapabilityToolBinding,
    CapabilityToolResolver,
)
from app.services.harness_checkpoint import HarnessExecutionContext
from app.services.harness_service import (
    FunctionCallingHarness,
    FunctionTool,
    HarnessPort,
    ModelPort,
)
from app.services.runner_service import (
    A2AInboundClaimedWorkResolver,
    ClaimedWorkResolutionError,
    MissionControlRunnerPort,
    WorkUnitRunner,
)

A2A_RECEIVE_CAPABILITY = "a2a.receive"


class HarnessModelFactoryPort(Protocol):
    """Build a request-scoped model adapter with the exact resolved tool set."""

    def build(self, tools: Sequence[FunctionTool]) -> ModelPort: ...


class CapabilityBindingFactoryPort(Protocol):
    """Build capability bindings correlated to one execution attempt."""

    def build(
        self,
        execution: HarnessExecutionContext,
    ) -> Sequence[CapabilityToolBinding]: ...


class A2AInboundHarnessFactory:
    """Build one capability-scoped model Harness from claimed durable context."""

    def __init__(
        self,
        model_factory: HarnessModelFactoryPort,
        binding_factory: CapabilityBindingFactoryPort,
        *,
        max_iterations: int = 8,
        max_tool_calls: int = 32,
        max_total_tokens: int | None = None,
        max_model_cost: float | None = None,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls must be at least 1")
        if max_total_tokens is not None and max_total_tokens < 1:
            raise ValueError("max_total_tokens must be at least 1")
        if max_model_cost is not None and (
            not math.isfinite(max_model_cost) or max_model_cost < 0
        ):
            raise ValueError("max_model_cost must be non-negative")
        self._model_factory = model_factory
        self._binding_factory = binding_factory
        self._max_iterations = max_iterations
        self._max_tool_calls = max_tool_calls
        self._max_total_tokens = max_total_tokens
        self._max_model_cost = max_model_cost

    def build(self, context: Mapping[str, Any]) -> HarnessPort:
        try:
            contract = MissionContract.model_validate(context.get("contract"))
            work_unit = WorkUnit.model_validate(context.get("workUnit"))
        except (TypeError, ValidationError) as exc:
            raise ClaimedWorkResolutionError(
                "claimed execution context failed domain validation"
            ) from exc

        if A2A_RECEIVE_CAPABILITY not in work_unit.required_capabilities:
            raise ClaimedWorkResolutionError(
                "inbound WorkUnit lacks the A2A admission capability"
            )
        if work_unit.attempt < 1:
            raise ClaimedWorkResolutionError("inbound WorkUnit has no active attempt")
        if work_unit.status.value not in {"LEASED", "RUNNING"}:
            raise ClaimedWorkResolutionError("inbound WorkUnit is not actively leased")
        execution = HarnessExecutionContext(
            mission_id=work_unit.mission_id,
            work_unit_id=work_unit.id,
            attempt=work_unit.attempt,
        )
        try:
            bindings = list(self._binding_factory.build(execution))
        except Exception as exc:
            raise ClaimedWorkResolutionError(
                f"capability binding factory failed: {type(exc).__name__}"
            ) from exc

        tool_work_unit = work_unit.model_copy(
            update={
                "required_capabilities": tuple(
                    capability
                    for capability in work_unit.required_capabilities
                    if capability != A2A_RECEIVE_CAPABILITY
                )
            }
        )
        try:
            tools = CapabilityToolResolver(bindings).resolve(contract, tool_work_unit)
        except CapabilityResolutionError as exc:
            raise ClaimedWorkResolutionError(
                "claimed capability requirements could not be resolved"
            ) from exc
        except Exception as exc:
            raise ClaimedWorkResolutionError(
                f"capability binding resolution failed: {type(exc).__name__}"
            ) from exc

        try:
            model = self._model_factory.build(tools)
        except Exception as exc:
            raise ClaimedWorkResolutionError(
                f"model factory failed: {type(exc).__name__}"
            ) from exc
        if not callable(getattr(model, "complete", None)):
            raise ClaimedWorkResolutionError("model factory returned an invalid model")

        model_cost_limit = contract.budgets.model_cost
        if self._max_model_cost is not None:
            model_cost_limit = min(model_cost_limit, self._max_model_cost)
        try:
            return FunctionCallingHarness(
                model,
                tools,
                max_iterations=self._max_iterations,
                max_tool_calls=self._max_tool_calls,
                max_total_tokens=self._max_total_tokens,
                max_model_cost=model_cost_limit,
            )
        except ValueError as exc:
            raise ClaimedWorkResolutionError(
                "inbound Harness policy configuration is invalid"
            ) from exc


def build_a2a_inbound_runner(
    control: MissionControlRunnerPort,
    *,
    publisher: ArtifactPublisher,
    model_factory: HarnessModelFactoryPort,
    binding_factory: CapabilityBindingFactoryPort,
    runner_id: str,
    assigned_agent_id: str,
    assigned_adapter: str,
    max_context_chars: int = 32_768,
    max_timeout_seconds: float = 300.0,
    max_iterations: int = 8,
    max_tool_calls: int = 32,
    max_total_tokens: int | None = None,
    max_model_cost: float | None = None,
    heartbeat_interval_seconds: float | None = None,
) -> WorkUnitRunner:
    """Compose the inbound claim path without provider or tool fallbacks."""
    for name, value in (
        ("runner_id", runner_id),
        ("assigned_agent_id", assigned_agent_id),
        ("assigned_adapter", assigned_adapter),
    ):
        if not value.strip():
            raise ValueError(f"{name} must be non-empty")
    if assigned_adapter == "a2a.outbound":
        raise ValueError("inbound Runner cannot use the outbound A2A adapter")
    harness_factory = A2AInboundHarnessFactory(
        model_factory,
        binding_factory,
        max_iterations=max_iterations,
        max_tool_calls=max_tool_calls,
        max_total_tokens=max_total_tokens,
        max_model_cost=max_model_cost,
    )
    resolver = A2AInboundClaimedWorkResolver(
        control,
        runner_id=runner_id,
        harness_factory=harness_factory,
        max_context_chars=max_context_chars,
        max_timeout_seconds=max_timeout_seconds,
    )
    return WorkUnitRunner(
        control,
        publisher=publisher,
        runner_id=runner_id,
        assigned_agent_id=assigned_agent_id,
        assigned_adapter=assigned_adapter,
        claimed_work_resolver=resolver,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )


__all__ = [
    "A2A_RECEIVE_CAPABILITY",
    "A2AInboundHarnessFactory",
    "CapabilityBindingFactoryPort",
    "HarnessModelFactoryPort",
    "build_a2a_inbound_runner",
]
