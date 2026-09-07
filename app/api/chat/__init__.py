"""Chat API package — session CRUD, membership, auto-name, tasks."""

from app.api.chat._helpers import (
    SessionCreateRequest,
    InviteRequest,
    RoleChangeRequest,
    is_generic_name,
    GENERIC_SESSION_NAMES,
    try_auto_name_session,
)
from app.api.chat._routes import router

__all__ = [
    "router",
    "SessionCreateRequest",
    "InviteRequest",
    "RoleChangeRequest",
    "is_generic_name",
    "GENERIC_SESSION_NAMES",
    "try_auto_name_session",
]