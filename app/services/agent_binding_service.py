"""Tenant/workspace-scoped Agent binding ports for Mission Control.

The resolver returns only the execution facts needed by a WorkUnit. Provider
credentials and raw Agent configuration stay outside the Mission domain.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

_ADAPTER_TYPE_PATTERN = re.compile(
    r"^(?=.{1,64}$)[a-z][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)*$"
)
_MAX_CATALOG_VERSION = 2_147_483_646


class AgentBindingUnavailableError(RuntimeError):
    """Raised when no authorized Agent catalog is configured."""


class AgentCatalogVersionConflictError(RuntimeError):
    """Raised when a catalog mutation loses optimistic concurrency control."""


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
        if len(agent_id) > 255:
            raise ValueError("Agent binding agent_id is too long")
        if not _ADAPTER_TYPE_PATTERN.fullmatch(adapter_type):
            raise ValueError("Agent binding adapter_type is invalid")
        if any(len(capability) > 255 for capability in capabilities):
            raise ValueError("Agent binding capability is too long")
        if len(capabilities) > 256:
            raise ValueError("Agent binding has too many capabilities")
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


class AgentBindingSelector(Protocol):
    async def select(
        self,
        *,
        scope_id: str,
        required_capabilities: Sequence[str],
        adapter_type: str | None = None,
    ) -> AgentBinding | None:
        """Select one deterministic, enabled binding for all capabilities."""


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


class UnavailableAgentBindingSelector:
    """Fail closed until a scoped catalog selector is installed."""

    async def select(
        self,
        *,
        scope_id: str,
        required_capabilities: Sequence[str],
        adapter_type: str | None = None,
    ) -> AgentBinding | None:
        del scope_id, required_capabilities, adapter_type
        raise AgentBindingUnavailableError(
            "workspace-scoped Agent binding selector is not configured"
        )


AgentCatalogLookup = Callable[
    [str, str], Awaitable[Mapping[str, object] | None]
]

AgentCatalogSelect = Callable[
    [str, tuple[str, ...], str | None], Awaitable[Mapping[str, object] | None]
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

    async def list_enabled(self, *, scope_id: str) -> list[AgentBinding]:
        """Return every enabled agent binding for this workspace.

        Used by the unified member roster endpoint — the member model
        (ADR-0108 §3.3) treats bound, enabled agents as first-class
        session members alongside human users.
        """
        from app.db.session import afetch_all

        rows = await afetch_all(
            """
            SELECT agent_id, adapter_type, capabilities
            FROM agent_catalog_bindings
            WHERE scope_id = $1 AND enabled = TRUE
            ORDER BY agent_id ASC
            """,
            scope_id,
        )
        return [AgentBinding.from_mapping(row) for row in rows]


async def _select_catalog_binding(
    scope_id: str,
    required_capabilities: tuple[str, ...],
    adapter_type: str | None,
) -> Mapping[str, object] | None:
    from app.db.session import afetch_one

    return await afetch_one(
        """
        SELECT agent_id, adapter_type, capabilities
        FROM agent_catalog_bindings
        WHERE scope_id = $1
          AND enabled = TRUE
          AND capabilities @> $2::jsonb
          AND ($3::text IS NULL OR adapter_type = $3)
        ORDER BY agent_id ASC, adapter_type ASC
        LIMIT 1
        """,
        scope_id,
        json.dumps(list(required_capabilities)),
        adapter_type,
    )


class DatabaseAgentBindingSelector:
    """Select one enabled, capability-complete binding deterministically."""

    def __init__(self, select: AgentCatalogSelect | None = None) -> None:
        self._select = select or _select_catalog_binding

    async def select(
        self,
        *,
        scope_id: str,
        required_capabilities: Sequence[str],
        adapter_type: str | None = None,
    ) -> AgentBinding | None:
        normalized_scope_id = scope_id.strip()
        if not normalized_scope_id or len(normalized_scope_id) > 255:
            raise ValueError("Agent catalog scope_id is invalid")
        capabilities = _normalize_required_capabilities(required_capabilities)
        normalized_adapter = _normalize_adapter_type(adapter_type)
        try:
            row = await self._select(
                normalized_scope_id,
                capabilities,
                normalized_adapter,
            )
            if row is None:
                return None
            binding = AgentBinding.from_mapping(row)
        except AgentBindingUnavailableError:
            raise
        except Exception as exc:
            raise AgentBindingUnavailableError("Agent catalog selection failed") from exc
        missing = sorted(set(capabilities) - set(binding.capabilities))
        if missing:
            raise AgentBindingUnavailableError(
                "Agent catalog selected a capability-incomplete binding"
            )
        if (
            normalized_adapter is not None
            and binding.adapter_type != normalized_adapter
        ):
            raise AgentBindingUnavailableError(
                "Agent catalog selected a binding for another adapter"
            )
        return binding


@dataclass(frozen=True, slots=True)
class AgentCatalogMutation:
    scope_id: str
    binding: AgentBinding
    enabled: bool
    expected_version: int


@dataclass(frozen=True, slots=True)
class AgentCatalogRecord:
    scope_id: str
    binding: AgentBinding
    enabled: bool
    source_version: int
    updated_at: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> AgentCatalogRecord:
        scope_id = str(value.get("scope_id") or "").strip()
        source_version = int(value.get("source_version") or 0)
        updated_at_value = value.get("updated_at")
        if isinstance(updated_at_value, datetime):
            updated_at = updated_at_value.isoformat()
        else:
            updated_at = str(updated_at_value or "").strip()
        if not scope_id or len(scope_id) > 255:
            raise ValueError("Agent catalog scope_id is invalid")
        if source_version < 1 or not updated_at:
            raise ValueError("Agent catalog version metadata is invalid")
        return cls(
            scope_id=scope_id,
            binding=AgentBinding.from_mapping(value),
            enabled=bool(value.get("enabled")),
            source_version=source_version,
            updated_at=updated_at,
        )

    def to_public_dict(self) -> dict[str, object]:
        return {
            "scopeId": self.scope_id,
            "agentId": self.binding.agent_id,
            "adapterType": self.binding.adapter_type,
            "capabilities": list(self.binding.capabilities),
            "enabled": self.enabled,
            "sourceVersion": self.source_version,
            "updatedAt": self.updated_at,
        }


AgentCatalogWrite = Callable[
    [AgentCatalogMutation], Awaitable[Mapping[str, object] | None]
]


async def _write_catalog_binding(
    mutation: AgentCatalogMutation,
) -> Mapping[str, object] | None:
    from app.db.session import afetch_one

    return await afetch_one(
        """
        WITH updated AS (
            UPDATE agent_catalog_bindings
            SET adapter_type = $3,
                capabilities = $4::jsonb,
                enabled = $5,
                source_version = source_version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE scope_id = $1
              AND agent_id = $2
              AND $6 > 0
              AND source_version = $6
            RETURNING scope_id, agent_id, adapter_type, capabilities, enabled,
                      source_version, updated_at
        ), inserted AS (
            INSERT INTO agent_catalog_bindings (
                scope_id, agent_id, adapter_type, capabilities, enabled,
                source_version
            )
            SELECT $1, $2, $3, $4::jsonb, $5, 1
            WHERE $6 = 0
            ON CONFLICT (scope_id, agent_id) DO NOTHING
            RETURNING scope_id, agent_id, adapter_type, capabilities, enabled,
                      source_version, updated_at
        )
        SELECT * FROM updated
        UNION ALL
        SELECT * FROM inserted
        LIMIT 1
        """,
        mutation.scope_id,
        mutation.binding.agent_id,
        mutation.binding.adapter_type,
        json.dumps(list(mutation.binding.capabilities)),
        mutation.enabled,
        mutation.expected_version,
    )


class DatabaseAgentCatalogWriter:
    """Persist catalog bindings with atomic optimistic concurrency control."""

    def __init__(self, write: AgentCatalogWrite | None = None) -> None:
        self._write = write or _write_catalog_binding

    async def put(
        self,
        *,
        scope_id: str,
        agent_id: str,
        adapter_type: str,
        capabilities: Sequence[str],
        enabled: bool,
        expected_version: int,
    ) -> AgentCatalogRecord:
        normalized_scope_id = scope_id.strip()
        if not normalized_scope_id or len(normalized_scope_id) > 255:
            raise ValueError("Agent catalog scope_id is invalid")
        if not 0 <= expected_version <= _MAX_CATALOG_VERSION:
            raise ValueError("Agent catalog expected_version is out of range")
        binding = AgentBinding.from_mapping(
            {
                "agent_id": agent_id,
                "adapter_type": adapter_type,
                "capabilities": capabilities,
            }
        )
        mutation = AgentCatalogMutation(
            scope_id=normalized_scope_id,
            binding=binding,
            enabled=enabled,
            expected_version=expected_version,
        )
        try:
            row = await self._write(mutation)
            if row is None:
                raise AgentCatalogVersionConflictError(
                    "Agent catalog binding version conflict"
                )
            record = AgentCatalogRecord.from_mapping(row)
        except AgentCatalogVersionConflictError:
            raise
        except Exception as exc:
            raise AgentBindingUnavailableError("Agent catalog write failed") from exc

        if (
            record.scope_id != normalized_scope_id
            or record.binding != binding
            or record.enabled is not enabled
            or record.source_version != expected_version + 1
        ):
            raise AgentBindingUnavailableError("Agent catalog returned invalid mutation")
        return record


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


class StaticAgentBindingSelector:
    """Deterministic capability selector used by adapter and service tests."""

    def __init__(self, bindings: Mapping[str, Sequence[AgentBinding]]) -> None:
        self._bindings = {
            scope_id: tuple(bindings_for_scope)
            for scope_id, bindings_for_scope in bindings.items()
        }

    async def select(
        self,
        *,
        scope_id: str,
        required_capabilities: Sequence[str],
        adapter_type: str | None = None,
    ) -> AgentBinding | None:
        capabilities = set(_normalize_required_capabilities(required_capabilities))
        normalized_adapter = _normalize_adapter_type(adapter_type)
        eligible = [
            binding
            for binding in self._bindings.get(scope_id, ())
            if capabilities <= set(binding.capabilities)
            and (
                normalized_adapter is None or binding.adapter_type == normalized_adapter
            )
        ]
        if not eligible:
            return None
        return min(eligible, key=lambda binding: (binding.agent_id, binding.adapter_type))


def _normalize_required_capabilities(
    required_capabilities: Sequence[str],
) -> tuple[str, ...]:
    if isinstance(required_capabilities, (str, bytes, bytearray)):
        raise TypeError("required capabilities must be an array")
    if any(not isinstance(capability, str) for capability in required_capabilities):
        raise TypeError("required capabilities must contain strings")
    normalized = tuple(
        sorted({capability.strip() for capability in required_capabilities})
    )
    if any(not capability for capability in normalized):
        raise ValueError("required capabilities must not contain empty values")
    if len(normalized) > 256 or any(len(capability) > 255 for capability in normalized):
        raise ValueError("required capabilities exceed catalog limits")
    return normalized


def _normalize_adapter_type(adapter_type: str | None) -> str | None:
    if adapter_type is None:
        return None
    normalized = adapter_type.strip()
    if not _ADAPTER_TYPE_PATTERN.fullmatch(normalized):
        raise ValueError("Agent catalog adapter_type is invalid")
    return normalized
