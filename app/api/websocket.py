from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.db.init_db import now
from app.db.session import afetch_one
from app.api import websocket_state as ws_state
from app.api.websocket_dispatch import dispatch_control_event, dispatch_message_flow
from app.api.websocket_lifecycle import close_websocket_session, open_websocket_session
from app.api.websocket_processor import _process_and_stream  # noqa: F401
from app.services.agent_service import save_message
from app.services.auth_service import websocket_user
from app.services.auth.session_guard import check_session_access

from app.services.websocket_manager import manager

logger = logging.getLogger("agenthub.websocket")

# Business processing (_process_and_stream, _invoke_agent, memory helpers,
# deploy-card/broadcast helpers) lives in websocket_processor — the lane for
# message business logic. This shell owns transport wiring only.


def get_session_exec_permission(session_id: str) -> int:
    """Return the exec_permission for a session (default 1 = ask)."""
    return ws_state.get_session_exec_permission(session_id)


def set_session_exec_permission(session_id: str, mode: int) -> None:
    """Set the exec_permission for a session."""
    ws_state.set_session_exec_permission(session_id, mode)

# Auto-name throttle lives in websocket_state (shared, single owner).


def _should_auto_name(session_id: str) -> bool:
    """Return True if we should attempt auto-naming for this session."""
    return ws_state._should_auto_name(session_id)


async def _request_tool_permission(
    session_id: str,
    tool_name: str,
    arguments: dict,
    risk_level: str,
    reason: str,
    timeout: float = 30.0,
) -> str:
    """Request user permission for a tool call via WebSocket.

    Broadcasts a ``permission_request`` event and waits for the user's
    ``permission_response``. Returns ``"allow"`` or ``"deny"``.
    """
    request_id = str(uuid.uuid4())
    evt = asyncio.Event()
    entry = {"event": evt, "decision": "deny"}
    ws_state._permission_state.setdefault(session_id, {})[request_id] = entry

    try:
        await manager.broadcast(
            session_id,
            {
                "event": "permission_request",
                "sessionId": session_id,
                "requestId": request_id,
                "toolName": tool_name,
                "arguments": arguments,
                "riskLevel": risk_level,
                "reason": reason,
                "timestamp": now(),
            },
        )
        try:
            await asyncio.wait_for(evt.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "permission request timeout session=%s tool=%s",
                session_id, tool_name,
            )
    finally:
        decision = entry.get("decision", "deny")
        ws_state._permission_state.get(session_id, {}).pop(request_id, None)
        if session_id in ws_state._permission_state and not ws_state._permission_state[session_id]:
            del ws_state._permission_state[session_id]
        return decision


def _handle_permission_response(session_id: str, request_id: str, decision: str) -> bool:
    """Signal a waiting permission check with the user's decision.

    Returns True if the request was found and signaled.
    """
    session_entries = ws_state._permission_state.get(session_id, {})
    entry = session_entries.get(request_id)
    if entry:
        entry["decision"] = decision
        entry["event"].set()
        return True
    return False


# PM/PMO interaction response handlers.
# Pending PM interaction state (questions/warnings/todos) and task-preview
# confirmation waits live in websocket_state — single owner, shared with the
# dispatch lane.


async def _wait_for_task_confirmation(
    session_id: str, preview_msg_id: str, token, user_id: str = "",
) -> tuple[str, str]:
    """Wait for the user to confirm/modify/cancel a task preview.

    In multi-user sessions, only the session **owner** has the authority
    to confirm or cancel the DAG.  Members can vote/comment but their
    decisions are treated as advisory — they don't block the flow.

    Returns ``(decision, modifications)``.
    Ownership of the pending-preview state is websocket_state.
    """
    return await ws_state.wait_for_task_confirmation(
        session_id, preview_msg_id, token, user_id=user_id,
    )


def _resolve_pending_task_preview(
    session_id: str, preview_msg_id: str, decision: str, modifications: str = "",
) -> bool:
    """Signal a waiting task preview. Returns True if it was actually pending."""
    return ws_state.resolve_pending_task_preview(
        session_id, preview_msg_id, decision, modifications,
    )




async def _handle_agent_question_response(session_id: str, data: dict, user_id: str = "", user_name: str = "") -> None:
    """User clicked an option on an agent_question bubble."""
    question_msg_id = data.get("questionMessageId", "")
    selected = data.get("selectedOptionId", "")
    custom = data.get("customAnswer", "")
    # Security: derive sender from JWT, NOT from client-supplied data
    sender_name = user_name or "user"
    sender_id = user_id or ""

    # ── First-wins: check if already resolved ────────────────────────
    if not ws_state.mark_interaction_resolved(session_id, question_msg_id, sender_id, sender_name):
        # Already resolved — notify the late responder
        resolver = ws_state.get_resolved_by(session_id, question_msg_id)
        await manager.broadcast_interaction_already_resolved(
            session_id, question_msg_id, resolver or {},
        )
        return

    # Wake up the waiting agent
    session_qs = ws_state._pm_pending_questions.get(session_id, {})
    entry = session_qs.get(question_msg_id)
    if entry:
        entry["response"] = {"selectedOptionId": selected, "customAnswer": custom}
        entry["event"].set()

    # Broadcast to peers so their bubbles update
    await manager.broadcast_interaction_already_resolved(
        session_id, question_msg_id,
        {"resolvedBy": sender_id, "userName": sender_name, "timestamp": now()},
    )

    # Echo the user's choice as a message in the chat
    from app.db.init_db import now as _now
    choice_text = custom or f"[选择了选项: {selected}]"
    await save_message(session_id, sender_name, choice_text, "text", user_id=sender_id)
    await manager.broadcast(session_id, {
        "event": "message",
        "sessionId": session_id,
        "content": choice_text,
        "sender": sender_name,
        "timestamp": _now(),
        "type": "text",
    })


async def _handle_risk_warning_response(session_id: str, data: dict, user_id: str = "", user_name: str = "") -> None:
    """User clicked an action on a risk_warning bubble."""
    warning_msg_id = data.get("warningMessageId", "")
    selected = data.get("selectedActionId", "")
    # Security: derive sender from JWT, NOT from client-supplied data
    sender_name = user_name or "user"
    sender_id = user_id or ""

    # ── First-wins: check if already resolved ────────────────────────
    if not ws_state.mark_interaction_resolved(session_id, warning_msg_id, sender_id, sender_name):
        resolver = ws_state.get_resolved_by(session_id, warning_msg_id)
        await manager.broadcast_interaction_already_resolved(
            session_id, warning_msg_id, resolver or {},
        )
        return

    # Wake up the waiting agent
    session_ws = ws_state._pm_pending_warnings.get(session_id, {})
    entry = session_ws.get(warning_msg_id)
    if entry:
        entry["response"] = {"selectedActionId": selected}
        entry["event"].set()

    # Broadcast to peers
    await manager.broadcast_interaction_already_resolved(
        session_id, warning_msg_id,
        {"resolvedBy": sender_id, "userName": sender_name, "timestamp": now()},
    )

    from app.db.init_db import now as _now
    await save_message(session_id, sender_name,
                       f"[风险应对: {selected}]", "text", user_id=sender_id)
    await manager.broadcast(session_id, {
        "event": "message",
        "sessionId": session_id,
        "content": f"⚠️ 风险应对: {selected}",
        "sender": sender_name,
        "timestamp": _now(),
        "type": "text",
    })


async def _handle_agent_todo_response(session_id: str, data: dict, user_id: str = "", user_name: str = "") -> None:
    """User clicked approve/reject on an agent_todo bubble."""
    todo_msg_id = data.get("todoMessageId", "")
    selected = data.get("selectedActionId", "")
    comment = data.get("comment", "")
    # Security: derive sender from JWT, NOT from client-supplied data
    sender_name = user_name or "user"
    sender_id = user_id or ""

    # ── First-wins: check if already resolved ────────────────────────
    if not ws_state.mark_interaction_resolved(session_id, todo_msg_id, sender_id, sender_name):
        resolver = ws_state.get_resolved_by(session_id, todo_msg_id)
        await manager.broadcast_interaction_already_resolved(
            session_id, todo_msg_id, resolver or {},
        )
        return

    # Wake up the waiting agent
    session_tds = ws_state._pm_pending_todos.get(session_id, {})
    entry = session_tds.get(todo_msg_id)
    if entry:
        entry["response"] = {"selectedActionId": selected, "comment": comment}
        entry["event"].set()

    # Broadcast to peers
    await manager.broadcast_interaction_already_resolved(
        session_id, todo_msg_id,
        {"resolvedBy": sender_id, "userName": sender_name, "timestamp": now()},
    )

    from app.db.init_db import now as _now
    action_label = "批准" if "approve" in selected else ("拒绝" if "reject" in selected else selected)
    await save_message(session_id, sender_name,
                       f"[{action_label}]: {comment or ''}", "text", user_id=sender_id)
    await manager.broadcast(session_id, {
        "event": "message",
        "sessionId": session_id,
        "content": f"📋 {action_label}: {comment or ''}",
        "sender": sender_name,
        "timestamp": _now(),
        "type": "text",
    })


async def _get_user_session_role(session_id: str, user_id: str) -> str:
    """Get the user's role in a session.

    Returns ``"owner"`` if the user is the session owner, ``"member"``
    otherwise.  Falls back to ``"owner"`` when the session has no
    explicit owner record (single-user or legacy mode).
    """
    if not session_id or not user_id:
        return "owner"  # single-user / legacy — full authority
    try:
        row = await afetch_one(
            "SELECT owner_id FROM sessions WHERE id=$1", session_id,
        )
        if row and row.get("owner_id") == user_id:
            return "owner"
        member_row = await afetch_one(
            "SELECT role FROM session_members WHERE session_id=$1 AND user_id=$2",
            session_id, user_id,
        )
        if member_row:
            return member_row.get("role", "member")
        return "owner"
    except Exception:
        return "owner"  # legacy fallback


async def _handle_task_preview_response(
    session_id: str, data: dict, user_id: str, user_name: str,
) -> None:
    """User confirmed or modified a task preview.

    In multi-user sessions, only the session **owner** can confirm or
    cancel the DAG.  Member decisions are recorded as votes and broadcast
    to all participants but do NOT alter the execution flow.  The owner
    still needs to explicitly confirm.
    """
    decision = data.get("decision", "confirm")
    modifications = data.get("modifications", "")
    preview_msg_id = data.get("previewMessageId", "")

    # ── Determine if user is the session owner ────────────────────────
    user_role = await _get_user_session_role(session_id, user_id)

    # ── First-wins: check if already resolved ────────────────────────
    if not ws_state.mark_interaction_resolved(session_id, preview_msg_id, user_id, user_name):
        resolver = ws_state.get_resolved_by(session_id, preview_msg_id)
        await manager.broadcast_interaction_already_resolved(
            session_id, preview_msg_id, resolver or {},
        )
        return

    # ── Non-owner member: record as advisory vote ─────────────────────
    if user_role != "owner":
        entry = ws_state._pending_task_previews.get(session_id, {}).get(preview_msg_id)
        if entry and entry.get("member_votes") is not None:
            entry["member_votes"][user_id] = decision
        action_label = {"confirm": "赞同执行", "cancel": "建议取消", "modify": "建议修改"}.get(decision, decision)
        await save_message(
            session_id, user_name,
            f"[{action_label}]（成员建议）: {modifications or ''}",
            "text", user_id=user_id,
        )
        # Broadcast member vote to all participants
        await manager.broadcast(session_id, {
            "event": "task_vote",
            "sessionId": session_id,
            "previewMessageId": preview_msg_id,
            "userId": user_id,
            "userName": user_name,
            "role": user_role,
            "decision": decision,
            "timestamp": now(),
        })
        logger.info(
            "task_preview member vote session=%s user=%s role=%s decision=%s",
            session_id, user_id, user_role, decision,
        )
        return

    # ── Owner path: full authority to confirm/cancel/modify ──────────
    # Broadcast to peers
    await manager.broadcast_interaction_already_resolved(
        session_id, preview_msg_id,
        {"resolvedBy": user_id, "userName": user_name, "role": "owner", "timestamp": now()},
    )

    # ── If a _process_and_stream call is waiting on this preview,
    #     signal it so it can proceed / cancel / modify in-line ──────
    if ws_state.resolve_pending_task_preview(session_id, preview_msg_id, decision, modifications):
        action_label = {"confirm": "确认执行", "cancel": "取消了任务执行", "modify": "修改计划"}.get(decision, decision)
        await save_message(session_id, user_name, f"[{action_label}]: {modifications or ''}", "text", user_id=user_id)
        return

    if decision == "cancel":
        await save_message(session_id, user_name, "[取消了任务执行]", "text", user_id=user_id)
        await manager.broadcast(session_id, {
            "event": "message", "sessionId": session_id,
            "content": "❌ 任务已由会话 Owner 取消",
            "sender": "system", "timestamp": now(), "type": "system",
        })
    elif decision == "modify":
        modified_content = f"[用户修改了任务计划]\n{modifications}"
        await _process_and_stream(session_id, modified_content, user_name, user_id)
    else:
        await save_message(session_id, user_name, "[确认执行任务计划]", "text", user_id=user_id)
        await manager.broadcast(session_id, {
            "event": "message", "sessionId": session_id,
            "content": "✅ 任务计划已由 Owner 确认，开始执行...",
            "sender": "system", "timestamp": now(), "type": "system",
        })


async def _handle_solution_selection(
    session_id: str, data: dict, user_id: str = "", user_name: str = "",
) -> None:
    """User selected a solution from the solution_proposal bubble.

    Signals the waiting Orchestrator task to proceed with the chosen
    solution (or the recommended one if auto-confirmed).
    """
    solution_id = data.get("solutionId", "")
    auto_selected = data.get("autoSelected", False)

    if session_id in ws_state._solution_selection_events:
        ws_state._solution_selection_results[session_id] = {
            "solutionId": solution_id,
            "autoSelected": auto_selected,
        }
        ws_state._solution_selection_events[session_id].set()
        logger.info(
            "solution_selection: session=%s solution=%s auto=%s user=%s",
            session_id, solution_id, auto_selected, user_id,
        )


async def _handle_diff_decision(session_id: str, data: dict) -> None:
    """User clicked Accept or Reject on a diff bubble from CloudCode."""
    decision = data.get("decision", "reject")
    file_path = data.get("path", "")

    if decision == "accept":
        # Register the accepted file as an artifact
        try:
            import uuid as _uuid

            from app.db.session import aexecute
            from pathlib import Path

            from app.services.workspace_context import get_workspace_root

            ws_root = get_workspace_root()
            full_path = (
                ws_root / file_path
                if not Path(file_path).is_absolute()
                else Path(file_path)
            )
            if full_path.exists() and full_path.is_file():
                content = full_path.read_text(encoding="utf-8", errors="replace")
                await aexecute(
                    "INSERT INTO artifacts(id, session_id, file_path, content, version, created_at) "
                    "VALUES($1,$2,$3,$4,$5,$6)",
                    str(_uuid.uuid4()), session_id, file_path, content, 1, now(),
                )
        except Exception:
            logger.debug("diff_decision artifact registration failed", exc_info=True)

    # Broadcast confirmation
    await manager.broadcast(
        session_id,
        {
            "event": "message",
            "sessionId": session_id,
            "content": f"Diff {file_path}: {'Accepted ✓' if decision == 'accept' else 'Rejected ✗'}",
            "sender": "system",
            "timestamp": now(),
            "type": "system",
        },
    )












def _should_run_memory_tasks(session_id: str) -> bool:
    """Return True if enough time has passed since last memory task run."""
    return ws_state.should_run_memory_tasks(session_id)

router = APIRouter(tags=["websocket"])


def _chunk_text_for_streaming(text: str, chunk_size: int = 60) -> list[str]:
    """Split text for pseudo-stream fallback only.

    Uses a small default chunk size (60 chars) so the frontend sees
    progressive text even when real SSE streaming is unavailable.
    Sentence-aware splitting ensures chunks end at natural boundaries.
    """
    return ws_state.chunk_text_for_streaming(text, chunk_size)


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str, token: str | None = Query(default=None)) -> None:
    user = await websocket_user(token)
    user_id = user["id"]
    user_name = user["name"]

    # ── Session access control ─────────────────────────────────────
    access = await check_session_access(session_id, user)
    return await websocket_endpoint_v2(websocket, session_id, user, access)
def _log_task_error(session_id: str, task: asyncio.Task) -> None:
    exc = task.exception()
    if exc:
        logger.error("ws background task failed session=%s: %s", session_id, exc)



async def websocket_endpoint_v2(websocket: WebSocket, session_id: str, user: dict, access) -> None:
    user_id = user["id"]
    user_name = user["name"]

    conn_id, heartbeat_task = await open_websocket_session(
        session_id=session_id,
        websocket=websocket,
        user_id=user_id,
        user_name=user_name,
        role=access.role.value,
    )

    try:
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                break

            if await dispatch_control_event(
                session_id=session_id,
                data=data,
                websocket=websocket,
                user_id=user_id,
                user_name=user_name,
                conn_id=conn_id,
                on_agent_question_response=_handle_agent_question_response,
                on_risk_warning_response=_handle_risk_warning_response,
                on_agent_todo_response=_handle_agent_todo_response,
                on_task_preview_response=_handle_task_preview_response,
                on_solution_selection=_handle_solution_selection,
                on_diff_decision=_handle_diff_decision,
            ):
                continue

            content = str(data.get("content", "")).strip()
            if await dispatch_message_flow(
                session_id=session_id,
                content=content,
                sender=user_name,
                user_id=user_id,
                access_can_write=access.can_write,
                websocket=websocket,
                data=data,
                attachments=data.get("attachments", []),
                quote_references=data.get("quoteReferences", []),
                auto_reply=data.get("auto_reply", True),
                process_and_stream=_process_and_stream,
                log_task_error=_log_task_error,
            ):
                continue
    except WebSocketDisconnect:
        pass
    finally:
        await close_websocket_session(
            session_id=session_id,
            websocket=websocket,
            user_id=user_id,
            user_name=user_name,
            heartbeat_task=heartbeat_task,
        )












