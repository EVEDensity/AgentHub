from __future__ import annotations

from fastapi import HTTPException


def authorize_workspace(user: dict, workspace_id: str) -> None:
    if user.get("role") == "admin" or str(user["id"]) == workspace_id:
        return
    raise HTTPException(status_code=403, detail="Workspace access denied")
