"""Chat-to-Mission v1 route package.

Thin re-export layer so existing import paths keep working unchanged,
including private helpers that services and tests import directly:

  from app.api.v1.chat_mission import router, create_chat_mission, ...
  from app.api.v1.chat_mission import _SPECIAL_MENTIONS, _parse_mentions, ...
"""

from app.api.v1.chat_mission._helpers import (
    ChatMissionRequest,
    ConfirmPendingRequest,
    CancelPendingRequest,
    router,
    _MENTION_RE,
    _SPECIAL_MENTIONS,
    _now_dt,
    _parse_mentions,
    _resolve_mentions,
    _pick_default_participant,
    _build_chat_contract,
    _inline_derive_work_units,
    _preprocess_archivist,
    get_mission_repository,
    get_session_event_repository,
    get_session_repository,
    get_agent_binding_resolver,
    get_pending_confirmation_repository,
)
from app.api.v1.chat_mission._handlers import (
    create_chat_mission,
    confirm_pending,
    cancel_pending,
)

__all__ = [
    "router",
    "ChatMissionRequest",
    "ConfirmPendingRequest",
    "CancelPendingRequest",
    "create_chat_mission",
    "confirm_pending",
    "cancel_pending",
    "get_mission_repository",
    "get_session_event_repository",
    "get_session_repository",
    "get_agent_binding_resolver",
    "get_pending_confirmation_repository",
    # Private helpers — re-exported for services and tests
    "_MENTION_RE",
    "_SPECIAL_MENTIONS",
    "_now_dt",
    "_parse_mentions",
    "_resolve_mentions",
    "_pick_default_participant",
    "_build_chat_contract",
    "_inline_derive_work_units",
    "_preprocess_archivist",
]