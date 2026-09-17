from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from app.services import message_router


def test_route_message_always_uses_call_agent():
    """T0-4: Legacy LangGraph orchestration decommissioned.

    route_message now always delegates to call_agent directly; the
    AGENTHUB_ENABLE_LEGACY_LANGGRAPH env var is retained in config.py for
    backwards-compat but is intentionally ignored.
    """
    call = AsyncMock(return_value={"content": "ok", "agent": "Orchestrator"})
    with patch.object(message_router, "call_agent", call):
        result = asyncio.run(
            message_router.route_message("s1", "hello", user_id="u1", attachments=[])
        )
    call.assert_awaited_once_with(
        "s1", "hello", user_id="u1", attachments=[], on_tool_event=None
    )
    assert result["content"] == "ok"
