"""Workspace-scoped service-principal grants consumed by Mission Control."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

RUNNER_WORKSPACE_CLAIM_SCOPE = "mission:claim"


class RunnerWorkspaceGrantUnavailableError(RuntimeError):
    """Raised when the workspace ACL source cannot answer safely."""


class RunnerWorkspaceGrantAuthorizer(Protocol):
    async def has_claim_grant(
        self,
        *,
        workspace_id: str,
        principal_id: str,
    ) -> bool:
        """Return whether a principal may claim work in one workspace."""


RunnerWorkspaceGrantLookup = Callable[
    [str, str, str], Awaitable[Mapping[str, object] | None]
]


async def _lookup_runner_workspace_grant(
    workspace_id: str,
    principal_id: str,
    scope: str,
) -> Mapping[str, object] | None:
    from app.db.session import afetch_one

    return await afetch_one(
        """
        SELECT 1 AS granted
        FROM platform_workspace_members
        WHERE workspace_id = $1
          AND user_id = $2
          AND permissions @> jsonb_build_array($3::text)
        LIMIT 1
        """,
        workspace_id,
        principal_id,
        scope,
    )


class DatabaseRunnerWorkspaceGrantAuthorizer:
    """Read explicit Runner grants from the existing workspace ACL truth."""

    def __init__(self, lookup: RunnerWorkspaceGrantLookup | None = None) -> None:
        self._lookup = lookup or _lookup_runner_workspace_grant

    async def has_claim_grant(
        self,
        *,
        workspace_id: str,
        principal_id: str,
    ) -> bool:
        normalized_workspace_id = _normalize_identifier(workspace_id, "workspace_id")
        normalized_principal_id = _normalize_identifier(principal_id, "principal_id")
        try:
            row = await self._lookup(
                normalized_workspace_id,
                normalized_principal_id,
                RUNNER_WORKSPACE_CLAIM_SCOPE,
            )
        except RunnerWorkspaceGrantUnavailableError:
            raise
        except Exception as exc:
            raise RunnerWorkspaceGrantUnavailableError(
                "Runner workspace grant lookup failed"
            ) from exc
        return row is not None


def _normalize_identifier(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 255:
        raise ValueError(f"Runner workspace grant {field} is invalid")
    return normalized
