from __future__ import annotations

import asyncio

import pytest

from app.api import websocket_dispatch as dispatch


def test_dispatch_control_event_routes_presence(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class FakeManager:
        def set_user_presence(self, session_id: str, user_id: str, status: str) -> None:
            seen["presence"] = (session_id, user_id, status)

        async def broadcast_user_event(self, session_id: str, payload: dict, exclude_user: str | None = None) -> None:
            seen["broadcast"] = (session_id, payload, exclude_user)

    monkeypatch.setattr(dispatch, "_manager", lambda: FakeManager())

    handled = asyncio.run(
        dispatch.dispatch_control_event(
            session_id="session-1",
            data={"event": "set_presence", "status": "away"},
            websocket=object(),
            user_id="user-1",
            user_name="Alice",
            conn_id="conn-1",
            on_agent_question_response=lambda *args: asyncio.sleep(0),
            on_risk_warning_response=lambda *args: asyncio.sleep(0),
            on_agent_todo_response=lambda *args: asyncio.sleep(0),
            on_task_preview_response=lambda *args: asyncio.sleep(0),
            on_solution_selection=lambda *args: asyncio.sleep(0),
            on_diff_decision=lambda *args: asyncio.sleep(0),
        )
    )

    assert handled is True
    assert seen["presence"] == ("session-1", "user-1", "away")
    assert seen["broadcast"][1]["event"] == "presence_update"


def test_dispatch_message_flow_blocks_non_writers(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict] = []

    class FakeManager:
        async def _send_safe(self, websocket, payload: dict) -> bool:
            seen.append(payload)
            return True

    monkeypatch.setattr(dispatch, "_manager", lambda: FakeManager())

    handled = asyncio.run(
        dispatch.dispatch_message_flow(
            session_id="session-2",
            content="hello",
            sender="Alice",
            user_id="user-2",
            access_can_write=False,
            websocket=object(),
            data={},
            attachments=[],
            quote_references=[],
            auto_reply=True,
            process_and_stream=lambda *args: asyncio.sleep(0),
            log_task_error=lambda *args: None,
        )
    )

    assert handled is True
    assert seen[0]["event"] == "system"
    assert "permission" in seen[0]["content"]


def test_dispatch_message_flow_schedules_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def fake_process_and_stream(*args) -> None:
        seen.append(args[1])

    def fake_create_task(coro):
        coro.close()

        class _Task:
            def add_done_callback(self, callback):
                seen.append("scheduled")

        return _Task()

    class FakeManager:
        def has_active_stream(self, session_id: str) -> bool:
            return False

    monkeypatch.setattr(dispatch, "_manager", lambda: FakeManager())
    monkeypatch.setattr(dispatch.asyncio, "create_task", fake_create_task)

    handled = asyncio.run(
        dispatch.dispatch_message_flow(
            session_id="session-3",
            content="do work",
            sender="Alice",
            user_id="user-3",
            access_can_write=True,
            websocket=object(),
            data={"auto_reply": True},
            attachments=[{"path": "a.txt"}],
            quote_references=[],
            auto_reply=True,
            process_and_stream=fake_process_and_stream,
            log_task_error=lambda *args: None,
        )
    )

    assert handled is True
    assert seen == ["scheduled"]
