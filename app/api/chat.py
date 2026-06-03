from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db.init_db import now
from app.db.session import afetch_all, afetch_one, aexecute
from app.schemas.common import ChatTaskRequest
from app.services.auth_service import get_current_user
from app.services.agent_service import list_messages
from app.services.task_state_machine import task_state_machine

logger = logging.getLogger("agenthub.chat")

router = APIRouter(prefix="/api/chat", tags=["chat"])


class SessionCreateRequest(BaseModel):
    name: str = "新建会话"


@router.get("/sessions")
async def sessions() -> list[dict]:
    return await afetch_all(
        "SELECT id,name,type,active,created_at AS \"createdAt\",is_pinned AS \"isPinned\",last_message_at AS \"lastMessageAt\" "
        "FROM sessions ORDER BY is_pinned DESC, "
        "CASE WHEN last_message_at != '' THEN last_message_at ELSE created_at END DESC"
    )


@router.post("/sessions")
async def create_session(data: SessionCreateRequest, user: dict = Depends(get_current_user)) -> dict:
    session_id = f"session-{uuid.uuid4().hex[:8]}"
    await aexecute(
        "INSERT INTO sessions(id,name,type,participants,active,created_at) VALUES($1,$2,$3,$4,$5,$6)",
        session_id, data.name.strip() or "新建会话", "group", "[]", 1, now(),
    )
    return {"id": session_id, "name": data.name.strip() or "新建会话", "createdAt": now(), "active": 1, "type": "group"}


@router.get("/sessions/{session_id}/messages")
async def messages(session_id: str) -> list[dict]:
    return await list_messages(session_id)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user: dict = Depends(get_current_user)) -> dict:
    from pathlib import Path
    from app.utils.async_file import aexists, aisfile, aunlink, aglob_simple, aread_json, awrite_json

    # ── 1. Get session name before deletion (needed for memory cleanup) ──
    session_name: str | None = None
    row = await afetch_all("SELECT name FROM sessions WHERE id=$1 LIMIT 1", session_id)
    if row and row[0].get("name"):
        session_name = row[0]["name"]

    # ── 2. Delete from PostgreSQL ─────────────────────────────────────
    await aexecute("DELETE FROM messages WHERE session_id=$1", session_id)
    await aexecute("DELETE FROM tasks WHERE session_id=$1", session_id)
    deleted = await afetch_one("DELETE FROM sessions WHERE id=$1 RETURNING id", session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")

    # ── 3. Clean up memory artifacts ───────────────────────────────────
    from app.config import MEMORY_DIR
    from app.services.memory.models import sanitize_filename

    memory_base = Path(MEMORY_DIR)
    cleaned: list[str] = []

    # 3a. Delete session summary file
    sessions_dir = memory_base / "sessions"
    raw_fname = sanitize_filename(session_id)
    stem_fname = raw_fname[:-3] if raw_fname.endswith(".md") else raw_fname
    for fname in (raw_fname, stem_fname):
        if not fname:
            continue
        summary_path = sessions_dir / fname
        try:
            if await aexists(summary_path):
                await aunlink(summary_path)
                cleaned.append(f"session_summary/{summary_path.name}")
        except OSError:
            pass

    # 3b. Delete memory files named after the session
    if session_name:
        sanitized_name = sanitize_filename(session_name)
        for candidate in await aglob_simple(memory_base, f"{sanitized_name}*"):
            try:
                if await aisfile(candidate) and candidate.name.endswith(".md") and candidate.name != "MEMORY.md":
                    await aunlink(candidate)
                    cleaned.append(f"memory/{candidate.name}")
            except OSError:
                pass

    # 3c. Also check for memory files named after the session ID itself
    sanitized_id = sanitize_filename(session_id)
    for candidate in await aglob_simple(memory_base, f"{sanitized_id}*"):
        try:
            if await aisfile(candidate) and candidate.name.endswith(".md") and candidate.name != "MEMORY.md":
                await aunlink(candidate)
                cleaned.append(f"memory/{candidate.name}")
        except OSError:
            pass

    # 3c-extra. 兜底清理：扫描所有 .md 文件，检查其 YAML header 的 session_id
    # 字段是否等于被删的 session_id（即使文件名与 name/id 都不匹配也能删除）。
    from app.utils.async_file import aiterdir, aread_text
    try:
        for md_file in await aiterdir(memory_base):
            if not md_file.name.endswith(".md") or md_file.name == "MEMORY.md":
                continue
            try:
                content = await aread_text(md_file)
            except OSError:
                continue
            if f"session_id: {session_id}" in content or f"session_id:{session_id}" in content:
                try:
                    await aunlink(md_file)
                    cleaned.append(f"memory_by_header/{md_file.name}")
                except OSError:
                    pass
    except OSError:
        pass

    # 3d. Clean extraction state cursor
    extraction_state_path = memory_base / ".extraction_state.json"
    try:
        if await aexists(extraction_state_path):
            state = await aread_json(extraction_state_path)
            if state.get("sessions", {}).pop(session_id, None):
                await awrite_json(extraction_state_path, state)
                cleaned.append("extraction_state_cursor")
    except (OSError, ValueError):
        pass

    # 3e. Clean session memory state
    session_state_path = sessions_dir / ".session_state.json"
    try:
        if await aexists(session_state_path):
            state = await aread_json(session_state_path)
            if state.get("sessions", {}).pop(session_id, None):
                await awrite_json(session_state_path, state)
                cleaned.append("session_memory_state")
    except (OSError, ValueError):
        pass

    # 3f. Rebuild MEMORY.md index to reflect deletions
    if cleaned:
        try:
            from app.services.memory.storage import MemoryStorage
            storage = MemoryStorage(memory_base)
            await storage.rebuild_index()
        except Exception:
            pass

    logger.info(
        "session deleted id=%s name=%s cleaned=[%s]",
        session_id, session_name, ", ".join(cleaned),
    )

    return {"status": "success", "sessionId": session_id, "cleaned": cleaned}


@router.put("/sessions/{session_id}")
async def rename_session(session_id: str, data: dict, user: dict = Depends(get_current_user)) -> dict:
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    updated = await afetch_one(
        "UPDATE sessions SET name=$1 WHERE id=$2 RETURNING id", name, session_id,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "success", "sessionId": session_id, "name": name}


@router.put("/sessions/{session_id}/pin")
async def toggle_pin_session(session_id: str, user: dict = Depends(get_current_user)) -> dict:
    row = await afetch_one("SELECT is_pinned FROM sessions WHERE id=$1", session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    new_val = 0 if row.get("is_pinned") else 1
    await aexecute("UPDATE sessions SET is_pinned=$1 WHERE id=$2", new_val, session_id)
    return {"status": "success", "sessionId": session_id, "isPinned": new_val}


# ── Auto-name helpers ──────────────────────────────────────────

GENERIC_SESSION_NAMES = {"新建会话", "默认会话", "new session", "untitled"}


def is_generic_name(name: str) -> bool:
    """Check whether a session name is a default/placeholder that should be auto-named."""
    stripped = (name or "").strip().lower()
    if not stripped:
        return True
    for pattern in GENERIC_SESSION_NAMES:
        if stripped.startswith(pattern):
            return True
    return False


def _build_auto_name_prompt(messages: list[dict]) -> str:
    """Build a prompt for the LLM to generate a session title from the first interaction.

    The title is determined primarily by the user's first message — this ensures
    the conversation name reflects what the user originally asked about, not
    whatever tangent the conversation may have drifted into.
    """
    # Separate user messages from agent responses
    user_msgs = [m for m in messages if m.get("sender") not in ("system", "agent", "orchestrator")]
    agent_msgs = [m for m in messages if m.get("sender") in ("agent", "orchestrator", "system")]

    # The first user message is the primary signal
    first_user = (user_msgs[0].get("content") or "").strip() if user_msgs else ""

    if not first_user:
        return ""

    # Truncate if needed
    if len(first_user) > 300:
        first_user = first_user[:300] + "..."

    # Optionally include the first agent response for context
    first_reply = ""
    if agent_msgs:
        reply_content = (agent_msgs[0].get("content") or "").strip()
        if reply_content:
            if len(reply_content) > 200:
                reply_content = reply_content[:200] + "..."
            first_reply = f"\n助手回复摘要：{reply_content}"

    return (
        "你是一个对话标题生成器。请根据用户的第一条消息（对话的初始交互）生成一个简洁的标题。\n\n"
        "要求：\n"
        "1. 标题必须为中文，3-15字\n"
        "2. 准确概括用户的核心意图或问题主题\n"
        "3. 有区分度，方便日后查找\n"
        "4. 不要生成\"新建会话\"、\"未命名\"、\"对话\"、\"聊天\"等无意义标题\n"
        "5. 只输出标题文本，不要加引号、编号或任何额外说明\n\n"
        f"用户第一条消息：{first_user}{first_reply}\n\n标题："
    )


def _extract_local_title(first_message: str) -> str:
    """Local fallback: extract a meaningful Chinese title from the first user message.

    Uses keyword pattern matching to generate a concise title (3-15 chars)
    without calling any LLM. Handles common patterns like:
    - \"@Agent do something\" → \"do something\"
    - \"帮我实现XXX\" → \"实现XXX\"
    - \"Generate a FastAPI...\" → translates common English intents
    """
    import re
    text = first_message.strip()

    # Strip @mentions and leading/trailing noise
    text = re.sub(r'@\w+\s*', '', text).strip()
    if not text:
        return ""

    # Common action patterns → Chinese title keywords
    patterns = [
        (r'(?:生成|创建|写|编写|实现|开发|搭建)\s*(?:一个?\s*)?(.{2,30}?)(?:文件|代码|页面|模块|功能|路由|接口|API|组件)?$', ''),
        (r'(?:帮我|请|麻烦|帮忙)\s*(.{2,30}?)(?:谢谢|感谢)?$', ''),
        (r'(?:如何|怎么|怎样)\s*(.{2,30}?)(?:\?|？)?$', ''),
        (r'(?:修复|修改|优化|调整|更新)\s*(.{2,30}?)$', ''),
        (r'(?:分析|审查|检查|review|analyze)\s*(.{2,30}?)$', ''),
    ]

    for pattern, _ in patterns:
        m = re.search(pattern, text)
        if m:
            keyword = m.group(1).strip().rstrip('。！？.?！，,')
            if 2 <= len(keyword) <= 20:
                return keyword

    # English intent mapping (common dev commands)
    eng_patterns = [
        (r'[Gg]enerate\s+(?:a\s+)?(.{2,40}?)(?:\s+(?:file|route|code|page|module))?$', '生成'),
        (r'[Cc]reate\s+(?:a\s+)?(.{2,40}?)$', '创建'),
        (r'[Ff]ix\s+(?:the\s+)?(.{2,40}?)$', '修复'),
        (r'[Ii]mplement\s+(?:a\s+)?(.{2,40}?)$', '实现'),
        (r'[Cc]ode\s+(?:review|check)\s+(?:of\s+)?(.{2,40}?)$', '审查'),
    ]

    for pattern, prefix in eng_patterns:
        m = re.search(pattern, text)
        if m:
            keyword = m.group(1).strip().rstrip('.!?')
            # Translate common English dev terms
            translations = {
                'health route': '健康检查路由', 'health check': '健康检查',
                'api': 'API接口', 'rest api': 'REST接口',
                'login': '登录功能', 'auth': '认证功能',
                'database': '数据库', 'config': '配置管理',
                'test': '测试用例', 'component': '组件开发',
                'middleware': '中间件', 'docker': 'Docker部署',
                'frontend': '前端页面', 'backend': '后端服务',
                'pipeline': 'CI/CD流水线', 'deploy': '部署流程',
            }
            keyword_lower = keyword.lower()
            for eng, chn in translations.items():
                if eng in keyword_lower:
                    return f'{prefix}{chn}'
            # Generic prefix + English keyword (limited to 15 chars)
            title = f'{prefix}{keyword[:10]}'
            return title[:15]

    # Last resort: take the first meaningful segment
    # Split on common delimiters and take the first meaningful chunk
    parts = re.split(r'[,，。！？\n!?]', text)
    for part in parts:
        part = part.strip()
        # Remove pure punctuation / short fragments
        clean = re.sub(r'[^\w一-鿿]', '', part)
        if len(clean) >= 3:
            if len(part) <= 15:
                return part
            return part[:15]

    # Absolute fallback
    return text[:15] if len(text) >= 3 else ""


async def _call_llm_for_name(prompt: str) -> str | None:
    """Call the best available LLM to generate a session name. Returns name or None."""
    if not prompt:
        return None

    from app.services.adapter_manager import adapter_manager
    from app.services.secret_service import decrypt_secret

    candidates: list[dict] = []

    # 1) Try model_configs table
    try:
        rows = await afetch_all(
            "SELECT provider, model_name, api_key, base_url "
            "FROM model_configs WHERE is_active=1 ORDER BY id DESC LIMIT 5"
        )
        for row in rows:
            key = decrypt_secret(row.get("api_key") or "")
            if key and (row.get("provider") or "").lower() != "mock":
                candidates.append({**row, "api_key": key})
    except Exception:
        pass

    # 2) Try agent_registry
    try:
        agent_rows = await afetch_all(
            "SELECT DISTINCT adapter_type AS provider, base_model_name AS model_name, "
            "api_key, base_url "
            "FROM agent_registry WHERE api_key IS NOT NULL AND api_key != '' "
            "AND adapter_type != '' AND adapter_type IS NOT NULL"
        )
        for row in agent_rows:
            key = decrypt_secret(row.get("api_key") or "")
            if key and (row.get("provider") or "").lower() != "mock":
                candidates.append({**row, "api_key": key})
    except Exception:
        pass

    # 3) Fallback to env vars
    from app.config import OPENAI_API_KEY, ANTHROPIC_API_KEY
    if OPENAI_API_KEY:
        candidates.append({"provider": "openai", "model_name": "gpt-4o-mini", "api_key": OPENAI_API_KEY, "base_url": ""})
    if ANTHROPIC_API_KEY:
        candidates.append({"provider": "anthropic", "model_name": "claude-sonnet-4-6", "api_key": ANTHROPIC_API_KEY, "base_url": ""})

    for c in candidates:
        try:
            adapter = adapter_manager.get_adapter(c["provider"])
            result = await adapter.execute_prompt(
                prompt,
                model=(c.get("model_name") or ""),
                api_key=(c.get("api_key") or ""),
                base_url=(c.get("base_url") or ""),
            )
            if result and result.strip():
                name = result.strip()
                for prefix in ("标题：", "标题:", "Title：", "Title:"):
                    if name.startswith(prefix):
                        name = name[len(prefix):].strip()
                if len(name) >= 2 and name[0] == name[-1] and name[0] in ('"', "'", "「", "『"):
                    name = name[1:-1].strip()
                if 2 <= len(name) <= 50:
                    return name
        except Exception as exc:
            logger.debug("auto-name LLM candidate %s/%s failed: %s", c.get("provider"), c.get("model_name"), exc)
            continue

    return None


@router.post("/sessions/{session_id}/auto-name")
async def auto_name_session(session_id: str, user: dict = Depends(get_current_user)) -> dict:
    """Generate a session name automatically from conversation content."""
    session = await afetch_one("SELECT id, name FROM sessions WHERE id=$1", session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    msgs = await list_messages(session_id)
    if not msgs or len(msgs) < 2:
        return {"status": "skipped", "reason": "Not enough messages", "sessionId": session_id}

    prompt = _build_auto_name_prompt(msgs)
    if not prompt:
        return {"status": "skipped", "reason": "No message content", "sessionId": session_id}

    name = await _call_llm_for_name(prompt)
    if not name:
        # LLM failed — use local keyword extraction from first user message
        user_msgs = [m for m in msgs if m.get("sender") not in ("system", "agent", "orchestrator")]
        first_msg = (user_msgs[0].get("content") or "").strip() if user_msgs else ""
        if first_msg:
            name = _extract_local_title(first_msg)
    if not name:
        return {"status": "skipped", "reason": "LLM call failed", "sessionId": session_id}

    await aexecute("UPDATE sessions SET name=$1 WHERE id=$2", name, session_id)
    return {"status": "success", "sessionId": session_id, "name": name}


async def try_auto_name_session(session_id: str) -> str | None:
    """Non-blocking helper: generate and apply an auto-name if the session name is generic.
    Returns the new name if one was set, None otherwise.
    """
    try:
        session = await afetch_one("SELECT id, name FROM sessions WHERE id=$1", session_id)
        if not session:
            return None

        current_name = session.get("name") or ""
        if not is_generic_name(current_name):
            return None

        msgs = await list_messages(session_id)
        if not msgs or len(msgs) < 1:
            return None

        prompt = _build_auto_name_prompt(msgs)
        if not prompt:
            # No prompt could be built — try local extraction directly
            user_msgs = [m for m in msgs if m.get("sender") not in ("system", "agent", "orchestrator")]
            first_msg = (user_msgs[0].get("content") or "").strip() if user_msgs else ""
            if first_msg:
                name = _extract_local_title(first_msg)
                if name:
                    await aexecute("UPDATE sessions SET name=$1 WHERE id=$2", name, session_id)
                    return name
            return None

        name = await _call_llm_for_name(prompt)
        if not name:
            # LLM failed — use local keyword extraction from first user message
            user_msgs = [m for m in msgs if m.get("sender") not in ("system", "agent", "orchestrator")]
            first_msg = (user_msgs[0].get("content") or "").strip() if user_msgs else ""
            if first_msg:
                name = _extract_local_title(first_msg)
        if not name:
            return None

        await aexecute("UPDATE sessions SET name=$1 WHERE id=$2", name, session_id)
        return name
    except Exception:
        logger.debug("auto-name background task failed for %s", session_id, exc_info=True)
        return None


@router.post("/tasks")
async def create_task(data: ChatTaskRequest, user: dict = Depends(get_current_user)) -> dict:
    if not data.message.strip():
        raise HTTPException(status_code=400, detail="message is required")
    return await task_state_machine.create_task(data.sessionId, data.message)


@router.get("/workflows")
async def list_workflows() -> list[dict]:
    rows = await afetch_all(
        "SELECT id,name,description,trigger_keywords FROM agent_routes WHERE active=1 ORDER BY is_default DESC, updated_at DESC"
    )
    for r in rows:
        import json
        r["triggerKeywords"] = json.loads(r.pop("trigger_keywords", "[]") or "[]")
        r["routeId"] = r.pop("id")
    return rows
