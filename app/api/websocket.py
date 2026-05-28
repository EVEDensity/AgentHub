from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.db.init_db import now
from app.services.agent_service import save_message
from app.services.auth_service import websocket_user
from app.services.message_router import route_message, stream_message
from app.services.websocket_manager import manager

router = APIRouter(tags=["websocket"])


def _chunk_text_for_streaming(text: str, chunk_size: int = 80) -> list[str]:
    """Split text for pseudo-stream fallback only.

    Use larger chunks to reduce per-chunk overhead and latency.
    """
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
    await manager.connect(session_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            content = str(data.get("content", "")).strip()
            if not content:
                continue

            # Cancel any in-flight stream — the per-session lock inside
            # _process_and_stream guarantees the old task releases it
            # before the new one proceeds.
            manager.cancel_token(session_id)
            await asyncio.sleep(0.02)

            asyncio.create_task(
                _process_and_stream(
                    session_id,
                    content,
                    data.get("sender", user["name"]),
                    user["id"],
                    data.get("attachments", []),
                )
            )

    except WebSocketDisconnect:
        manager.disconnect(session_id, websocket)
        manager.teardown_session(session_id)


async def _process_and_stream(
    session_id: str,
    content: str,
    sender: str,
    user_id: str,
    attachments: list[dict] | None = None,
) -> None:
    """Process one user message with the per-session lock held.

    Only one instance of this coroutine runs per session at a time.
    The caller is responsible for calling ``manager.cancel_token``
    *before* scheduling this task so that any in-flight work sees the
    cancellation signal and releases the lock promptly.
    """
    lock = manager.get_session_lock(session_id)
    async with lock:
        token = manager.create_token(session_id)

        try:
            # Persist the user message regardless of streaming path
            save_message(session_id, sender, content, "text")

            stream_result = await stream_message(session_id, content, sender, user_id, token, attachments or [])

            # Non-streaming fallback: emit immediate thinking chunk, then pseudo-stream final body
            if stream_result is None:
                message_id = str(uuid.uuid4())
                await manager.stream_broadcast(session_id, message_id, "<thinking>正在分析中...</thinking>\n\n", is_final=False)
                response = await route_message(session_id, content, sender, user_id, attachments or [])
                if token.cancelled:
                    return
                for piece in _chunk_text_for_streaming(str(response.get("content", ""))):
                    if token.cancelled:
                        return
                    await manager.stream_broadcast(session_id, message_id, piece, is_final=False)
                    await asyncio.sleep(0.004)
                if not token.cancelled:
                    await manager.stream_broadcast(session_id, message_id, "", is_final=True)
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
                return

            # Streaming path
            message_id = str(uuid.uuid4())

            async for chunk in stream_result:
                if token.cancelled:
                    return
                # Some adapters may return a full body in one chunk;
                # re-chunk to preserve strict stream UX.
                for piece in _chunk_text_for_streaming(chunk or ""):
                    if token.cancelled:
                        return
                    await manager.stream_broadcast(session_id, message_id, piece, is_final=False)
                    await asyncio.sleep(0.002)

            if not token.cancelled:
                await manager.stream_broadcast(session_id, message_id, "", is_final=True)
                # Broadcast the final persisted message so the frontend receives
                # full metadata (sender, fidelityScore, symbolic, generated)
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

        except Exception:
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
