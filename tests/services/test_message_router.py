from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from app.services import message_router


def test_route_message_defaults_to_direct_call_agent(monkeypatch):
    """AGENTHUB_ENABLE_LEGACY_LANGGRAPH 默认关闭：route_message 直接走 call_agent，
    不再经 LangGraph 编排（R2 decommission）。"""
    monkeypatch.setattr(message_router, "_use_legacy_langgraph", False)
    call = AsyncMock(return_value={"content": "ok", "agent": "Orchestrator"})
    with patch.object(message_router, "call_agent", call):
        result = asyncio.run(
            message_router.route_message("s1", "hello", user_id="u1", attachments=[])
        )
    call.assert_awaited_once_with(
        "s1", "hello", user_id="u1", attachments=[], on_tool_event=None
    )
    assert result["content"] == "ok"


def test_route_message_legacy_flag_invokes_langgraph(monkeypatch):
    """显式开启 AGENTHUB_ENABLE_LEGACY_LANGGRAPH 时保留 LangGraph 编排迁移窗口。"""
    monkeypatch.setattr(message_router, "_use_legacy_langgraph", True)
    run = AsyncMock(return_value={"content": "legacy", "taskId": "t1"})
    workflow = type("Workflow", (), {"run": run})
    fake_module = type("MModule", (), {"agent_workflow": workflow()})
    with patch.dict("sys.modules", {"app.services.langgraph_workflow": fake_module}):
        result = asyncio.run(message_router.route_message("s1", "hello", user_id="u1"))
    run.assert_awaited_once()
    assert result["content"] == "legacy"