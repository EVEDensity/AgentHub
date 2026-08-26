from __future__ import annotations

import asyncio

from app.api import websocket_state as ws_state


def test_session_exec_permission_roundtrip() -> None:
    session_id = "session-permission-test"
    assert ws_state.get_session_exec_permission(session_id) == 1

    ws_state.set_session_exec_permission(session_id, 2)
    assert ws_state.get_session_exec_permission(session_id) == 2

    ws_state.set_session_exec_permission(session_id, 9)
    assert ws_state.get_session_exec_permission(session_id) == 2


def test_auto_name_and_memory_throttle_are_session_scoped(monkeypatch) -> None:
    current = [100.0]
    monkeypatch.setattr(ws_state.time, "monotonic", lambda: current[0])

    session_id = "session-throttle-test"
    assert ws_state._should_auto_name(session_id) is True
    assert ws_state._should_auto_name(session_id) is False

    current[0] += 16
    assert ws_state._should_auto_name(session_id) is True

    assert ws_state.should_run_memory_tasks(session_id) is True
    assert ws_state.should_run_memory_tasks(session_id) is False

    current[0] += 31
    assert ws_state.should_run_memory_tasks(session_id) is True


def test_permission_response_wakes_waiter() -> None:
    session_id = "session-permission-response"
    request_id = "req-1"
    event = asyncio.Event()
    ws_state._permission_state[session_id] = {
        request_id: {"event": event, "decision": "deny"},
    }

    try:
        assert ws_state.handle_permission_response(session_id, request_id, "allow") is True
        assert event.is_set()
        assert ws_state._permission_state[session_id][request_id]["decision"] == "allow"
    finally:
        ws_state._permission_state.pop(session_id, None)


def test_task_preview_resolution_and_wait() -> None:
    async def _run() -> tuple[str, str]:
        session_id = "session-preview-test"
        preview_id = "preview-1"
        waiter = asyncio.create_task(
            ws_state.wait_for_task_confirmation(session_id, preview_id, token=None)
        )
        await asyncio.sleep(0)

        ws_state._pending_task_previews[session_id][preview_id]
        assert ws_state.resolve_pending_task_preview(session_id, preview_id, "modify", "update plan")
        return await waiter

    try:
        decision, modifications = asyncio.run(_run())
        assert decision == "modify"
        assert modifications == "update plan"
    finally:
        ws_state._pending_task_previews.pop("session-preview-test", None)
