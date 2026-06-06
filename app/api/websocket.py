from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.db.init_db import now
from app.db.session import afetch_all, afetch_one
from app.services.agent_service import extract_mentions, save_message
from app.services.auth_service import websocket_user
from app.services.message_router import route_message, stream_message

from app.services.guardrails import scan_input as _guardrails_scan
from app.services.websocket_manager import manager

logger = logging.getLogger("agenthub.websocket")

# ── auto-memory extraction (lazy singletons) ────────────────────────
_memory_extractor = None  # type: ignore
_session_mgr_singleton = None  # type: ignore
# Per-session throttle: {session_id: last_run_timestamp}
_throttle_state: dict[str, float] = {}
_THROTTLE_SECONDS = 90  # min interval between background memory tasks per session

# ── Permission request state (session-scoped async Events) ─────────
# {session_id: {request_id: {"event": asyncio.Event, "decision": str}}}
_permission_state: dict[str, dict[str, dict]] = {}

# ── Exec permission mode per session ───────────────────────────────
# 1 = 询问 (ask), 2 = 跳过 (bypass), 3 = 计划 (plan/read-only)
_session_exec_permission: dict[str, int] = {}


def get_session_exec_permission(session_id: str) -> int:
    """Return the exec_permission for a session (default 1 = ask)."""
    return _session_exec_permission.get(session_id, 1)


def set_session_exec_permission(session_id: str, mode: int) -> None:
    """Set the exec_permission for a session."""
    if mode in (1, 2, 3):
        _session_exec_permission[session_id] = mode

# Auto-name throttle: separate from memory tasks — fires more aggressively
# for the first few messages, then backs off
_auto_name_state: dict[str, tuple[float, int]] = {}
_AUTO_NAME_INITIAL_SECONDS = 15  # quick check after first messages
_AUTO_NAME_BACKOFF_SECONDS = 120  # slower after initial attempts
_AUTO_NAME_MAX_ATTEMPTS = 5  # stop trying after this many attempts


def _should_auto_name(session_id: str) -> bool:
    """Return True if we should attempt auto-naming for this session.

    Always fires on the very first attempt (attempts == 0) so that
    new sessions get named immediately after the first agent response.
    Subsequent attempts are throttled.
    """
    import time
    now_ts = time.monotonic()
    last_ts, attempts = _auto_name_state.get(session_id, (0.0, 0))
    if attempts >= _AUTO_NAME_MAX_ATTEMPTS:
        return False
    # Always allow the very first attempt — no throttle
    if attempts == 0:
        _auto_name_state[session_id] = (now_ts, 1)
        return True
    interval = _AUTO_NAME_INITIAL_SECONDS if attempts < 2 else _AUTO_NAME_BACKOFF_SECONDS
    if now_ts - last_ts >= interval:
        _auto_name_state[session_id] = (now_ts, attempts + 1)
        return True
    return False


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
    _permission_state.setdefault(session_id, {})[request_id] = entry

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
        _permission_state.get(session_id, {}).pop(request_id, None)
        if session_id in _permission_state and not _permission_state[session_id]:
            del _permission_state[session_id]
        return decision


def _handle_permission_response(session_id: str, request_id: str, decision: str) -> bool:
    """Signal a waiting permission check with the user's decision.

    Returns True if the request was found and signaled.
    """
    session_entries = _permission_state.get(session_id, {})
    entry = session_entries.get(request_id)
    if entry:
        entry["decision"] = decision
        entry["event"].set()
        return True
    return False


# ── PM/PMO interaction response handlers ───────────────────────────────
# Store pending PM interaction state so the agent can await user responses.

# {session_id: {message_id: {"event": asyncio.Event, "response": dict}}}
_pm_pending_questions: dict[str, dict[str, dict]] = {}
_pm_pending_warnings: dict[str, dict[str, dict]] = {}
_pm_pending_todos: dict[str, dict[str, dict]] = {}


async def _handle_agent_question_response(session_id: str, data: dict) -> None:
    """User clicked an option on an agent_question bubble."""
    question_msg_id = data.get("questionMessageId", "")
    selected = data.get("selectedOptionId", "")
    custom = data.get("customAnswer", "")
    session_qs = _pm_pending_questions.get(session_id, {})
    entry = session_qs.get(question_msg_id)
    if entry:
        entry["response"] = {"selectedOptionId": selected, "customAnswer": custom}
        entry["event"].set()
    # Echo the user's choice as a message in the chat
    from app.db.init_db import now as _now
    choice_text = custom or f"[选择了选项: {selected}]"
    await save_message(session_id, data.get("sender", "user"), choice_text, "text")
    await manager.broadcast(session_id, {
        "event": "message",
        "sessionId": session_id,
        "content": choice_text,
        "sender": data.get("sender", "user"),
        "timestamp": _now(),
        "type": "text",
    })


async def _handle_risk_warning_response(session_id: str, data: dict) -> None:
    """User clicked an action on a risk_warning bubble."""
    warning_msg_id = data.get("warningMessageId", "")
    selected = data.get("selectedActionId", "")
    session_ws = _pm_pending_warnings.get(session_id, {})
    entry = session_ws.get(warning_msg_id)
    if entry:
        entry["response"] = {"selectedActionId": selected}
        entry["event"].set()
    from app.db.init_db import now as _now
    await save_message(session_id, data.get("sender", "user"),
                       f"[风险应对: {selected}]", "text")
    await manager.broadcast(session_id, {
        "event": "message",
        "sessionId": session_id,
        "content": f"⚠️ 风险应对: {selected}",
        "sender": data.get("sender", "user"),
        "timestamp": _now(),
        "type": "text",
    })


async def _handle_agent_todo_response(session_id: str, data: dict) -> None:
    """User clicked approve/reject on an agent_todo bubble."""
    todo_msg_id = data.get("todoMessageId", "")
    selected = data.get("selectedActionId", "")
    comment = data.get("comment", "")
    session_tds = _pm_pending_todos.get(session_id, {})
    entry = session_tds.get(todo_msg_id)
    if entry:
        entry["response"] = {"selectedActionId": selected, "comment": comment}
        entry["event"].set()
    from app.db.init_db import now as _now
    action_label = "批准" if "approve" in selected else ("拒绝" if "reject" in selected else selected)
    await save_message(session_id, data.get("sender", "user"),
                       f"[{action_label}]: {comment or ''}", "text")
    await manager.broadcast(session_id, {
        "event": "message",
        "sessionId": session_id,
        "content": f"📋 {action_label}: {comment or ''}",
        "sender": data.get("sender", "user"),
        "timestamp": _now(),
        "type": "text",
    })


async def _handle_task_preview_response(
    session_id: str, data: dict, user_id: str, user_name: str,
) -> None:
    """User confirmed or modified a task preview."""
    decision = data.get("decision", "confirm")
    modifications = data.get("modifications", "")
    from app.db.init_db import now as _now
    if decision == "cancel":
        await save_message(session_id, user_name, "[取消了任务执行]", "text")
        await manager.broadcast(session_id, {
            "event": "message", "sessionId": session_id,
            "content": "❌ 任务已取消",
            "sender": "system", "timestamp": _now(), "type": "system",
        })
    elif decision == "modify":
        # Re-process with user modifications
        modified_content = f"[用户修改了任务计划]\n{modifications}"
        await _process_and_stream(session_id, modified_content, user_name, user_id)
    else:
        # Confirm — the task execution continues in the current flow
        await save_message(session_id, user_name, "[确认执行任务计划]", "text")
        await manager.broadcast(session_id, {
            "event": "message", "sessionId": session_id,
            "content": "✅ 任务计划已确认，开始执行...",
            "sender": "system", "timestamp": _now(), "type": "system",
        })


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

            from app.config import PROJECT_ROOT

            full_path = (
                Path(PROJECT_ROOT) / file_path
                if not Path(file_path).is_absolute()
                else Path(file_path)
            )
            if full_path.exists() and full_path.is_file():
                content = full_path.read_text(encoding="utf-8", errors="replace")
                await aexecute(
                    "INSERT INTO artifacts(id, session_id, file_path, content, version, created_at) "
                    "VALUES($1,$2,$3,$4,$5,$6)",
                    str(_uuid.uuid4()), session_id, file_path, content, 1, _now(),
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
            "timestamp": _now(),
            "type": "system",
        },
    )


async def _auto_name_and_broadcast(session_id: str) -> None:
    """Background task: generate an auto-name and broadcast it to connected clients."""
    try:
        from app.api.chat import is_generic_name, try_auto_name_session

        # Quick pre-check: skip if already has a meaningful name
        row = await afetch_one("SELECT name FROM sessions WHERE id=$1", session_id)
        if row and not is_generic_name(row.get("name") or ""):
            return  # already named

        new_name = await try_auto_name_session(session_id)
        if new_name:
            # Reset attempts: we found a good name, no need to keep trying
            _auto_name_state.pop(session_id, None)

            await manager.broadcast(
                session_id,
                {
                    "event": "session_renamed",
                    "sessionId": session_id,
                    "name": new_name,
                    "timestamp": now(),
                },
            )
    except Exception:
        logger.debug("auto-name background task failed for %s", session_id, exc_info=True)


def _get_memory_extractor():
    global _memory_extractor
    if _memory_extractor is None:
        from app.services.memory import MemoryExtractor
        _memory_extractor = MemoryExtractor()
    return _memory_extractor


def _get_session_mgr():
    global _session_mgr_singleton
    if _session_mgr_singleton is None:
        from app.services.memory.session_memory import SessionMemoryManager
        _session_mgr_singleton = SessionMemoryManager()
    return _session_mgr_singleton


def _should_run_memory_tasks(session_id: str) -> bool:
    """Return True if enough time has passed since last memory task run."""
    import time
    now_ts = time.monotonic()
    last = _throttle_state.get(session_id, 0.0)
    if now_ts - last >= _THROTTLE_SECONDS:
        _throttle_state[session_id] = now_ts
        return True
    return False

router = APIRouter(tags=["websocket"])


def _chunk_text_for_streaming(text: str, chunk_size: int = 120) -> list[str]:
    """Split text for pseudo-stream fallback only."""
    if not text:
        return []
    chunks: list[str] = []
    buf = ""
    separators = "，。！？；：,.!?;:\n"
    for ch in text:
        buf += ch
        if len(buf) >= chunk_size or ch in separators:
            chunks.append(buf)
            buf = ""
    if buf:
        chunks.append(buf)
    return chunks


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str, token: str | None = Query(default=None)) -> None:
    user = await websocket_user(token)
    user_id = user["id"]
    conn_id = await manager.connect(session_id, websocket, user_id)

    # Start per-connection heartbeat
    heartbeat_task = asyncio.create_task(
        manager.heartbeat_loop(session_id, conn_id, websocket)
    )

    try:
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                break

            # Handle client pong responses
            if data.get("event") == "pong":
                manager.record_pong(session_id, conn_id)
                continue

            # Handle reconnection sync request
            if data.get("event") == "sync_request":
                last_id = data.get("lastMessageId")
                count = await manager.replay_missed_messages(session_id, websocket, last_id)
                await manager._send_safe(websocket, {
                    "event": "sync_complete",
                    "sessionId": session_id,
                    "replayed": count,
                })
                continue

            # Handle permission response from frontend
            if data.get("event") == "permission_response":
                request_id = data.get("requestId", "")
                decision = data.get("decision", "deny")
                if request_id:
                    _handle_permission_response(session_id, request_id, decision)
                continue

            # ── PM/PMO interaction responses ──────────────────────────
            if data.get("event") == "agent_question_response":
                await _handle_agent_question_response(session_id, data)
                continue

            if data.get("event") == "risk_warning_response":
                await _handle_risk_warning_response(session_id, data)
                continue

            if data.get("event") == "agent_todo_response":
                await _handle_agent_todo_response(session_id, data)
                continue

            if data.get("event") == "task_preview_response":
                await _handle_task_preview_response(session_id, data, user_id, user["name"])
                continue

            if data.get("event") == "diff_decision":
                await _handle_diff_decision(session_id, data)
                continue

            content = str(data.get("content", "")).strip()
            if not content:
                continue

            # ── Store exec permission mode for this session ────────
            exec_perm = data.get("exec_permission")
            if isinstance(exec_perm, int) and exec_perm in (1, 2, 3):
                set_session_exec_permission(session_id, exec_perm)
                # Also sync to the shared permission module store
                from app.services.tools.permission import set_exec_permission
                set_exec_permission(session_id, exec_perm)

            # Cancel any in-flight stream — but ONLY if there is one.
            # 无条件 cancel 会让上一轮尚未走完模型循环的 invocation 看到 token.cancelled=True
            # 并返回 "流式响应已被中断"。 先用 has_active_stream 守卫，避免误中断。
            if manager.has_active_stream(session_id):
                manager.cancel_token(session_id)
                await manager.send_stream_interrupted(session_id, "New message received, interrupting current stream")
                # 等旧任务释放 session 锁；锁释放后新任务才能进入 _process_and_stream。
                lock = manager.get_session_lock(session_id)
                # 最多等 2s；拿不到就直接放行（让新消息处理，新任务内部 create_token 会再取消一次）。
                try:
                    await asyncio.wait_for(lock.acquire(), timeout=2.0)
                    lock.release()
                except asyncio.TimeoutError:
                    logger.debug("ws interrupt wait timeout session=%s", session_id)
                await asyncio.sleep(0.02)

            task = asyncio.create_task(
                _process_and_stream(
                    session_id, content,
                    data.get("sender", user["name"]),
                    user_id,
                    data.get("attachments", []),
                    quote_references=data.get("quoteReferences", []),
                )
            )
            task.add_done_callback(
                lambda t: _log_task_error(session_id, t) if t.exception() else None
            )

    except WebSocketDisconnect:
        pass
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except (asyncio.CancelledError, Exception):
            pass
        manager.disconnect(session_id, websocket)
        # Only teardown if this was the last connection
        if manager._connection_count(session_id) == 0:
            manager.teardown_session(session_id)


def _log_task_error(session_id: str, task: asyncio.Task) -> None:
    exc = task.exception()
    if exc:
        logger.error("ws background task failed session=%s: %s", session_id, exc)



def _build_safety_block_message(result) -> str:
    """Build a human-readable safety block message from guardrail flags."""
    lines = [
        "🚫 **安全护栏检测 — 消息已被阻断**\n",
        "您的输入触发了以下安全红线，消息未被发送给 Agent：\n",
    ]
    for f in result.flags:
        cat_label = {
            "pii": "🔒 隐私信息泄露",
            "injection": "🛡️ 注入攻击检测",
            "harmful": "⚠️ 有害内容检测",
        }.get(f.category.value, f.category.value)
        lines.append(f"- {cat_label}：{f.message}")
    lines.append("\n---\n")
    lines.append("请移除敏感信息后重试。如有疑问，请联系系统管理员。")
    return "\n".join(lines)


async def _process_and_stream(
    session_id: str,
    content: str,
    sender: str,
    user_id: str,
    attachments: list[dict] | None = None,
    quote_references: list[dict] | None = None,
) -> None:
    """Process one user message with the per-session lock held."""
    lock = manager.get_session_lock(session_id)
    async with lock:
        token = manager.create_token(session_id)

        try:
            await save_message(session_id, sender, content, "text")

            # ── Safety guardrail scan (Tier 1: auto-block) ──────────
            guard_result = _guardrails_scan(content)
            if guard_result.blocked:
                block_msg = _build_safety_block_message(guard_result)
                await manager.broadcast(
                    session_id,
                    {
                        "event": "message",
                        "sessionId": session_id,
                        "content": block_msg,
                        "sender": "system",
                        "timestamp": now(),
                        "type": "system",
                        "guardrailResult": guard_result.to_dict(),
                    },
                )
                logger.warning(
                    "ws guardrail blocked session=%s flags=%s",
                    session_id,
                    [f"{f.rule}:{f.message[:60]}" for f in guard_result.flags],
                )
                return  # stop — don't route to agent

            # ── Skill invocation detection ──────────────────────────
            # Detect /skill-name patterns and inject SKILL.md body as
            # system prompt context before routing to agents.
            from app.services.agent_service import extract_skill_calls, load_skill_prompt

            skill_names = extract_skill_calls(content)
            skill_context = ""
            if skill_names:
                loaded_skills: list[str] = []
                for sn in skill_names:
                    body = await load_skill_prompt(sn)
                    if body:
                        loaded_skills.append(
                            f"## 技能：{sn}\n\n{body}"
                        )
                        logger.info(
                            "ws skill loaded session=%s skill=%s body_len=%d",
                            session_id, sn, len(body),
                        )
                    else:
                        logger.debug(
                            "ws skill not found session=%s skill=%s",
                            session_id, sn,
                        )
                if loaded_skills:
                    skill_context = (
                        "你正在执行以下技能指令。请严格遵循每个技能的定义、规则和输出格式。\n\n"
                        + "\n\n---\n\n".join(loaded_skills)
                        + "\n\n---\n\n## 用户消息\n\n"
                    )
                    # Prepend skill context to the user content
                    content = skill_context + content

            mentioned = extract_mentions(content)
            target_agents: list[dict] = []
            seen: set[str] = set()
            for name in mentioned:
                if name in seen:
                    continue
                seen.add(name)
                row = await afetch_one(
                    "SELECT agent_id,domain,status,adapter_type,risk_level FROM agent_registry WHERE agent_id=$1",
                    name,
                )
                if row:
                    target_agents.append(row)

            # ── Multi-agent path (Architect-driven DAG execution) ──────
            if len(target_agents) >= 2:
                from app.services.agent_service import CollaborationContext
                from app.services.task_decomposer import task_decomposer
                from app.services.dag_executor import DAGExecutor
                from app.services.result_synthesizer import result_synthesizer

                collab = CollaborationContext(content)
                for a in target_agents:
                    collab.register(a)

                # ── Step 1: Architect-driven task decomposition ─────────
                # Use the Architect LLM to decompose the user request into
                # a structured DAG, falling back to keyword templates.
                try:
                    dag_config = await task_decomposer.decompose(
                        content=content,
                        session_id=session_id,
                        agents=target_agents,
                    )
                except Exception:
                    # Last-resort fallback
                    from app.schemas.dag import DAGConfig
                    dag_config = DAGConfig(
                        total=len(target_agents),
                        completed=0,
                        nodes=[{
                            "id": f"n{i}", "domain": a.get("domain", "general"),
                            "agent": a["agent_id"],
                            "description": f"执行 {a['agent_id']} 的任务",
                            "dependencies": [f"n{j}" for j in range(i)] if i > 0 else [],
                        } for i, a in enumerate(target_agents)],
                        execution_strategy="sequential",
                        analysis=f"自动分解为 {len(target_agents)} 个节点",
                    )

                # ── 🟡 Trigger 1: Task Preview with real DAG ────────
                task_preview_msg_id = str(uuid.uuid4())
                task_items = []
                for node in dag_config.nodes:
                    domain_label = {
                        "orchestrator": "协调调度", "architect": "架构设计",
                        "codegen": "代码生成", "review": "代码审查",
                        "test": "测试验证", "deploy": "部署发布",
                    }.get(node.domain, node.domain)
                    task_items.append({
                        "id": node.id,
                        "description": f"{node.agent} ({domain_label}): {node.description}",
                        "agent": node.agent,
                        "dependencies": node.dependencies,
                        "estimatedSeconds": {"low": 20, "medium": 45, "high": 90}.get(
                            node.estimated_effort, 45
                        ),
                    })
                await manager.broadcast_task_preview(
                    session_id, task_preview_msg_id, task_items,
                    eta_seconds=sum(t.get("estimatedSeconds", 45) for t in task_items),
                )

                # ── Step 2: Execute DAG with real agent invocations ──
                async def _dag_invoke(sid, agent_id, content, extra_context=""):
                    agent_row = await afetch_one(
                        "SELECT * FROM agent_registry WHERE agent_id=$1", agent_id,
                    )
                    if not agent_row:
                        return f"[错误] Agent '{agent_id}' 未在注册表中找到"
                    collab_ctx = collab.context_for(agent_id) if collab else ""
                    full = f"{collab_ctx}\n\n{content}"
                    if extra_context:
                        full = f"{extra_context}\n\n{full}"
                    result = await _invoke_agent(
                        sid, full, agent_row, user_id, token,
                        attachments or [],
                        collab_ctx=collab_ctx,
                        quote_references=quote_references,
                    )
                    if result:
                        collab.record(agent_id, agent_row.get("domain", ""), result)
                    return result or ""

                executor = DAGExecutor(
                    session_id=session_id,
                    manager=manager,
                    invoke_fn=_dag_invoke,
                    on_node_update=None,
                )

                try:
                    node_results = await executor.execute(dag_config, collab)
                except Exception as exc:
                    logger.warning("DAG execution error: %s", exc)

                # ── Step 3: Synthesize results ────────────────────────
                if not token.cancelled:
                    async def _synthesize_invoke(prompt):
                        architect_row = await afetch_one(
                            "SELECT * FROM agent_registry WHERE agent_id='Architect'",
                        )
                        if not architect_row:
                            return None
                        return await _invoke_agent(
                            session_id, prompt, architect_row, user_id, token,
                            attachments or [],
                            collab_ctx="",
                            quote_references=None,
                        )

                    final_response = await result_synthesizer.synthesize(
                        dag=dag_config,
                        node_results=executor.node_results,
                        original_request=content,
                        invoke_fn=_synthesize_invoke,
                    )

                    if final_response and not token.cancelled:
                        # Save final synthesized message
                        await save_message(
                            session_id, final_response, "Architect",
                            "text", None, None,
                        )
                        await manager.broadcast(
                            session_id,
                            {
                                "event": "message",
                                "sessionId": session_id,
                                "content": final_response,
                                "sender": "Architect",
                                "timestamp": now(),
                                "type": "text",
                            },
                        )

                # ── 🟡 Trigger 5: Agent Todo ────────────────────────────
                # After all agents complete, broadcast a follow-up todo
                # list suggesting next steps based on which agents ran.
                if not token.cancelled:
                    todo_items = []
                    agent_domains = {a.get("domain", "") for a in target_agents}
                    if "codegen" in agent_domains or "CodeGen" in {a["agent_id"] for a in target_agents}:
                        todo_items.append({
                            "id": "todo_test",
                            "label": "运行测试验证生成的代码",
                            "intent": "approve",
                            "description": "建议运行单元测试和集成测试确保代码正确性",
                        })
                    if "review" in agent_domains or "Review" in {a["agent_id"] for a in target_agents}:
                        todo_items.append({
                            "id": "todo_fix",
                            "label": "根据审查意见修改代码",
                            "intent": "approve",
                            "description": "Review Agent 已提出修改建议，请检查并应用",
                        })
                    if "deploy" in agent_domains or "Deploy" in {a["agent_id"] for a in target_agents}:
                        todo_items.append({
                            "id": "todo_verify_deploy",
                            "label": "验证部署结果",
                            "intent": "approve",
                            "description": "检查生产环境是否正常运行，监控日志和指标",
                        })
                    # Always include a generic follow-up
                    todo_items.append({
                        "id": "todo_feedback",
                        "label": "提供反馈或继续迭代",
                        "intent": "approve",
                        "description": "如果结果不满意可以提出修改意见，或开启新一轮协作",
                    })
                    await manager.broadcast_agent_todo(
                        session_id,
                        str(uuid.uuid4()),
                        "PM",
                        "协作完成 — 建议后续步骤",
                        f"以下 Agent 已完成本轮协作：{', '.join(a['agent_id'] for a in target_agents)}。建议检查结果并继续推进：",
                        todo_items,
                        priority="medium",
                    )

                return

            # ── Single-agent path ─────────────────────────────────────
            agent = target_agents[0] if target_agents else None

            # ── 🟡 Trigger 1 (single): Task Preview ───────────────────
            # For single-agent invocations, broadcast a lightweight task
            # preview so the user knows which agent will process their request.
            if agent and not token.cancelled:
                agent_domain_label = {
                    "orchestrator": "协调调度", "architect": "架构设计",
                    "codegen": "代码生成", "review": "代码审查",
                    "test": "测试验证", "deploy": "部署发布",
                }.get(agent.get("domain", ""), agent.get("domain", "general"))
                await manager.broadcast_task_preview(
                    session_id,
                    str(uuid.uuid4()),
                    [{
                        "id": "task_0",
                        "description": f"{agent['agent_id']} ({agent_domain_label}): 处理您的请求",
                        "agent": agent["agent_id"],
                        "dependencies": [],
                        "estimatedSeconds": 30,
                    }],
                    eta_seconds=30,
                )

            await _invoke_agent(
                session_id, content, agent, user_id, token, attachments or [],
                sender_override=sender,
                quote_references=quote_references,
            )

            # ── 🟡 Trigger 5 (single): Agent Todo ──────────────────────
            # After single agent completes, suggest logical next steps.
            if agent and not token.cancelled:
                single_todo_items = [{
                    "id": "todo_feedback",
                    "label": "检查结果并提供反馈",
                    "intent": "approve",
                    "description": f"{agent['agent_id']} 已完成处理，请检查结果是否符合预期",
                }]
                agent_domain = agent.get("domain", "")
                if agent_domain == "codegen" or agent["agent_id"] == "CodeGen":
                    single_todo_items.insert(0, {
                        "id": "todo_review",
                        "label": "发送代码审查",
                        "intent": "approve",
                        "description": "建议 @Review 审查生成的代码质量和安全性",
                    })
                elif agent_domain == "review" or agent["agent_id"] == "Review":
                    single_todo_items.insert(0, {
                        "id": "todo_apply_fixes",
                        "label": "应用审查建议",
                        "intent": "approve",
                        "description": "根据 Review 的意见修改代码，然后 @CodeGen 重新生成",
                    })
                await manager.broadcast_agent_todo(
                    session_id,
                    str(uuid.uuid4()),
                    agent["agent_id"],
                    f"{agent['agent_id']} 任务完成",
                    f"Agent 已完成本轮处理。建议下一步：",
                    single_todo_items,
                    priority="low",
                )

        except Exception:
            logger.exception("ws _process_and_stream failed session=%s", session_id)
            if not token.cancelled:
                # Include the actual error so the user can see what went wrong,
                # rather than a generic "模型调用失败" that hides the root cause.
                import traceback as _tb
                error_detail = _tb.format_exc()
                # Only show the last 3 lines (the actual exception) — not the full traceback
                error_lines = [L for L in error_detail.strip().split('\n') if L.strip()]
                short_error = '\n'.join(error_lines[-4:]) if len(error_lines) > 4 else '\n'.join(error_lines)
                await manager.broadcast(
                    session_id,
                    {
                        "event": "message",
                        "sessionId": session_id,
                        "content": f"模型调用失败：\n{short_error}",
                        "sender": "system",
                        "timestamp": now(),
                        "type": "system",
                    },
                )
        finally:
            manager.remove_token(session_id, token)

    # ── Auto memory tasks (background, non-blocking, throttled) ───
    # Both extraction and summarization fire in background after a message.
    # Throttled: only run once every _THROTTLE_SECONDS per session to avoid
    # firing expensive LLM calls on every single message.
    if _should_run_memory_tasks(session_id):
        try:
            from app.config import AUTO_MEMORY_ENABLED
            if AUTO_MEMORY_ENABLED:
                extractor = _get_memory_extractor()
                asyncio.create_task(extractor.extract_from_session(session_id))
        except Exception:
            logger.debug("auto-memory extraction init failed", exc_info=True)

        try:
            session_mgr = _get_session_mgr()
            asyncio.create_task(session_mgr.update_session_summary(session_id))
        except Exception:
            logger.debug("session-memory update init failed", exc_info=True)

    # ── Auto session naming (background, non-blocking) ────────
    # Fire after every message for sessions with generic names.
    # Runs on its own throttle separate from memory tasks.
    if _should_auto_name(session_id):
        asyncio.create_task(_auto_name_and_broadcast(session_id))


async def _invoke_agent(
    session_id: str,
    content: str,
    agent: dict | None,
    user_id: str,
    token,
    attachments: list[dict],
    collab_ctx: str = "",
    sender_override: str | None = None,
    quote_references: list[dict] | None = None,
) -> str:
    """Invoke a single agent — streaming first, non-streaming fallback.

    Returns the agent's full response text (for collaboration context recording).
    """
    agent_id = agent["agent_id"] if agent else (sender_override or "Orchestrator")

    # ── Broadcast "agent_thinking" so the frontend shows a streaming
    #     indicator immediately, even during the tool-call loop phase
    #     where no message_chunks are emitted yet. This prevents the UI
    #     from appearing frozen while the agent reasons + calls tools.
    thinking_msg_id = str(uuid.uuid4())
    await manager.broadcast(
        session_id,
        {
            "event": "agent_thinking",
            "sessionId": session_id,
            "messageId": thinking_msg_id,
            "agentId": agent_id,
            "phase": "analyzing",
            "details": "正在分析需求，判断是否需要调用工具...",
            "timestamp": now(),
        },
    )

    # ── Tool event callback for WebSocket broadcast ─────────────────
    msg_id_for_tools = str(uuid.uuid4())
    thinking_sent_iterations = 0  # track tool loop iterations for progress

    async def _on_tool_event(status: str, tool_calls: list[dict], tool_results: list[dict] | None) -> None:
        """Broadcast tool call/result events and progress updates to the frontend."""
        nonlocal thinking_sent_iterations
        if status == "calling":
            # Send tool_call event so the frontend can render tool-call bubbles
            tool_names = [tc.get("name", "unknown") for tc in tool_calls]
            await manager.broadcast(
                session_id,
                {
                    "event": "tool_call",
                    "sessionId": session_id,
                    "messageId": msg_id_for_tools,
                    "toolCalls": [
                        {"name": tc.get("name", ""), "arguments": tc.get("arguments", {}), "status": "calling"}
                        for tc in tool_calls
                    ],
                    "timestamp": now(),
                },
            )
            # Also update the thinking indicator to show tool execution phase
            thinking_sent_iterations += 1
            await manager.broadcast(
                session_id,
                {
                    "event": "agent_thinking",
                    "sessionId": session_id,
                    "messageId": thinking_msg_id,
                    "agentId": agent_id,
                    "phase": "executing",
                    "details": f"正在调用工具: {', '.join(tool_names)} (第{thinking_sent_iterations}轮)",
                    "timestamp": now(),
                },
            )

            # ── 🟡 Trigger 4: Risk Warning ──────────────────────────
            # When the tool-call loop detects high-risk operations, broadcast
            # a risk_warning event so the PM proactively alerts the user.
            # tool_calls here may contain risk-classified entries with
            # {"name", "arguments", "risk": {...}} from classify_tool_risk.
            for tc in tool_calls:
                risk = tc.get("risk")
                if risk and risk.get("flags"):
                    risk_flags = risk.get("flags", [])
                    risk_level = "critical" if any(
                        f.get("severity") == "block" for f in risk_flags
                    ) else "high" if any(
                        f.get("severity") == "confirm" for f in risk_flags
                    ) else "medium"
                    risk_msgs = [f.get("message", "") for f in risk_flags if f.get("message")]
                    await manager.broadcast_risk_warning(
                        session_id,
                        str(uuid.uuid4()),
                        agent_id,
                        risk_level,
                        f"高风险操作: {tc.get('name', 'unknown')}",
                        "; ".join(risk_msgs) or f"工具 {tc.get('name', 'unknown')} 需要确认",
                        [
                            {"id": "continue", "label": "继续执行", "intent": "continue"},
                            {"id": "cancel", "label": "取消操作", "intent": "cancel"},
                        ],
                    )

        elif status == "risk_warning":
            # ── 🟡 Trigger 4 (from tool loop): High-risk tool detected ──
            # The _run_tool_call_loop sends risk_warning status with
            # high_risk_tools list [{name, arguments, risk}].
            for rt in tool_calls:
                risk_info = rt.get("risk", {})
                risk_flags = risk_info.get("flags", [])
                risk_level = "critical" if any(
                    f.get("severity") == "block" for f in risk_flags
                ) else "high" if any(
                    f.get("severity") == "confirm" for f in risk_flags
                ) else "medium"
                risk_msgs = [f.get("message", "") for f in risk_flags if f.get("message")]
                await manager.broadcast_risk_warning(
                    session_id,
                    str(uuid.uuid4()),
                    agent_id,
                    risk_level,
                    f"⚠️ 高风险操作: {rt.get('name', 'unknown')}",
                    "; ".join(risk_msgs) or f"Agent 尝试执行高风险工具 {rt.get('name', 'unknown')}，需用户确认",
                    [
                        {"id": "continue", "label": "继续执行", "intent": "continue"},
                        {"id": "cancel", "label": "取消操作", "intent": "cancel"},
                    ],
                )

        elif status == "done" and tool_results:
            await manager.broadcast(
                session_id,
                {
                    "event": "tool_result",
                    "sessionId": session_id,
                    "messageId": msg_id_for_tools,
                    "results": [
                        {
                            "tool_name": tr.get("tool_name", ""),
                            "success": tr.get("success", False),
                            "result": tr.get("result") if tr.get("success") else None,
                            "error": tr.get("error") if not tr.get("success") else None,
                        }
                        for tr in tool_results
                    ],
                    "timestamp": now(),
                },
            )
            # Update thinking: now synthesizing results
            await manager.broadcast(
                session_id,
                {
                    "event": "agent_thinking",
                    "sessionId": session_id,
                    "messageId": thinking_msg_id,
                    "agentId": agent_id,
                    "phase": "synthesizing",
                    "details": "工具执行完成，正在综合结果生成回复...",
                    "timestamp": now(),
                },
            )

            # ── 🟡 Trigger 3: Progress Update ───────────────────────
            # After each tool iteration completes, broadcast progress so
            # the frontend ProgressBubble updates in real time.
            # Estimate remaining iterations based on results complexity.
            total_estimate = max(thinking_sent_iterations + 1, thinking_sent_iterations + len(tool_results))
            await manager.broadcast_progress_update(
                session_id,
                str(uuid.uuid4()),
                agent_id,
                thinking_sent_iterations,  # completed steps
                total_estimate,             # estimated total
                f"第 {thinking_sent_iterations} 轮工具调用完成",
            )

            # ── 🟡 Trigger 2: Agent Question detection ──────────────
            # Detect when tool results indicate ambiguity or the agent
            # needs user clarification (e.g., multiple file matches,
            # ambiguous search results, conflicting configurations).
            for tr in tool_results:
                result_data = tr.get("result")
                if isinstance(result_data, dict):
                    # Check for explicit ask_user flag from tools
                    if result_data.get("ask_user"):
                        question_text = result_data.get("question", "需要您的确认")
                        opts = result_data.get("options", [])
                        await manager.broadcast_agent_question(
                            session_id,
                            str(uuid.uuid4()),
                            agent_id,
                            str(question_text),
                            opts if isinstance(opts, list) else [],
                            allow_custom=True,
                        )
                    # Check for ambiguity markers in tool output
                    ambiguity = result_data.get("ambiguous") or result_data.get("multiple_matches")
                    if ambiguity and isinstance(ambiguity, list) and len(ambiguity) > 1:
                        options = [
                            {"id": f"opt_{i}", "label": str(item.get("name", item)), "description": str(item.get("path", ""))}
                            for i, item in enumerate(ambiguity[:5])
                        ]
                        await manager.broadcast_agent_question(
                            session_id,
                            str(uuid.uuid4()),
                            agent_id,
                            f"找到 {len(ambiguity)} 个匹配项，请选择目标：",
                            options,
                            allow_custom=True,
                        )

    stream_result = await stream_message(
        session_id, content, agent_id, user_id, token,
        attachments, agent=agent, collab_ctx=collab_ctx,
        on_tool_event=_on_tool_event,
        quote_references=quote_references,
    )

    # Non-streaming fallback
    if stream_result is None:
        message_id = str(uuid.uuid4())
        await manager.stream_broadcast(
            session_id, message_id,
            f"<thinking>正在分析中...</thinking>\n\n",
            is_final=False,
        )
        if agent:
            from app.services.agent_service import call_agent as _call
            response = await _call(session_id, content, user_id, attachments, agent=agent, collab_ctx=collab_ctx, token=token, on_tool_event=_on_tool_event, quote_references=quote_references)
        else:
            response = await route_message(session_id, content, sender_override or "user", user_id, attachments, on_tool_event=_on_tool_event)

        if token.cancelled:
            return
        text = str(response.get("content", ""))
        for piece in _chunk_text_for_streaming(text):
            if token.cancelled:
                return
            await manager.stream_broadcast(session_id, message_id, piece, is_final=False)
            await asyncio.sleep(0.004)
        if not token.cancelled:
            await manager.stream_broadcast(session_id, message_id, "", is_final=True)
            await _broadcast_final_message(session_id, message_id, response)
        return text

    # Streaming path — real SSE chunks from the adapter
    message_id = str(uuid.uuid4())
    full_response: list[str] = []
    batch: list[str] = []
    last_flush = 0.0

    try:
        async for chunk in stream_result:
            if token.cancelled:
                return ""
            if chunk:
                full_response.append(chunk)
                batch.append(chunk)
            # Flush frequently so the frontend sees tokens as they arrive.
            # Real SSE chunks come at natural pacing (every few hundred ms),
            # so a short batch window adds minimal overhead while keeping
            # latency low.
            now_ts = asyncio.get_event_loop().time()
            joined = "".join(batch)
            if batch and (now_ts - last_flush > 0.02 or len(joined) >= 20):
                await manager.stream_broadcast(
                    session_id, message_id, joined, is_final=False,
                    sender=agent_id,
                )
                batch.clear()
                last_flush = now_ts
                await asyncio.sleep(0)  # yield to event loop

        # Final flush
        if batch:
            await manager.stream_broadcast(
                session_id, message_id, "".join(batch), is_final=False,
                sender=agent_id,
            )

        if not token.cancelled:
            await manager.stream_broadcast(session_id, message_id, "", is_final=True, sender=agent_id)
            await _broadcast_final_db_message(session_id, message_id)

        return "".join(full_response)

    except Exception as _stream_exc:
        logger.exception("ws _invoke_agent stream failed session=%s agent=%s", session_id, agent_id)
        # Flush any remaining chunks before reporting the error
        if batch and not token.cancelled:
            await manager.stream_broadcast(
                session_id, message_id, "".join(batch), is_final=False,
                sender=agent_id,
            )
        # Send the actual error so the user can see what went wrong
        if not token.cancelled:
            error_text = f"\n\n⚠️ 流式响应处理异常：{_stream_exc}"
            await manager.stream_broadcast(
                session_id, message_id, error_text, is_final=False,
                sender=agent_id,
            )
            await manager.stream_broadcast(session_id, message_id, "", is_final=True, sender=agent_id)
        return "".join(full_response)


async def _broadcast_final_message(session_id: str, message_id: str, response: dict) -> None:
    rows = await afetch_all(
        "SELECT id,session_id AS \"sessionId\",sender,content,type,fidelity_score AS \"fidelityScore\",symbolic_json,created_at AS timestamp FROM messages WHERE session_id=$1 ORDER BY created_at DESC, id DESC LIMIT 1",
        session_id,
    )
    if rows:
        final = rows[0]
        final["event"] = "message"
        final["messageId"] = message_id
        try:
            final["symbolic"] = json.loads(final.pop("symbolic_json", "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            final["symbolic"] = {}
        await manager.broadcast(session_id, final)
    else:
        response["messageId"] = message_id
        await manager.broadcast(session_id, response)


async def _broadcast_final_db_message(session_id: str, message_id: str) -> None:
    rows = await afetch_all(
        "SELECT id,session_id AS \"sessionId\",sender,content,type,fidelity_score AS \"fidelityScore\",symbolic_json,created_at AS timestamp FROM messages WHERE session_id=$1 ORDER BY created_at DESC, id DESC LIMIT 1",
        session_id,
    )
    if rows:
        final = rows[0]
        final["event"] = "message"
        final["messageId"] = message_id
        try:
            final["symbolic"] = json.loads(final.pop("symbolic_json", "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            final["symbolic"] = {}
        await manager.broadcast(session_id, final)
