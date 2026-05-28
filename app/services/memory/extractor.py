from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Optional

from app.config import MEMORY_DIR
from app.db.session import dict_rows
from app.services.adapter_manager import adapter_manager
from app.services.memory.storage import MemoryStorage
from app.services.memory.models import MemoryType, sanitize_filename

logger = logging.getLogger("agenthub.memory.extractor")

EXTRACTION_PROMPT = """你是一个专业的信息提取系统，任务是从对话中提取有价值的持久化信息并将其压缩归档。

## 可提取的记忆类型

| 类型 | 用途 | 示例 |
|------|------|------|
| `user` | 用户的角色、技能、偏好、工作习惯 | "用户是资深 Go 开发者" |
| `feedback` | 用户对工作方式的纠正或肯定 | "不要 mock 数据库" |
| `project` | 无法从代码推导的项目上下文 | "项目截止日期为下周五" |
| `reference` | 指向外部资源、文档、API 的指针 | "文档位于 internal/docs/api.md" |

## 提取规则（非常重要）

1. **只提取不会随时间快速变化的信息**（不提取临时状态、一次性问题、当前情绪）
2. **宁少勿多**：如果不确定是否有用，就不要提取
3. **每条记忆必须独立且有明确含义**
4. **对话摘要**：对对话内容进行 200 字以内的精炼压缩，保留关键决策和结论
5. 如果**没有任何**值得提取的信息，返回 `"memories": []`

## 对话内容

{transcript}

## 输出格式

你必须只返回一个**纯 JSON 对象**（不要包裹 markdown 代码块，不要添加任何其他文字）：

```json
{{
  "compressed_summary": "对话的精炼摘要（200字以内）",
  "memories": [
    {{
      "name": "英文小写+下划线的唯一名称",
      "description": "一行描述（30字以内）",
      "type": "user|feedback|project|reference",
      "body": "详细内容，使用 Markdown 格式"
    }}
  ]
}}
```
"""


class MemoryExtractor:
    """Extract memories from conversations using an LLM.

    Mimics extractMemories.ts from the architecture doc:
      - forked agent pattern → here: background asyncio task
      - cursor tracking → last_extracted message ID per session
      - throttling → configurable min message gap
    """

    def __init__(self, storage: Optional[MemoryStorage] = None) -> None:
        self._storage = storage or MemoryStorage(MEMORY_DIR)
        self._state_path = self._storage.base / ".extraction_state.json"
        self._state: dict[str, Any] = self._load_state()
        # Throttle: min new messages since last extraction
        self._min_new_messages = int(os.environ.get("AGENTHUB_MEMORY_MIN_MSG", "4"))

    # ── public API ──────────────────────────────────────────────────

    async def extract_from_session(self, session_id: str) -> int:
        """Extract new memories from a session.

        Returns the number of new memories saved, or 0 if skipped/throttled.
        """
        # 1. Fetch messages
        messages = self._get_session_messages(session_id)
        if len(messages) < self._min_new_messages:
            return 0

        # 2. Check throttling (cursor tracking)
        last_id = self._state.get("sessions", {}).get(session_id, "")
        new_count = self._count_new_messages(messages, last_id)
        if new_count < self._min_new_messages:
            return 0

        # 3. Build transcript
        transcript = self._build_transcript(messages)

        # 4. Call LLM
        raw_result = await self._call_extraction_llm(transcript)
        if not raw_result:
            logger.info("extraction returned empty for session=%s", session_id)
            return 0

        # 5. Parse result
        parsed = self._parse_result(raw_result)
        if parsed is None:
            logger.warning("extraction parse failed for session=%s", session_id)
            return 0

        summary = parsed.get("compressed_summary", "")
        memories = parsed.get("memories", [])

        # 6. Save each memory
        saved = 0
        for mem in memories:
            try:
                name = mem.get("name", "").strip()
                desc = mem.get("description", "").strip()
                raw_type = mem.get("type", "reference").strip()
                body = mem.get("body", "").strip()

                if not name:
                    continue

                # Validate type
                try:
                    mtype = MemoryType(raw_type)
                except ValueError:
                    mtype = MemoryType.REFERENCE

                # Check if already exists — skip duplicates by name
                existing = self._find_by_name(name)
                if existing is not None:
                    logger.debug("memory '%s' already exists, skipping", name)
                    continue

                self._storage.save(
                    name=name,
                    description=desc,
                    type_=mtype,
                    body=body or summary,
                )
                saved += 1
            except Exception as exc:
                logger.error("failed to save memory '%s': %s", mem.get("name", "?"), exc)

        # 7. Save compressed summary as project memory if non-empty
        if summary and saved == 0 and not self._find_by_name("session_summary"):
            try:
                self._storage.save(
                    name="session_summary",
                    description=f"会话 {session_id} 的压缩摘要",
                    type_=MemoryType.PROJECT,
                    body=summary,
                    filename=f"summary_{sanitize_filename(session_id)}",
                )
                saved += 1
            except Exception as exc:
                logger.error("failed to save summary: %s", exc)

        # 8. Update cursor
        if messages:
            self._update_cursor(session_id, messages[-1]["id"])

        logger.info("extraction saved %d memories from session=%s", saved, session_id)
        return saved

    # ── message fetching ────────────────────────────────────────────

    def _get_session_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Fetch all text messages from a session, oldest first."""
        try:
            rows = dict_rows(
                "SELECT id, sender, content, type, created_at "
                "FROM messages WHERE session_id=? AND type!='system' "
                "ORDER BY created_at ASC",
                (session_id,),
            )
            return rows
        except Exception as exc:
            logger.error("failed to fetch messages for session=%s: %s", session_id, exc)
            return []

    @staticmethod
    def _count_new_messages(messages: list[dict[str, Any]], last_id: str) -> int:
        """Count messages after the given ID."""
        if not last_id:
            return len(messages)
        for i, m in enumerate(reversed(messages)):
            if m["id"] == last_id:
                return i  # messages after this one
        return len(messages)

    # ── transcript building ─────────────────────────────────────────

    @staticmethod
    def _build_transcript(messages: list[dict[str, Any]], max_chars: int = 8000) -> str:
        """Build a compressed conversation transcript.

        Truncates oldest messages first if over the limit, keeping the most
        recent conversation intact.
        """
        if not messages:
            return ""

        lines: list[str] = []
        for m in messages:
            sender = m.get("sender", "unknown")
            content = m.get("content", "")
            # Truncate very long individual messages
            if len(content) > 1000:
                content = content[:1000] + "\n... [已截断]"
            lines.append(f"[{sender}]: {content}")

        # If over limit, truncate from the top (oldest first)
        total = sum(len(l) for l in lines)
        if total > max_chars:
            # Keep newest messages
            truncated: list[str] = []
            running = 0
            for l in reversed(lines):
                running += len(l)
                truncated.append(l)
                if running > max_chars:
                    break
            truncated.reverse()
            lines = ["[早期对话已截断]"] + truncated

        return "\n\n".join(lines)

    # ── LLM call ────────────────────────────────────────────────────

    async def _call_extraction_llm(self, transcript: str) -> Optional[str]:
        """Call an LLM with the extraction prompt.

        Uses the first available configured LLM model. Falls back to mock
        which returns a simulated extraction (for development).
        """
        prompt = EXTRACTION_PROMPT.format(transcript=transcript)

        # Find the best available model
        model = self._pick_extraction_model()
        provider = model.get("provider", "mock")
        model_name = model.get("model_name", "mock")
        api_key = model.get("api_key", "")
        base_url = model.get("base_url", "")

        adapter = adapter_manager.get_adapter(provider)

        try:
            result = await adapter.execute_prompt(
                prompt=prompt,
                model=model_name,
                api_key=api_key,
                base_url=base_url,
            )
            return result.strip() if result else None
        except Exception as exc:
            logger.warning("LLM extraction failed (%s/%s): %s", provider, model_name, exc)
            # Fallback: try mock for dev/testing
            if provider != "mock":
                try:
                    mock_result = await adapter_manager.get_adapter("mock").execute_prompt(
                        prompt=prompt, model="mock",
                    )
                    return mock_result.strip() if mock_result else None
                except Exception:
                    pass
            return None

    def _pick_extraction_model(self) -> dict[str, str]:
        """Pick the best available model for extraction.

        Priority: DB model_configs → env vars → mock
        """
        # First: look up active model configs from DB
        try:
            rows = dict_rows(
                "SELECT provider, model_name, api_key, base_url "
                "FROM model_configs WHERE is_active=1 ORDER BY id DESC LIMIT 5"
            )
            if rows:
                # Prefer non-mock, non-empty-key models
                for row in rows:
                    if row.get("api_key") and row.get("provider") != "mock":
                        return row
                # Fallback to any active config
                return rows[0]
        except Exception:
            pass

        # Second: check environment-configured keys
        from app.config import OPENAI_API_KEY, ANTHROPIC_API_KEY
        if OPENAI_API_KEY:
            return {"provider": "openai", "model_name": "gpt-4o-mini", "api_key": OPENAI_API_KEY, "base_url": ""}
        if ANTHROPIC_API_KEY:
            return {"provider": "anthropic", "model_name": "claude-sonnet-4-6", "api_key": ANTHROPIC_API_KEY, "base_url": ""}

        # Fallback: mock (development only)
        return {"provider": "mock", "model_name": "mock", "api_key": "", "base_url": ""}

    # ── parsing ────────────────────────────────────────────────────

    @staticmethod
    def _parse_result(raw: str) -> Optional[dict[str, Any]]:
        """Parse the LLM response as JSON.

        Handles common LLM quirks: markdown code block wrapping, trailing
        commas, extra text before/after JSON.
        """
        text = raw.strip()

        # Strip markdown code blocks
        if text.startswith("```"):
            # Find the first { or [
            start = text.find("{")
            if start < 0:
                start = text.find("[")
            if start >= 0:
                text = text[start:]
            # Find the last } or ]
            end = text.rfind("}")
            if end < 0:
                end = text.rfind("]")
            if end >= 0:
                text = text[: end + 1]

        # Try to parse
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                return result
            return None
        except json.JSONDecodeError:
            pass

        # Attempt: find JSON object via regex
        import re

        obj_match = re.search(r"\{(?:[^{}]|(?:\{[^{}]*\}))*\}", text, re.DOTALL)
        if obj_match:
            try:
                result = json.loads(obj_match.group(0))
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        return None

    # ── duplicate detection ─────────────────────────────────────────

    def _find_by_name(self, name: str) -> Optional[str]:
        """Find a memory file by name. Returns filename or None."""
        fname = sanitize_filename(name)
        doc = self._storage.get(fname)
        if doc is not None:
            return doc.file_path
        # Also check all files by scanning frontmatter
        for h in self._storage.list_headers():
            if h.name == name:
                return h.path
        return None

    # ── state persistence (cursor tracking) ─────────────────────────

    def _load_state(self) -> dict[str, Any]:
        """Load extraction state from JSON file."""
        try:
            if self._state_path.exists():
                return json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("failed to load extraction state: %s", exc)
        return {"sessions": {}}

    def _save_state(self) -> None:
        """Persist extraction state."""
        try:
            self._state_path.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error("failed to save extraction state: %s", exc)

    def _update_cursor(self, session_id: str, message_id: str) -> None:
        """Update the extraction cursor for a session."""
        sessions = self._state.setdefault("sessions", {})
        sessions[session_id] = message_id
        self._state["last_updated"] = datetime.now().isoformat(timespec="seconds")
        self._save_state()

    def get_cursor(self, session_id: str) -> str:
        """Get the last extracted message ID for a session."""
        return self._state.get("sessions", {}).get(session_id, "")

    def reset_session(self, session_id: str) -> None:
        """Reset extraction cursor for a session (forces re-extraction)."""
        self._state.get("sessions", {}).pop(session_id, None)
        self._save_state()

    # ── backfill ────────────────────────────────────────────────────

    async def backfill_all_sessions(self) -> dict[str, int]:
        """Extract memories from all existing sessions.

        Returns a dict of session_id -> memories_saved.
        """
        try:
            sessions = dict_rows("SELECT id FROM sessions ORDER BY created_at ASC")
        except Exception as exc:
            logger.error("failed to list sessions for backfill: %s", exc)
            return {}

        results: dict[str, int] = {}
        for s in sessions:
            sid = s["id"]
            try:
                count = await self.extract_from_session(sid)
                if count > 0:
                    results[sid] = count
            except Exception as exc:
                logger.error("backfill failed for session=%s: %s", sid, exc)
        return results
