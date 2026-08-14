"""Tenant/workspace-scoped Agent binding ports for Mission Control.

The resolver returns only the execution facts needed by a WorkUnit. Provider
credentials and raw Agent configuration stay outside the Mission domain.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
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
        if isinstance(raw_capabilities, str):
            try:
                raw_capabilities = json.loads(raw_capabilities)
            except json.JSONDecodeError as exc:
                raise ValueError("Agent binding capabilities must be valid JSON") from exc
        if not isinstance(raw_capabilities, Sequence) or isinstance(
            raw_capabilities,
            (str, bytes, bytearray),
        ):
            raise TypeError("Agent binding capabilities must be an array")
        if any(not isinstance(item, str) for item in raw_capabilities):
            raise TypeError("Agent binding capabilities must contain strings")
        capabilities = tuple(
            sorted({item.strip() for item in raw_capabilities if item.strip()})
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


AgentCatalogLookup = Callable[
    [str, str], Awaitable[Mapping[str, object] | None]
]


async def _lookup_catalog_binding(
    scope_id: str,
    agent_id: str,
) -> Mapping[str, object] | None:
    from app.db.session import afetch_one

    return await afetch_one(
        """
        SELECT agent_id, adapter_type, capabilities
        FROM agent_catalog_bindings
        WHERE scope_id = $1 AND agent_id = $2 AND enabled = TRUE
        """,
        scope_id,
        agent_id,
    )


class DatabaseAgentBindingResolver:
    """Resolve enabled bindings from the credential-free catalog projection."""

    def __init__(self, lookup: AgentCatalogLookup | None = None) -> None:
        self._lookup = lookup or _lookup_catalog_binding

    async def resolve(
        self,
        *,
        scope_id: str,
        agent_id: str,
    ) -> AgentBinding | None:
        try:
            row = await self._lookup(scope_id, agent_id)
            if row is None:
                return None
            binding = AgentBinding.from_mapping(row)
        except AgentBindingUnavailableError:
            raise
        except Exception as exc:
            raise AgentBindingUnavailableError("Agent catalog lookup failed") from exc

        if binding.agent_id != agent_id:
            raise AgentBindingUnavailableError("Agent catalog returned invalid binding")
        return binding


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
