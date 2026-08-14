"""Credential-free projection from the legacy Registry into Agent catalog."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from app.services.agent_binding_service import (
    AgentBinding,
    AgentBindingUnavailableError,
    AgentCatalogRecord,
    DatabaseAgentCatalogWriter,
)


class RegistryAgentNotFoundError(RuntimeError):
    """Raised when the actor cannot see the requested Registry Agent."""


class RegistryAgentNotRunnableError(RuntimeError):
    """Raised when a visible Registry Agent cannot become an execution binding."""


@dataclass(frozen=True, slots=True)
class RegistryAgentProjection:
    binding: AgentBinding
    status: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> RegistryAgentProjection:
        try:
            binding = AgentBinding.from_mapping(
                {
                    "agent_id": value.get("agent_id"),
                    "adapter_type": value.get("adapter_type"),
                    "capabilities": value.get("capability_tags") or (),
                }
            )
        except (TypeError, ValueError) as exc:
            raise RegistryAgentNotRunnableError(
                "Registry Agent binding is invalid"
            ) from exc
        if binding.adapter_type == "mock":
            raise RegistryAgentNotRunnableError(
                "Registry Agent has no executable adapter"
            )
        status = str(value.get("status") or "").strip().lower()
        if status not in {"online", "offline", "sleeping"}:
            raise RegistryAgentNotRunnableError("Registry Agent status is invalid")
        return cls(binding=binding, status=status)

    @property
    def enabled(self) -> bool:
        return self.status in {"online", "sleeping"}


class RegistryAgentSource(Protocol):
    async def resolve(
        self,
        *,
        owner_id: str,
        agent_id: str,
    ) -> RegistryAgentProjection | None:
        """Return only the safe fields needed for a catalog binding."""


RegistryAgentLookup = Callable[
    [str, str], Awaitable[Mapping[str, object] | None]
]


async def _lookup_registry_agent(
    owner_id: str,
    agent_id: str,
) -> Mapping[str, object] | None:
    from app.db.session import afetch_one

    return await afetch_one(
        """
        SELECT agent_id, adapter_type, capability_tags, status
        FROM agent_registry
        WHERE agent_id = $1 AND (user_id = $2 OR user_id = '')
        ORDER BY CASE WHEN user_id = $2 THEN 0 ELSE 1 END
        LIMIT 1
        """,
        agent_id,
        owner_id,
    )


class DatabaseRegistryAgentSource:
    """Read a user's visible Registry Agent through a safe SQL projection."""

    def __init__(self, lookup: RegistryAgentLookup | None = None) -> None:
        self._lookup = lookup or _lookup_registry_agent

    async def resolve(
        self,
        *,
        owner_id: str,
        agent_id: str,
    ) -> RegistryAgentProjection | None:
        try:
            row = await self._lookup(owner_id, agent_id)
        except Exception as exc:
            raise AgentBindingUnavailableError("Agent Registry lookup failed") from exc
        if row is None:
            return None
        projection = RegistryAgentProjection.from_mapping(row)
        if projection.binding.agent_id != agent_id:
            raise AgentBindingUnavailableError(
                "Agent Registry returned invalid projection"
            )
        return projection


class AgentCatalogSynchronizer:
    """Synchronize one safe Registry projection through the catalog CAS writer."""

    def __init__(
        self,
        source: RegistryAgentSource,
        writer: DatabaseAgentCatalogWriter,
    ) -> None:
        self._source = source
        self._writer = writer

    async def sync(
        self,
        *,
        scope_id: str,
        source_owner_id: str,
        agent_id: str,
        expected_version: int,
    ) -> AgentCatalogRecord:
        projection = await self._source.resolve(
            owner_id=source_owner_id,
            agent_id=agent_id,
        )
        if projection is None:
            raise RegistryAgentNotFoundError("Registry Agent not found")
        return await self._writer.put(
            scope_id=scope_id,
            agent_id=projection.binding.agent_id,
            adapter_type=projection.binding.adapter_type,
            capabilities=projection.binding.capabilities,
            enabled=projection.enabled,
            expected_version=expected_version,
        )
