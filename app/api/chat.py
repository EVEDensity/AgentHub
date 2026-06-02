from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db.init_db import now
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
    from app.db.session import dict_rows

    return dict_rows("SELECT id,name,type,active,created_at AS createdAt,is_pinned AS isPinned,last_message_at AS lastMessageAt FROM sessions ORDER BY is_pinned DESC, CASE WHEN last_message_at != '' THEN last_message_at ELSE created_at END DESC")


@router.post("/sessions")
async def create_session(data: SessionCreateRequest, user: dict = Depends(get_current_user)) -> dict:
    from app.db.session import get_connection

    session_id = f"session-{uuid.uuid4().hex[:8]}"
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO sessions(id,name,type,participants,active,created_at) VALUES(?,?,?,?,?,?)",
            (session_id, data.name.strip() or "新建会话", "group", "[]", 1, now()),
        )
    return {"id": session_id, "name": data.name.strip() or "新建会话", "createdAt": now(), "active": 1, "type": "group"}


@router.get("/sessions/{session_id}/messages")
async def messages(session_id: str) -> list[dict]:
    return list_messages(session_id)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user: dict = Depends(get_current_user)) -> dict:
    from pathlib import Path
    from app.db.session import get_connection, dict_rows

    # ── 1. Get session name before deletion (needed for memory cleanup) ──
    session_name: str | None = None
    row = dict_rows("SELECT name FROM sessions WHERE id=? LIMIT 1", (session_id,))
    if row and row[0].get("name"):
        session_name = row[0]["name"]

    # ── 2. Delete from SQLite ──────────────────────────────────────────
    with get_connection() as conn:
        conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM tasks WHERE session_id=?", (session_id,))
        cursor = conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Session not found")

    # ── 3. Clean up memory artifacts ───────────────────────────────────
    from app.config import MEMORY_DIR
    from app.services.memory.models import sanitize_filename

    memory_base = Path(MEMORY_DIR)
    cleaned: list[str] = []

    # 3a. Delete session summary file (.claude/memory/sessions/{sanitize(session_id)})
    sessions_dir = memory_base / "sessions"
    raw_fname = sanitize_filename(session_id)  # always has .md suffix
    # Also try without .md for older files that may have been saved differently
    stem_fname = raw_fname[:-3] if raw_fname.endswith(".md") else raw_fname
    for fname in (raw_fname, stem_fname):
        if not fname:
            continue
        summary_path = sessions_dir / fname
        try:
            if summary_path.exists():
                summary_path.unlink()
                cleaned.append(f"session_summary/{summary_path.name}")
        except OSError:
            pass

    # 3b. Delete memory files named after the session (by sanitized session name)
    if session_name:
        sanitized_name = sanitize_filename(session_name)
        for candidate in memory_base.glob(f"{sanitized_name}*"):
            try:
                if candidate.is_file() and candidate.name.endswith(".md") and candidate.name != "MEMORY.md":
                    candidate.unlink()
                    cleaned.append(f"memory/{candidate.name}")
            except OSError:
                pass

    # 3c. Also check for memory files named after the session ID itself
    sanitized_id = sanitize_filename(session_id)
    for candidate in memory_base.glob(f"{sanitized_id}*"):
        try:
            if candidate.is_file() and candidate.name.endswith(".md") and candidate.name != "MEMORY.md":
                candidate.unlink()
                cleaned.append(f"memory/{candidate.name}")
        except OSError:
            pass

    # 3d. Clean extraction state cursor
    extraction_state_path = memory_base / ".extraction_state.json"
    try:
        if extraction_state_path.exists():
            import json
            state = json.loads(extraction_state_path.read_text(encoding="utf-8"))
            if state.get("sessions", {}).pop(session_id, None):
                extraction_state_path.write_text(
                    json.dumps(state, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                cleaned.append("extraction_state_cursor")
    except (OSError, json.JSONDecodeError):
        pass

    # 3e. Clean session memory state
    session_state_path = sessions_dir / ".session_state.json"
    try:
        if session_state_path.exists():
            import json
            state = json.loads(session_state_path.read_text(encoding="utf-8"))
            if state.get("sessions", {}).pop(session_id, None):
                session_state_path.write_text(
                    json.dumps(state, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                cleaned.append("session_memory_state")
    except (OSError, json.JSONDecodeError):
        pass

    # 3f. Rebuild MEMORY.md index to reflect deletions
    if cleaned:
        try:
            from app.services.memory.storage import MemoryStorage
            storage = MemoryStorage(memory_base)
            storage.rebuild_index()
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
    from app.db.session import get_connection

    with get_connection() as conn:
        cursor = conn.execute("UPDATE sessions SET name=? WHERE id=?", (name, session_id))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "success", "sessionId": session_id, "name": name}


@router.put("/sessions/{session_id}/pin")
async def toggle_pin_session(session_id: str, user: dict = Depends(get_current_user)) -> dict:
    from app.db.session import get_connection, one_row

    row = one_row("SELECT is_pinned FROM sessions WHERE id=?", (session_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    new_val = 0 if row.get("is_pinned") else 1
    with get_connection() as conn:
        conn.execute("UPDATE sessions SET is_pinned=? WHERE id=?", (new_val, session_id))
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
    """Build a prompt for the LLM to generate a session title from conversation."""
    lines: list[str] = []
    for m in messages[-12:]:  # last 12 messages max
        role = "User" if m.get("sender") not in ("system", "agent", "orchestrator") else "Assistant"
        content = (m.get("content") or "").strip()
        if not content:
            continue
        # Truncate long messages
        if len(content) > 500:
            content = content[:500] + "..."
        lines.append(f"[{role}]: {content}")

    if not lines:
        return ""

    conversation = "\n".join(lines)
    return (
        "你是一个对话标题生成器。请根据以下对话内容，生成一个简洁、有区分度的标题（中文，8-20字），"
        "准确概括对话的核心主题或用户的主要意图。\n"
        "不要生成\"新建会话\"、\"未命名\"、\"对话\"等无意义标题。\n"
        "只输出标题文本，不要加引号、编号或任何额外说明。\n\n"
        f"对话内容：\n{conversation}\n\n标题："
    )


async def _call_llm_for_name(prompt: str) -> str | None:
    """Call the best available LLM to generate a session name. Returns name or None."""
    if not prompt:
        return None

    from app.services.adapter_manager import adapter_manager
    from app.services.secret_service import decrypt_secret
    from app.db.session import dict_rows

    candidates: list[dict] = []

    # 1) Try model_configs table
    try:
        rows = dict_rows(
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
        agent_rows = dict_rows(
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
                # Clean up common artifacts
                for prefix in ("标题：", "标题:", "Title：", "Title:"):
                    if name.startswith(prefix):
                        name = name[len(prefix):].strip()
                # Remove quotes if the model wrapped the output
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
    from app.db.session import get_connection, one_row

    session = one_row("SELECT id, name FROM sessions WHERE id=?", (session_id,))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Fetch messages
    msgs = list_messages(session_id)
    if not msgs or len(msgs) < 2:
        return {"status": "skipped", "reason": "Not enough messages", "sessionId": session_id}

    prompt = _build_auto_name_prompt(msgs)
    if not prompt:
        return {"status": "skipped", "reason": "No message content", "sessionId": session_id}

    name = await _call_llm_for_name(prompt)
    if not name:
        return {"status": "skipped", "reason": "LLM call failed", "sessionId": session_id}

    # Update DB
    with get_connection() as conn:
        conn.execute("UPDATE sessions SET name=? WHERE id=?", (name, session_id))

    return {"status": "success", "sessionId": session_id, "name": name}


async def try_auto_name_session(session_id: str) -> str | None:
    """Non-blocking helper: generate and apply an auto-name if the session name is generic.
    Returns the new name if one was set, None otherwise.
    """
    from app.db.session import one_row

    try:
        session = one_row("SELECT id, name FROM sessions WHERE id=?", (session_id,))
        if not session:
            return None

        current_name = session.get("name") or ""
        if not is_generic_name(current_name):
            return None  # Already has a meaningful name

        msgs = list_messages(session_id)
        if not msgs or len(msgs) < 2:
            return None

        prompt = _build_auto_name_prompt(msgs)
        if not prompt:
            return None

        name = await _call_llm_for_name(prompt)
        if not name:
            return None

        from app.db.session import get_connection
        with get_connection() as conn:
            conn.execute("UPDATE sessions SET name=? WHERE id=?", (name, session_id))

        return name
    except Exception:
        logger.debug("auto-name background task failed for %s", session_id, exc_info=True)
        return None


@router.post("/tasks")
async def create_task(data: ChatTaskRequest, user: dict = Depends(get_current_user)) -> dict:
    if not data.message.strip():
        raise HTTPException(status_code=400, detail="message is required")
    return task_state_machine.create_task(data.sessionId, data.message)


@router.get("/workflows")
async def list_workflows() -> list[dict]:
    from app.db.session import dict_rows

    rows = dict_rows(
        "SELECT id,name,description,trigger_keywords FROM agent_routes WHERE active=1 ORDER BY is_default DESC, updated_at DESC"
    )
    for r in rows:
        import json

        r["triggerKeywords"] = json.loads(r.pop("trigger_keywords", "[]") or "[]")
        r["routeId"] = r.pop("id")
    return rows
