"""Tenant/workspace-scoped Agent binding ports for Mission Control.

The resolver returns only the execution facts needed by a WorkUnit. Provider
credentials and raw Agent configuration stay outside the Mission domain.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol


class AgentBindingUnavailableError(RuntimeError):
    """Raised when no authorized Agent catalog is configured."""


@dataclass(frozen=True, slots=True)
class AgentBinding:
    """Non-sensitive execution binding resolved for one scoped Agent."""

    agent_id: str
    adapter_type: str
    capabilities: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> AgentBinding:
        agent_id = str(value.get("agent_id") or value.get("agentId") or "").strip()
        adapter_type = str(
            value.get("adapter_type") or value.get("adapterType") or ""
        ).strip()
        raw_capabilities = value.get("capabilities") or value.get("capabilityTags") or ()
        if isinstance(raw_capabilities, str) or not isinstance(
            raw_capabilities, Sequence
        ):
            raw_capabilities = ()
        capabilities = tuple(
            sorted({str(item).strip() for item in raw_capabilities if str(item).strip()})
        )
        if not agent_id or not adapter_type:
            raise ValueError("Agent binding requires agent_id and adapter_type")
        return cls(
            agent_id=agent_id,
            adapter_type=adapter_type,
            capabilities=capabilities,
        )


class AgentBindingResolver(Protocol):
    async def resolve(
        self,
        *,
        scope_id: str,
        agent_id: str,
    ) -> AgentBinding | None:
        """Resolve one Agent without returning provider credentials."""


class UnavailableAgentBindingResolver:
    """Fail closed until a durable, scope-aware catalog adapter is installed."""

    async def resolve(
        self,
        *,
        scope_id: str,
        agent_id: str,
    ) -> AgentBinding | None:
        raise AgentBindingUnavailableError(
            "tenant-scoped Agent binding resolver is not configured"
        )


class StaticAgentBindingResolver:
    """Small deterministic resolver used by contract and service tests."""

    def __init__(self, bindings: Mapping[tuple[str, str], AgentBinding]) -> None:
        self._bindings = dict(bindings)

    async def resolve(
        self,
        *,
        scope_id: str,
        agent_id: str,
    ) -> AgentBinding | None:
        return self._bindings.get((scope_id, agent_id))
