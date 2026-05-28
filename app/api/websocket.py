from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.db.init_db import now
from app.db.session import one_row
from app.services.agent_service import extract_mentions, save_message
from app.services.auth_service import websocket_user
from app.services.message_router import route_message, stream_message
from app.services.symbolic import (
    FIDELITY_HIGH,
    FIDELITY_LOW,
    FIDELITY_WARN,
    build_enrichment_prompt,
    build_redistill_prompt,
    fidelity_action,
    requires_redistill,
)
from app.services.websocket_manager import manager

logger = logging.getLogger("agenthub.websocket")

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
    user = websocket_user(token)
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

            content = str(data.get("content", "")).strip()
            if not content:
                continue

            # Cancel any in-flight stream
            manager.cancel_token(session_id)
            await asyncio.sleep(0.02)

            task = asyncio.create_task(
                _process_and_stream(
                    session_id, content,
                    data.get("sender", user["name"]),
                    user_id,
                    data.get("attachments", []),
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


async def _handle_fidelity(
    session_id: str,
    content: str,
    agent: dict,
    user_id: str,
    token,
    attachments: list[dict],
    response_text: str,
    fid_result: dict,
    collab,  # CollaborationContext
) -> None:
    """Execute fidelity closed-loop actions per §3.3 thresholds.

    - ≥ 0.85: silent pass
    - 0.70–0.85: emit warning to frontend
    - 0.55–0.70: emit warning + pull enrichment context
    - < 0.55: emit block + attempt re-distillation
    """
    score = fid_result["fidelity_score"]
    action = fid_result["action"]

    if score >= FIDELITY_HIGH:
        return  # normal pass-through

    agent_id = agent["agent_id"]

    if score >= FIDELITY_WARN:
        # 0.70–0.85: continue but warn frontend
        await manager.broadcast(
            session_id,
            {
                "event": "fidelity_warning",
                "sessionId": session_id,
                "agentId": agent_id,
                "fidelityScore": score,
                "grade": "warn",
                "message": f"Agent {agent_id} 响应保真度偏低（{score:.2f}），建议核实关键信息。",
                "timestamp": now(),
            },
        )
        return

    if score >= FIDELITY_LOW:
        # 0.55–0.70: auto-pull extended context and supplement
        await manager.broadcast(
            session_id,
            {
                "event": "fidelity_warning",
                "sessionId": session_id,
                "agentId": agent_id,
                "fidelityScore": score,
                "grade": "low",
                "message": f"Agent {agent_id} 保真度不足（{score:.2f}），正在拉取扩展上下文补充...",
                "timestamp": now(),
            },
        )
        # Build enrichment prompt and re-invoke the agent
        enrichment = build_enrichment_prompt(content, response_text[:2000], score)
        logger.info("ws fidelity enrich session=%s agent=%s score=%.2f", session_id, agent_id, score)
        try:
            enriched_text = await _invoke_agent(
                session_id, enrichment, agent, user_id, token, attachments,
                collab_ctx=f"【保真度补充 — 上一轮响应保真度仅 {score:.2f}，请基于原始需求补充遗漏的关键信息】\n原始需求：{content[:1000]}",
            )
            if enriched_text and not token.cancelled:
                collab.record(agent_id + "_enriched", agent.get("domain", ""), enriched_text)
        except Exception:
            logger.exception("ws fidelity enrich failed session=%s agent=%s", session_id, agent_id)
        return

    # < 0.55: BLOCK — require re-distillation or human confirmation
    await manager.broadcast(
        session_id,
        {
            "event": "fidelity_block",
            "sessionId": session_id,
            "agentId": agent_id,
            "fidelityScore": score,
            "grade": "block",
            "message": f"Agent {agent_id} 响应保真度严重不足（{score:.2f}），已阻断传递，正在请求重新提炼...",
            "requiresHumanConfirm": True,
            "timestamp": now(),
        },
    )
    logger.warning("ws fidelity block session=%s agent=%s score=%.2f", session_id, agent_id, score)
    # Attempt re-distillation
    redistill_prompt = build_redistill_prompt(content, response_text[:2000], score)
    try:
        redistilled = await _invoke_agent(
            session_id, redistill_prompt, agent, user_id, token, attachments,
            collab_ctx="【重新提炼 — 上一轮响应质量不达标，请基于原始需求重新生成高质量回复】",
        )
        if redistilled and not token.cancelled:
            new_fid = collab.record(agent_id + "_redistilled", agent.get("domain", ""), redistilled)
            new_score = new_fid["fidelity_score"]
            if new_score >= FIDELITY_LOW:
                await manager.broadcast(
                    session_id,
                    {
                        "event": "fidelity_resolved",
                        "sessionId": session_id,
                        "agentId": agent_id,
                        "fidelityScore": new_score,
                        "message": f"重新提炼完成，保真度恢复至 {new_score:.2f}。",
                        "timestamp": now(),
                    },
                )
            else:
                await manager.broadcast(
                    session_id,
                    {
                        "event": "fidelity_block",
                        "sessionId": session_id,
                        "agentId": agent_id,
                        "fidelityScore": new_score,
                        "grade": "block",
                        "message": f"重新提炼后保真度仍不足（{new_score:.2f}），需要人工确认后继续。",
                        "requiresHumanConfirm": True,
                        "timestamp": now(),
                    },
                )
    except Exception:
        logger.exception("ws fidelity redistill failed session=%s agent=%s", session_id, agent_id)


async def _process_and_stream(
    session_id: str,
    content: str,
    sender: str,
    user_id: str,
    attachments: list[dict] | None = None,
) -> None:
    """Process one user message with the per-session lock held."""
    lock = manager.get_session_lock(session_id)
    async with lock:
        token = manager.create_token(session_id)

        try:
            save_message(session_id, sender, content, "text")

            mentioned = extract_mentions(content)
            target_agents: list[dict] = []
            seen: set[str] = set()
            for name in mentioned:
                if name in seen:
                    continue
                seen.add(name)
                row = one_row(
                    "SELECT agent_id,domain,status,adapter_type,risk_level FROM agent_registry WHERE agent_id=?",
                    (name,),
                )
                if row:
                    target_agents.append(row)

            # ── Multi-agent path ──────────────────────────────────────
            if len(target_agents) >= 2:
                from app.services.agent_service import CollaborationContext

                collab = CollaborationContext(content)
                for a in target_agents:
                    collab.register(a)

                for agent in target_agents:
                    if token.cancelled:
                        return
                    ctx = collab.context_for(agent["agent_id"])
                    try:
                        response_text = await _invoke_agent(
                            session_id, content, agent, user_id, token, attachments or [],
                            collab_ctx=ctx,
                        )
                        if not token.cancelled and response_text:
                            fid_result = collab.record(agent["agent_id"], agent.get("domain", ""), response_text)
                            # ── Fidelity closed-loop (§3.3) ──────────
                            await _handle_fidelity(
                                session_id, content, agent, user_id, token,
                                attachments or [], response_text, fid_result, collab,
                            )
                    except Exception:
                        if not token.cancelled:
                            await manager.broadcast(
                                session_id,
                                {
                                    "event": "message",
                                    "sessionId": session_id,
                                    "content": f"Agent 【{agent['agent_id']}】调用失败，请稍后重试。",
                                    "sender": "system",
                                    "timestamp": now(),
                                    "type": "system",
                                },
                            )

                # Emit collaboration summary (with overall fidelity)
                final_summary = collab.summary
                overall_fid = collab.overall_fidelity
                if final_summary and not token.cancelled:
                    await manager.broadcast(
                        session_id,
                        {
                            "event": "message",
                            "sessionId": session_id,
                            "content": final_summary,
                            "sender": "system",
                            "timestamp": now(),
                            "type": "system",
                            "fidelityScore": overall_fid,
                        },
                    )
                return

            # ── Single-agent path ─────────────────────────────────────
            agent = target_agents[0] if target_agents else None
            await _invoke_agent(
                session_id, content, agent, user_id, token, attachments or [],
                sender_override=sender,
            )

        except Exception:
            logger.exception("ws _process_and_stream failed session=%s", session_id)
            if not token.cancelled:
                await manager.broadcast(
                    session_id,
                    {
                        "event": "message",
                        "sessionId": session_id,
                        "content": "模型调用失败",
                        "sender": "system",
                        "timestamp": now(),
                        "type": "system",
                    },
                )
        finally:
            manager.remove_token(session_id, token)


async def _invoke_agent(
    session_id: str,
    content: str,
    agent: dict | None,
    user_id: str,
    token,
    attachments: list[dict],
    collab_ctx: str = "",
    sender_override: str | None = None,
) -> str:
    """Invoke a single agent — streaming first, non-streaming fallback.

    Returns the agent's full response text (for collaboration context recording).
    """
    agent_id = agent["agent_id"] if agent else (sender_override or "Orchestrator")

    stream_result = await stream_message(
        session_id, content, agent_id, user_id, token,
        attachments, agent=agent, collab_ctx=collab_ctx,
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
            response = await _call(session_id, content, user_id, attachments, agent=agent, collab_ctx=collab_ctx)
        else:
            response = await route_message(session_id, content, sender_override or "user", user_id, attachments)

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
            await _broadcast_final_message(session_id, response)
        return text

    # Streaming path
    message_id = str(uuid.uuid4())
    full_response: list[str] = []
    batch: list[str] = []
    last_flush = 0.0

    async for chunk in stream_result:
        if token.cancelled:
            return
        if chunk:
            full_response.append(chunk)
            batch.append(chunk)
        # Flush every ~50ms to balance latency vs overhead
        now_ts = asyncio.get_event_loop().time()
        if batch and now_ts - last_flush > 0.05:
            await manager.stream_broadcast(
                session_id, message_id, "".join(batch), is_final=False,
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
        await _broadcast_final_db_message(session_id)

    return "".join(full_response)


async def _broadcast_final_message(session_id: str, response: dict) -> None:
    from app.db.session import dict_rows as _dr

    rows = _dr(
        "SELECT id,session_id AS sessionId,sender,content,type,fidelity_score AS fidelityScore,symbolic_json,created_at AS timestamp FROM messages WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
        (session_id,),
    )
    if rows:
        final = rows[0]
        final["event"] = "message"
        try:
            final["symbolic"] = json.loads(final.pop("symbolic_json", "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            final["symbolic"] = {}
        await manager.broadcast(session_id, final)
    else:
        await manager.broadcast(session_id, response)


async def _broadcast_final_db_message(session_id: str) -> None:
    from app.db.session import dict_rows

    rows = dict_rows(
        "SELECT id,session_id AS sessionId,sender,content,type,fidelity_score AS fidelityScore,symbolic_json,created_at AS timestamp FROM messages WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
        (session_id,),
    )
    if rows:
        final = rows[0]
        final["event"] = "message"
        try:
            final["symbolic"] = json.loads(final.pop("symbolic_json", "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            final["symbolic"] = {}
        await manager.broadcast(session_id, final)
