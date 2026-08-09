from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.domain import MissionContract, WorkUnit
from app.services.harness_service import FunctionTool


class CapabilityResolutionError(ValueError):
    """Raised when a WorkUnit capability cannot be resolved fail-closed."""


ScopedArgumentValidator = Callable[
    [Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]
]
ScopedFunctionHandler = Callable[
    [Mapping[str, Any], Mapping[str, Any]], Awaitable[str]
]


@dataclass(frozen=True)
class CapabilityToolBinding:
    """Static registry entry for one capability-scoped function."""

    capability: str
    function_name: str
    description: str
    parameters: Mapping[str, Any]
    validate_arguments: ScopedArgumentValidator
    handler: ScopedFunctionHandler


class CapabilityToolResolver:
    """Build the least-privilege tool set for a Contract and WorkUnit."""

    def __init__(self, bindings: list[CapabilityToolBinding]) -> None:
        by_capability: dict[str, list[CapabilityToolBinding]] = {}
        for binding in bindings:
            if not binding.capability or not binding.function_name:
                raise ValueError("Capability and function names must be non-empty")
            by_capability.setdefault(binding.capability, []).append(binding)
        self._bindings = by_capability

    def resolve(
        self,
        contract: MissionContract,
        work_unit: WorkUnit,
    ) -> list[FunctionTool]:
        grants = {grant.capability: grant for grant in contract.allowed_capabilities}
        required = list(work_unit.required_capabilities)
        unauthorized = [capability for capability in required if capability not in grants]
        if unauthorized:
            raise CapabilityResolutionError(
                "WorkUnit capabilities are not granted by its Contract: "
                + ", ".join(unauthorized)
            )

        tools: list[FunctionTool] = []
        function_names: set[str] = set()
        for capability in required:
            bindings = self._bindings.get(capability, [])
            if not bindings:
                raise CapabilityResolutionError(
                    f"No tool binding is registered for capability: {capability}"
                )
            scope = grants[capability].scope
            for binding in bindings:
                if binding.function_name in function_names:
                    raise CapabilityResolutionError(
                        f"Duplicate function name resolved: {binding.function_name}"
                    )
                function_names.add(binding.function_name)
                tools.append(_bind_tool(binding, scope))
        return tools


def _bind_tool(
    binding: CapabilityToolBinding,
    scope: Mapping[str, Any],
) -> FunctionTool:
    def validate(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return binding.validate_arguments(arguments, scope)

    async def handle(arguments: Mapping[str, Any]) -> str:
        return await binding.handler(arguments, scope)

    return FunctionTool(
        name=binding.function_name,
        description=binding.description,
        parameters=binding.parameters,
        validate_arguments=validate,
        handler=handle,
    )


__all__ = [
    "CapabilityResolutionError",
    "CapabilityToolBinding",
    "CapabilityToolResolver",
]
