"""User management — admin-level user listing.

Endpoints:
  GET    /users   List all registered users
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.db.session import afetch_all
from app.services.auth_service import get_current_user, require_admin

router = APIRouter(prefix="/users", tags=["admin-users"])


@router.get("")
async def list_users(user: dict = Depends(get_current_user)) -> list[dict]:
    """Return every registered user (admin only)."""
    require_admin(user)
    return await afetch_all(
        "SELECT id, name, role, created_at AS \"createdAt\" FROM users ORDER BY created_at"
    )
