from __future__ import annotations

"""Session-scoped tools: artifacts, conversation search.

These tools require session context (via contextvars) to access the
current session's messages and artifacts.
"""

import contextvars
import logging
from typing import Any

logger = logging.getLogger("agenthub.tools.session")

# ── Context variables (mirrors agent_tools.py) ────────────────────────────

_ctx_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "stool_session_id", default=""
)

def set_session_tool_context(session_id: str) -> None:
    """Set the session ID for session-scoped tools."""
    if session_id:
        _ctx_session_id.set(session_id)

def get_session_tool_context() -> str:
    """Get the current session ID."""
    return _ctx_session_id.get()


# ── artifact_list ─────────────────────────────────────────────────────────

async def artifact_list_handler(
    max_results: int = 20,
) -> dict[str, Any]:
    """List all artifacts (generated files, code outputs, plans) for the
    current session.

    Artifacts are versioned — only the latest version of each file is
    returned.  Use ``artifact_read`` to fetch a specific artifact's content.

    Args:
        max_results: Maximum number of artifacts to return (default 20, max 50).
    """
    session_id = get_session_tool_context()
    if not session_id:
        return {"success": False, "error": "无法获取会话信息（无上下文）"}

    effective_max = min(max(max_results, 1), 50)

    try:
        from app.db.session import afetch_all

        rows = await afetch_all(
            "SELECT DISTINCT ON (file_path) id, session_id, file_path, "
            "version, created_at, "
            "length(content) AS content_length "
            "FROM artifacts WHERE session_id=$1 "
            "ORDER BY file_path, version DESC "
            "LIMIT $2",
            session_id,
            effective_max,
        )

        if not rows:
            return {
                "success": True,
                "result": {
                    "artifacts": [],
                    "total": 0,
                    "message": "当前会话暂无产物",
                },
            }

        artifacts = []
        for r in rows:
            artifacts.append({
                "id": r["id"],
                "file_path": r["file_path"],
                "version": r["version"],
                "content_length": r["content_length"],
                "created_at": str(r["created_at"]),
            })

        return {
            "success": True,
            "result": {
                "artifacts": artifacts,
                "total": len(artifacts),
            },
        }

    except Exception as exc:
        logger.exception("artifact_list failed")
        return {"success": False, "error": f"获取产物列表失败: {exc}"}


# ── artifact_read ─────────────────────────────────────────────────────────

async def artifact_read_handler(
    artifact_id: str,
) -> dict[str, Any]:
    """Read the content of a specific artifact by its ID.

    Use ``artifact_list`` first to discover available artifacts, then
    call this tool with the artifact's ID to retrieve its full content.

    Args:
        artifact_id: The artifact ID (UUID from artifact_list results).
    """
    session_id = get_session_tool_context()
    if not session_id:
        return {"success": False, "error": "无法获取会话信息（无上下文）"}

    if not artifact_id or not artifact_id.strip():
        return {"success": False, "error": "产物 ID 不能为空"}

    artifact_id = artifact_id.strip()

    try:
        from app.db.session import afetch_all

        rows = await afetch_all(
            "SELECT id, session_id, file_path, content, version, created_at "
            "FROM artifacts WHERE id=$1 AND session_id=$2",
            artifact_id,
            session_id,
        )

        if not rows:
            return {"success": False, "error": f"产物 '{artifact_id}' 不存在或不属于当前会话"}

        r = rows[0]
        content = str(r["content"]) if r["content"] else ""

        # Truncate for display
        preview = content[:5000]
        truncated = len(content) > 5000
        if truncated:
            preview += "\n\n... [已截断，全文共 {} 字符]".format(len(content))

        return {
            "success": True,
            "result": {
                "id": r["id"],
                "file_path": r["file_path"],
                "version": r["version"],
                "created_at": str(r["created_at"]),
                "content": preview,
                "content_length": len(content),
                "truncated": truncated,
            },
        }

    except Exception as exc:
        logger.exception("artifact_read failed")
        return {"success": False, "error": f"读取产物失败: {exc}"}


# ── conversation_search ───────────────────────────────────────────────────

async def conversation_search_handler(
    query: str,
    max_results: int = 10,
    sender: str = "",
) -> dict[str, Any]:
    """Search the current session's conversation history for messages
    matching a query.

    Useful for recalling what was said earlier in long conversations,
    finding specific decisions or context, and cross-referencing.

    Args:
        query: Search keywords (space-separated, matches content/sender).
        max_results: Maximum number of results (default 10, max 30).
        sender: Optional filter — only search messages from this sender
                (e.g. ``"user"``, ``"Orchestrator"``, ``"Architect"``).
    """
    session_id = get_session_tool_context()
    if not session_id:
        return {"success": False, "error": "无法获取会话信息（无上下文）"}

    if not query or not query.strip():
        return {"success": False, "error": "搜索关键词不能为空"}

    query = query.strip()
    effective_max = min(max(max_results, 1), 30)

    try:
        from app.db.session import afetch_all

        # Fetch recent messages (last 200)
        if sender and sender.strip():
            rows = await afetch_all(
                "SELECT id, sender, content, type, created_at "
                "FROM messages WHERE session_id=$1 AND sender=$2 AND type!='system' "
                "ORDER BY created_at DESC LIMIT 200",
                session_id,
                sender.strip(),
            )
        else:
            rows = await afetch_all(
                "SELECT id, sender, content, type, created_at "
                "FROM messages WHERE session_id=$1 AND type!='system' "
                "ORDER BY created_at DESC LIMIT 200",
                session_id,
            )

        # Score and rank
        query_words = set(query.lower().split())
        scored: list[tuple[float, dict[str, Any]]] = []

        for r in rows:
            message_content = str(r["content"] or "")
            msg_lower = message_content.lower()
            sender_lower = (r["sender"] or "").lower()

            score = 0.0
            # Exact phrase match
            if query.lower() in msg_lower:
                score += 20
            # Word-level matches
            for w in query_words:
                count = msg_lower.count(w)
                score += count * 3
            # Sender match
            for w in query_words:
                if w in sender_lower:
                    score += 2
            # Type bonus (user messages often more relevant)
            if r["type"] == "user":
                score += 1

            if score > 0:
                scored.append((score, {
                    "message_id": r["id"],
                    "sender": r["sender"],
                    "type": r["type"],
                    "content_preview": message_content[:300],
                    "timestamp": str(r["created_at"]),
                    "relevance": round(score, 1),
                }))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [item for _, item in scored[:effective_max]]

        return {
            "success": True,
            "result": {
                "query": query,
                "results": results,
                "total": len(results),
                "searched_count": len(rows),
            },
        }

    except Exception as exc:
        logger.exception("conversation_search failed")
        return {"success": False, "error": f"搜索对话历史失败: {exc}"}


# ── memory_recall ──────────────────────────────────────────────────────────

async def memory_recall_handler(
    query: str = "",
    scope: str = "session",
    max_results: int = 10,
) -> dict[str, Any]:
    """Read-only exposure of L0/L1 cognitive memory and project facts.

    Returns memory layers as structured text so the caller (Mission
    runner, MCP client, web surface) can decide what to surface.

    Memory layers:
      - L0  Working memory: the current session's recent conversation
            (last 20 messages).  Always read-only.
      - L1  Semantic memory: the persisted ``MEMORY.md`` documents
            scoped to the current user.  Filtered by ``query`` if given.
      - Episodic memory: the session summary (auto-generated fold of
            this session so far) + global summary (aggregate of all
            sessions).
      - Facts: project-level facts from ``facts_cli`` — ADRs,
            architecture decisions, invariants.

    Args:
        query: Optional keyword filter applied to L1 memories and facts.
        scope: Memory scope hint — ``session`` (default) or ``global``.
        max_results: Maximum L1 memories / facts to return (default 10, max 30).
    """
    session_id = get_session_tool_context()
    if not session_id:
        return {"success": False, "error": "无法获取会话信息（无上下文）"}

    effective_max = min(max(max_results, 1), 30)

    layers: dict[str, Any] = {}

    # ── L0: working memory (recent conversation) ──────────────────
    try:
        from app.db.session import afetch_all

        rows = await afetch_all(
            "SELECT sender, content, type, created_at "
            "FROM messages WHERE session_id=$1 AND type!='system' "
            "ORDER BY created_at DESC LIMIT 20",
            session_id,
        )
        recent = [
            {
                "sender": r["sender"],
                "type": r["type"],
                "content": str(r["content"] or "")[:300],
                "timestamp": str(r["created_at"]),
            }
            for r in rows
        ]
        layers["L0"] = {
            "label": "working_memory",
            "description": "最近 20 条非系统消息",
            "recent_messages": recent,
            "total": len(recent),
        }
    except Exception as exc:  # noqa: BLE001
        layers["L0"] = {"error": f"L0 读取失败: {exc}", "recent_messages": [], "total": 0}

    # ── L1: session episodic summary ─────────────────────────────
    try:
        from app.services.memory.session_memory import SessionMemoryManager

        sm = SessionMemoryManager()
        session_summary = await sm.get_session_summary(session_id)
        global_summary = await sm.get_global_summary()
        layers["L1"] = {
            "label": "episodic_summary",
            "session_summary": session_summary,
            "global_summary": global_summary,
        }
    except Exception as exc:  # noqa: BLE001
        layers["L1"] = {"error": f"L1 读取失败: {exc}"}

    # ── Semantic memory (persistent MEMORY.md files) ─────────────
    try:
        from app.config import MEMORY_DIR
        from app.services.memory.storage import MemoryStorage

        storage = MemoryStorage(MEMORY_DIR)
        headers = await storage.list_headers(max_files=100)
        if query:
            q = query.lower()
            # Filter on header fields first (cheap), then read body for content match
            filtered: list = []
            for h in headers:
                haystack = " ".join([
                    h.name or "",
                    h.description or "",
                    h.type.value if hasattr(h.type, "value") else str(h.type),
                    str(h.memory_type.value) if hasattr(h.memory_type, "value") else "",
                    str(h.scope.value) if hasattr(h.scope, "value") else "",
                ]).lower()
                if q in haystack:
                    filtered.append(h)
                else:
                    # Read body and check — acceptable cost for filter
                    doc = await storage.get(h.filename)
                    if doc and q in doc.body.lower():
                        filtered.append(h)
            headers = filtered

        semantic: list[dict[str, Any]] = []
        for h in headers[:effective_max]:
            doc = await storage.get(h.filename)
            semantic.append({
                "name": h.name or h.filename,
                "description": h.description,
                "type": h.type.value if hasattr(h.type, "value") else str(h.type),
                "memoryType": h.memory_type.value if hasattr(h.memory_type, "value") else str(h.memory_type),
                "scope": h.scope.value if hasattr(h.scope, "value") else str(h.scope),
                "content_preview": (doc.body if doc else "")[:500],
                "filename": h.filename,
            })
        layers["semantic"] = {
            "label": "semantic_memory",
            "description": "持久化记忆（MEMORY.md 文档）",
            "memories": semantic,
            "total": len(headers),
        }
    except Exception as exc:  # noqa: BLE001
        layers["semantic"] = {"error": f"语义记忆读取失败: {exc}", "memories": []}

    # ── Facts: project-level facts (ADR-0107) ────────────────────
    try:
        from app.cli.project_facts import get_all_facts

        facts = get_all_facts()
        if query:
            q = query.lower()
            facts = [
                f for f in facts
                if q in str(f.get("key", "")).lower()
                or q in str(f.get("value", "")).lower()
                or q in str(f.get("tags", [])).lower()
            ]
        layers["facts"] = {
            "label": "project_facts",
            "description": "项目级事实 / ADR / 架构决策",
            "entries": facts[:effective_max],
            "total": len(facts),
        }
    except Exception as exc:  # noqa: BLE001 - facts module may not exist in all envs
        layers["facts"] = {"description": "facts 模块不可用或目录为空", "entries": [], "total": 0}

    return {
        "success": True,
        "result": {
            "query": query or "(unfiltered)",
            "scope": scope,
            "layers": layers,
        },
    }


# ── memory_retain ──────────────────────────────────────────────────────────

async def memory_retain_handler(
    fact: str,
    note: str = "",
) -> dict[str, Any]:
    """Record a request-level working-memory fact for the current session.

    This is the write-side companion to ``memory_recall``.  The fact is
    appended to the session memory file and folded into the next
    session summary so it survives the working-memory sliding window.

    Unlike the legacy ``memory_save`` (which writes to the persistent
    ``MEMORY.md`` index), ``memory_retain`` is session-scoped and
    ephemeral — designed for on-the-fly observations the agent makes
    during a run (e.g. "user prefers Python 3.12 features", "this
    project uses pytest-xdist").

    Args:
        fact: The fact to retain (20-500 chars recommended).
        note: Optional context note (why / where this was observed).
    """
    session_id = get_session_tool_context()
    if not session_id:
        return {"success": False, "error": "无法获取会话信息（无上下文）"}

    fact = fact.strip()
    if not fact:
        return {"success": False, "error": "fact 内容不能为空"}
    if len(fact) > 500:
        return {"success": False, "error": "fact 内容太长（上限 500 字符）"}

    try:
        from app.services.memory.storage import MemoryStorage
        from app.services.memory.models import MemoryMeta, MemoryDocument
        from datetime import datetime
        from app.utils.async_file import aexists, awrite_text, aread_text, amkdir
        from pathlib import Path

        # Write to a session-scoped working memory file.
        # Uses MemoryStorage's session path convention: .claude/memory/sessions/<session_id>.md
        import re as _re
        safe_sid = _re.sub(r"[^a-zA-Z0-9_\-]", "_", session_id)[:64] or "session"
        from app.config import MEMORY_DIR
        sessions_dir = Path(MEMORY_DIR) / "sessions"
        await amkdir(sessions_dir)
        session_file = sessions_dir / f"{safe_sid}.md"

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry_line = f"- [{timestamp}] {fact}"
        if note:
            entry_line += f"  — {note}"
        entry_line += "\n"

        if await aexists(session_file):
            existing = await aread_text(session_file)
            new_content = existing + ("\n" if not existing.endswith("\n") else "") + entry_line
        else:
            header = f"# Session Working Memory: {session_id}\n\n"
            new_content = header + entry_line

        await awrite_text(session_file, new_content)

        logger.info("memory_retain: recorded fact for session=%s (%d chars)", session_id, len(fact))

        return {
            "success": True,
            "result": {
                "sessionId": session_id,
                "fact": fact,
                "recordedAt": timestamp,
                "path": str(session_file),
            },
        }
    except Exception as exc:
        logger.exception("memory_retain failed")
        return {"success": False, "error": f"记忆保存失败: {exc}"}
