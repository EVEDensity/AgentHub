from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.services.auth_service import get_current_user

router = APIRouter(prefix="/access", tags=["access"])


def authorize_workspace(user: dict, workspace_id: str) -> None:
    if user.get("role") == "admin" or str(user["id"]) == workspace_id:
        return
    raise HTTPException(status_code=403, detail="Workspace access denied")


def authorize_verifier(user: dict) -> None:
    if user.get("role") in {"admin", "verifier"}:
        return
    raise HTTPException(status_code=403, detail="Verifier access required")


@router.get("/whoami")
async def whoami(user: dict = Depends(get_current_user)) -> dict:
    """Report the authenticated principal and its effective v1 capabilities.

    Clients (admin web, CLI helpers) use this single probe instead of
    duplicating role logic: ``workspaceId`` is the principal's own workspace
    scope, and ``canVerify`` mirrors :func:`authorize_verifier` semantics.
    """
    role = user.get("role")
    return {
        "userId": str(user["id"]),
        "name": user.get("name", ""),
        "role": role,
        "workspaceId": str(user["id"]),
        "isAdmin": role == "admin",
        "canVerify": role in {"admin", "verifier"},
    }
