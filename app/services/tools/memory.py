"""Memory (session-level persistent memory) builtin tools.

Split out of ``builtin_tools.py``.
"""

from __future__ import annotations

import logging
import json
import os
from pathlib import Path
from typing import Any

from app.config import MEMORY_DIR

logger = logging.getLogger("agenthub.tools.builtin.memory")

async def memory_save_handler(
    name: str,
    content: str,
    type: str = "reference",
    description: str = "",
) -> dict[str, Any]:
    """Save a persistent memory entry for the current user.

    Memories persist across sessions and are searchable via ``memory_search``.
    Uses the file-based memory storage system (MEMORY.md index + .md files).

    Args:
        name: Short kebab-case slug for the memory (e.g. ``user-preferences``).
        content: The memory content (markdown body).
        type: Memory type — ``user`` | ``feedback`` | ``project`` | ``reference``.
        description: One-line summary for the MEMORY.md index.
    """
    from app.config import MEMORY_DIR
    from app.services.memory.storage import MemoryStorage
    from app.services.memory.models import MemoryType

    if not name or not name.strip():
        return {"success": False, "error": "记忆名称不能为空"}
    if not content or not content.strip():
        return {"success": False, "error": "记忆内容不能为空"}

    name = name.strip()
    valid_types = {"user": MemoryType.USER, "feedback": MemoryType.FEEDBACK,
                   "project": MemoryType.PROJECT, "reference": MemoryType.REFERENCE}
    mem_type = valid_types.get(type.strip().lower() if type else "reference", MemoryType.REFERENCE)

    try:
        storage = MemoryStorage(MEMORY_DIR)
        doc = await storage.save(
            name=name,
            description=description.strip() if description else name,
            type_=mem_type,
            body=content.strip(),
        )
        return {
            "success": True,
            "result": {
                "name": doc.meta.name,
                "filename": Path(doc.file_path).name,
                "type": doc.meta.type.value,
                "description": doc.meta.description,
                "updated_at": doc.meta.updated_at,
                "body_preview": content.strip()[:300],
            },
        }
    except Exception as exc:
        logger.exception("memory_save failed")
        return {"success": False, "error": f"保存记忆失败: {exc}"}


# ── memory_search ─────────────────────────────────────────────────────

async def memory_search_handler(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search the agent's persistent memory for relevant information."""
    if not query or not query.strip():
        return {"success": False, "error": "搜索关键词不能为空"}

    query = query.strip()
    try:
        from app.config import MEMORY_DIR as _mem_dir
        from app.services.memory.storage import MemoryStorage
        from app.services.memory.models import MemoryType

        storage = MemoryStorage(_mem_dir)
        headers = await storage.list_headers(max_files=200)

        # Simple keyword matching with scoring
        scored: list[tuple[float, dict]] = []
        query_lower = query.lower()
        for h in headers:
            score = 0.0
            name_lower = (h.name or "").lower()
            desc_lower = (h.description or "").lower()
            type_str = h.type.value if isinstance(h.type, MemoryType) else str(h.type)

            # Exact name match
            if query_lower in name_lower:
                score += 10
            # Description match
            if query_lower in desc_lower:
                score += 5
            # Type match
            if query_lower in type_str.lower():
                score += 3
            # Word-level matching
            query_words = set(query_lower.split())
            name_words = set(name_lower.replace("_", " ").replace("-", " ").split())
            desc_words = set(desc_lower.split())
            score += len(query_words & name_words) * 2
            score += len(query_words & desc_words) * 1

            if score > 0:
                scored.append((score, {
                    "name": h.name,
                    "filename": h.filename,
                    "type": type_str,
                    "description": h.description[:200],
                    "relevance_score": round(score, 1),
                }))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [item for _, item in scored[:max_results]]

        if results:
            return {
                "success": True,
                "result": {
                    "query": query,
                    "results": results,
                    "total": len(results),
                    "searched_files": len(headers),
                },
            }
        else:
            return {
                "success": True,
                "result": {
                    "query": query,
                    "results": [],
                    "total": 0,
                    "message": f"在 {len(headers)} 条记忆中未找到与 '{query}' 相关的内容",
                },
            }
    except Exception as exc:
        logger.exception("memory_search failed")
        return {"success": False, "error": f"记忆搜索失败: {exc}"}

