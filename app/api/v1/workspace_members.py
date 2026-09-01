"""Unified workspace member roster (P1, ADR-0108 §3.3).

Returns every member of a workspace as a single view: the requesting
human user plus every enabled agent catalog binding. Agents are
first-class members — the member model does not distinguish between
human and agent members at the roster level; roles and capabilities
are surfaced as structured fields on each entry.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.v1.access import authorize_workspace
from app.services.agent_binding_service import (
    AgentBindingUnavailableError,
    DatabaseAgentBindingResolver,
)
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def get_agent_binding_resolver() -> DatabaseAgentBindingResolver:
    return DatabaseAgentBindingResolver()


CurrentUser = Annotated[dict, Depends(get_current_user)]
AgentBindingResolverDep = Annotated[
    DatabaseAgentBindingResolver,
    Depends(get_agent_binding_resolver),
]


@router.get("/{scope_id}/members")
async def list_workspace_members(
    scope_id: str,
    user: CurrentUser,
    resolver: AgentBindingResolverDep,
) -> dict[str, object]:
    """Return the unified member roster for a workspace.

    The caller's own human record is always included; agents are pulled
    from the enabled catalog bindings for this scope. The roster is
    static — presence and typing indicators live on the client side
    (WebSocket events / SSE receipts) and are overlaid on top.
    """
    authorize_workspace(user, scope_id)

    members: list[dict[str, object]] = [
        {
            "memberId": str(user.get("id") or user.get("user_id") or ""),
            "kind": "human",
            "name": str(
                user.get("name")
                or user.get("username")
                or user.get("email")
                or ""
            ).strip()
            or "You",
            "role": str(user.get("role") or "member"),
            "adapterType": None,
            "capabilities": [],
            "enabled": True,
        }
    ]

    try:
        bindings = await resolver.list_enabled(scope_id=scope_id)
    except AgentBindingUnavailableError:
        bindings = []

    for binding in bindings:
        members.append(
            {
                "memberId": binding.agent_id,
                "kind": "agent",
                "name": binding.agent_id,
                "role": "agent",
                "adapterType": binding.adapter_type,
                "capabilities": list(binding.capabilities),
                "enabled": True,
            }
        )

    return {"scopeId": scope_id, "members": members}
