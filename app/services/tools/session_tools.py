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
