"""IAM-backed admission policy for workspace-scoped Runner claims."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

_MAX_RUNNER_CONCURRENCY = 1_000_000


class WorkspaceClaimAdmissionUnavailableError(RuntimeError):
    """Raised when Mission Control cannot resolve a trustworthy policy."""


class WorkspaceClaimAdmissionDeniedError(RuntimeError):
    """Raised when workspace ownership policy explicitly denies claims."""


@dataclass(frozen=True, slots=True)
class WorkspaceClaimAdmissionPolicy:
    tenant_id: str
    max_concurrent: int

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, object],
    ) -> WorkspaceClaimAdmissionPolicy:
        tenant_id = str(value.get("tenant_id") or "").strip()
        if not tenant_id or len(tenant_id) > 255:
            raise ValueError("Workspace admission tenant_id is invalid")
        status = str(value.get("status") or "").strip()
        if status not in {"active", "suspended", "closed"}:
            raise ValueError("Workspace admission tenant status is invalid")
        if status != "active":
            raise WorkspaceClaimAdmissionDeniedError(
                "Workspace tenant is not active"
            )
        raw_limit = value.get("max_concurrent")
        if isinstance(raw_limit, bool) or raw_limit is None:
            raise ValueError("Workspace admission max_concurrent is invalid")
        try:
            max_concurrent = int(raw_limit)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Workspace admission max_concurrent is invalid"
            ) from exc
        if str(max_concurrent) != str(raw_limit).strip():
            raise ValueError("Workspace admission max_concurrent is invalid")
        if not 0 <= max_concurrent <= _MAX_RUNNER_CONCURRENCY:
            raise ValueError("Workspace admission max_concurrent is out of range")
        return cls(tenant_id=tenant_id, max_concurrent=max_concurrent)


class WorkspaceClaimAdmissionPolicyResolver(Protocol):
    async def resolve(
        self,
        *,
        workspace_id: str,
    ) -> WorkspaceClaimAdmissionPolicy:
        """Resolve the tenant policy governing one workspace claim."""


WorkspaceClaimAdmissionLookup = Callable[
    [str], Awaitable[Mapping[str, object] | None]
]


async def _lookup_workspace_claim_admission(
    workspace_id: str,
) -> Mapping[str, object] | None:
    from app.db.session import afetch_one

    return await afetch_one(
        """
        SELECT workspace.tenant_id,
               tenant.status,
               CASE
                   WHEN tenant.quotas_json::jsonb ? 'max_concurrent'
                   THEN tenant.quotas_json::jsonb ->> 'max_concurrent'
                   ELSE quota.max_concurrent::text
               END AS max_concurrent
        FROM platform_workspaces AS workspace
        JOIN platform_tenants AS tenant ON tenant.id = workspace.tenant_id
        LEFT JOIN platform_quota_definitions AS quota ON quota.plan = tenant.plan
        WHERE workspace.id = $1
        LIMIT 1
        """,
        workspace_id,
    )


class DatabaseWorkspaceClaimAdmissionPolicyResolver:
    """Resolve Runner concurrency from the existing IAM quota truth."""

    def __init__(self, lookup: WorkspaceClaimAdmissionLookup | None = None) -> None:
        self._lookup = lookup or _lookup_workspace_claim_admission

    async def resolve(
        self,
        *,
        workspace_id: str,
    ) -> WorkspaceClaimAdmissionPolicy:
        normalized_workspace_id = workspace_id.strip()
        if not normalized_workspace_id or len(normalized_workspace_id) > 255:
            raise ValueError("Workspace admission workspace_id is invalid")
        try:
            row = await self._lookup(normalized_workspace_id)
            if row is None:
                raise WorkspaceClaimAdmissionUnavailableError(
                    "Workspace claim admission policy is not configured"
                )
            return WorkspaceClaimAdmissionPolicy.from_mapping(row)
        except (
            WorkspaceClaimAdmissionDeniedError,
            WorkspaceClaimAdmissionUnavailableError,
        ):
            raise
        except Exception as exc:
            raise WorkspaceClaimAdmissionUnavailableError(
                "Workspace claim admission policy lookup failed"
            ) from exc
