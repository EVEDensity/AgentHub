from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Optional

from app.config import MEMORY_DIR
from app.db.session import afetch_all
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
        self._min_new_messages = int(os.environ.get("AGENTHUB_MEMORY_MIN_MSG", "2"))

    # ── public API ──────────────────────────────────────────────────

    async def extract_from_session(self, session_id: str) -> int:
        """Extract new memories from a session.

        Returns the number of new memories saved, or 0 if skipped/throttled.
        """
        # 1. Fetch messages
        messages = await self._get_session_messages(session_id)
        if len(messages) < 2:
            return 0

        # 2. Check throttling (cursor tracking)
        last_id = self._state.get("sessions", {}).get(session_id, "")
        new_count = self._count_new_messages(messages, last_id)
        # For brand-new sessions (no cursor), extract immediately with 2+ messages.
        # For existing sessions, respect the configured threshold.
        min_new = 2 if not last_id else self._min_new_messages
        if new_count < min_new:
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

        memories = parsed.get("memories", [])
        summary = parsed.get("compressed_summary", "")
        session_name = await self._get_session_name(session_id)

        saved = 0

        # 6. Save individual extracted memories (the core output of extraction)
        for mem in memories:
            mem_name = mem.get("name", "").strip()
            if not mem_name:
                continue
            try:
                mem_type_raw = mem.get("type", "reference")
                try:
                    mem_type = MemoryType(mem_type_raw)
                except ValueError:
                    mem_type = MemoryType.REFERENCE

                # Check for duplicate by name before saving
                existing_path = await self._find_by_name(mem_name)
                if existing_path:
                    logger.debug("skipping duplicate memory '%s' (already exists)", mem_name)
                    continue

                await self._storage.save(
                    name=mem_name,
                    description=mem.get("description", ""),
                    type_=mem_type,
                    body=mem.get("body", ""),
                )
                saved += 1
                logger.info("saved memory '%s' type=%s from session=%s", mem_name, mem_type.value, session_id)
            except Exception as exc:
                logger.error("failed to save memory '%s': %s", mem_name, exc)

        # 7. Save session summary as a separate file (prefixed to avoid collision)
        if summary:
            session_slug = sanitize_filename(session_id)
            summary_filename = f"_session_{session_slug}"
            try:
                await self._storage.save(
                    name=f"会话摘要: {session_name or session_id}",
                    description=f"会话 {session_id} 的对话总结",
                    type_=MemoryType.PROJECT,
                    body=summary,
                    filename=summary_filename,
                )
                saved += 1
            except Exception as exc:
                logger.error("failed to save session summary: %s", exc)

        # 8. Update cursor
        if messages:
            self._update_cursor(session_id, messages[-1]["id"])

        # Invalidate memory context cache so the next agent call picks up fresh data
        try:
            from app.services.agent_service import _invalidate_memory_cache
            _invalidate_memory_cache()
        except Exception:
            pass

        logger.info("extraction saved %d memories from session=%s", saved, session_id)
        return saved

    # ── message fetching ────────────────────────────────────────────

    @staticmethod
    async def _get_session_name(session_id: str) -> str:
        try:
            row = await afetch_all(
                "SELECT name FROM sessions WHERE id=$1 LIMIT 1",
                session_id,
            )
            if row and row[0].get("name"):
                return row[0]["name"]
        except Exception:
            pass
        return session_id

    async def _get_session_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Fetch all text messages from a session, oldest first."""
        try:
            rows = await afetch_all(
                "SELECT id, sender, content, type, created_at "
                "FROM messages WHERE session_id=$1 AND type!='system' "
                "ORDER BY created_at ASC",
                session_id,
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

        Tries all available models in sequence until one succeeds.
        """
        prompt = EXTRACTION_PROMPT.format(transcript=transcript)
        candidates = await self._list_extraction_models()

        for model in candidates:
            provider = model.get("provider", "")
            model_name = model.get("model_name", "")
            api_key = model.get("api_key", "")
            base_url = model.get("base_url", "")

            if provider == "mock" or not api_key:
                continue  # skip mock and empty-key models during real extraction

            adapter = adapter_manager.get_adapter(provider)
            try:
                result = await adapter.execute_prompt(
                    prompt=prompt,
                    model=model_name,
                    api_key=api_key,
                    base_url=base_url,
                )
                if result and result.strip():
                    logger.info("extraction LLM succeeded with %s/%s", provider, model_name)
                    return result.strip()
                logger.warning("extraction LLM returned empty result (%s/%s)", provider, model_name)
            except Exception as exc:
                logger.warning("extraction LLM failed (%s/%s): %s", provider, model_name, exc)
                continue  # try next candidate

        logger.error("all extraction LLM candidates failed")
        return None

    async def _list_extraction_models(self) -> list[dict[str, str]]:
        """Return all available models for extraction in priority order.

        Priority: DB model_configs (active, with key, non-mock) → env vars.
        Mock is NOT included since it produces unusable results.
        """
        from app.services.secret_service import decrypt_secret

        candidates: list[dict[str, str]] = []
        seen: set[str] = set()

        try:
            rows = await afetch_all(
                "SELECT provider, model_name, api_key, base_url "
                "FROM model_configs WHERE is_active=1 ORDER BY id DESC LIMIT 5"
            )
            if rows:
                for row in rows:
                    decrypted = decrypt_secret(row.get("api_key") or "")
                    if decrypted and row.get("provider") != "mock":
                        key = f"{row['provider']}/{row['model_name']}"
                        if key not in seen:
                            seen.add(key)
                            candidates.append({**row, "api_key": decrypted})
        except Exception:
            pass

        # Also try models from agent_registry as fallback
        try:
            agent_rows = await afetch_all(
                "SELECT DISTINCT adapter_type AS provider, base_model_name AS model_name, "
                "api_key, base_url "
                "FROM agent_registry WHERE api_key IS NOT NULL AND api_key != '' "
                "AND adapter_type != '' AND adapter_type IS NOT NULL"
            )
            for row in agent_rows:
                decrypted = decrypt_secret(row.get("api_key") or "")
                if decrypted and row.get("provider") and row.get("provider") != "mock":
                    key = f"{row['provider']}/{row['model_name']}"
                    if key not in seen:
                        seen.add(key)
                        candidates.append({**row, "api_key": decrypted})
        except Exception:
            pass

        from app.config import OPENAI_API_KEY, ANTHROPIC_API_KEY
        if OPENAI_API_KEY:
            key = f"openai/gpt-4o-mini"
            if key not in seen:
                candidates.append({"provider": "openai", "model_name": "gpt-4o-mini", "api_key": OPENAI_API_KEY, "base_url": ""})
        if ANTHROPIC_API_KEY:
            key = f"anthropic/claude-sonnet-4-6"
            if key not in seen:
                candidates.append({"provider": "anthropic", "model_name": "claude-sonnet-4-6", "api_key": ANTHROPIC_API_KEY, "base_url": ""})

        return candidates

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

    async def _find_by_name(self, name: str) -> Optional[str]:
        """Find a memory file by name. Returns filename or None."""
        fname = sanitize_filename(name)
        doc = await self._storage.get(fname)
        if doc is not None:
            return doc.file_path
        # Also check all files by scanning frontmatter
        for h in await self._storage.list_headers():
            if h.name == name:
                return h.path
        return None

    # ── state persistence (cursor tracking) ─────────────────────────

    def _load_state(self) -> dict[str, Any]:
        """Load extraction state from JSON file (safe for sync context — constructor)."""
        try:
            if self._state_path.exists():
                return json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("failed to load extraction state: %s", exc)
        return {"sessions": {}}

    async def _load_state_async(self) -> dict[str, Any]:
        """Async version — use from async callers to avoid blocking the event loop."""
        return await asyncio.to_thread(self._load_state)

    def _save_state(self) -> None:
        """Persist extraction state.

        When called from an async context the actual disk write is dispatched
        via ``asyncio.to_thread`` to avoid blocking.  When called from a sync
        context (e.g. constructor path) the write is performed inline.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — write inline (safe for constructor / sync boot)
            self._write_state_file()
            return
        loop.create_task(asyncio.to_thread(self._write_state_file))

    def _write_state_file(self) -> None:
        """Perform the actual file write (may be called from any thread)."""
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
            sessions = await afetch_all("SELECT id FROM sessions ORDER BY created_at ASC")
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
