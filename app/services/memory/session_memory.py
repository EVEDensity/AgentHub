from __future__ import annotations

import logging
import os
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.config import MEMORY_DIR
from app.services.adapter_manager import adapter_manager
from app.services.memory.models import CognitiveMemoryType, MemoryScope, MemoryType, sanitize_filename
from app.services.memory.storage import MemoryStorage
from app.services.memory.summary_version import SummaryVersion, should_accept_summary
from app.utils.async_file import (
    aexists,
    aread_text,
    awrite_text,
    aread_json,
    awrite_json,
    aglob_simple,
    astat_mtime,
    aunlink,
    amkdir,
)

logger = logging.getLogger("agenthub.memory.session")

SESSION_SUMMARY_PROMPT = """你是一个会话总结系统。请将以下对话压缩为 200 字以内的精炼摘要。

重点关注：
1. 用户的明确需求和目标
2. 做出的关键决策和结论
3. 生成的代码或文件（如有）
4. 未解决的遗留问题

只返回摘要文本，不要添加任何前缀或后缀。

对话内容：
{transcript}
"""

SESSION_SUMMARY_INCREMENTAL_PROMPT = """你是一个会话总结系统。以下是某个会话的既有摘要，以及该会话**新增**的对话记录。请把新增内容合入既有摘要，输出更新后的摘要（ADR-0107 增量折叠：只处理变化，不重读整段历史）。

要求：
1. 保留既有摘要中的关键目标、决策、产物与遗留问题
2. 合并新增对话中的新需求、新决策、新产物与新遗留问题；被新增内容推翻或已不再相关的过时信息可以删去
3. 只返回摘要文本，不要添加任何前缀或后缀；总长度控制在 250 字以内

既有摘要：
{existing_summary}

新增对话：
{transcript}
"""

GLOBAL_SUMMARY_PROMPT = """你是一个全局记忆聚合系统。以下是多个会话的摘要列表。请综合为一段 500 字以内的全局摘要。

要求：
1. 识别跨会话的共性主题和模式
2. 记录重要的长期决策或偏好
3. 标注哪些信息可能已过时
4. 使用中文输出

会话摘要列表：
{summaries}
"""


class SessionMemoryManager:
    """Maintain per-session and global conversation summaries.

    Session summaries feed into the global memory pool, giving agents
    cross-session awareness without loading entire conversation histories.

    State file: .claude/memory/sessions/.session_state.json
    """

    _summary_locks: dict[str, asyncio.Lock] = {}

    def __init__(self, storage: Optional[MemoryStorage] = None) -> None:
        self._storage = storage or MemoryStorage(MEMORY_DIR)
        self._sessions_dir = self._storage.base / "sessions"
        self._state_path = self._sessions_dir / ".session_state.json"
        self._state: dict[str, Any] = {"sessions": {}, "global": {}}
        self._state_loaded: bool = False
        # Throttle: min new messages + time cooldown
        self._min_new_messages = int(os.environ.get("AGENTHUB_MEMORY_MIN_MSG", "2"))
        self._cooldown_seconds = 120  # 2 minutes between updates per session

    # ── public API ──────────────────────────────────────────────────

    async def update_session_summary(self, session_id: str) -> str | None:
        """Summarize recent messages in a session.

        Throttled: skipped if fewer than _min_new_messages since last update
        AND less than _cooldown_seconds since last update.
        """
        # 0. Ensure state is loaded
        await self._ensure_state_loaded()

        # 1. Fetch recent messages
        messages = await self._get_session_messages(session_id)
        if len(messages) < self._min_new_messages:
            return None

        # 2. Check throttle
        session_state = self._state.get("sessions", {}).get(session_id, {})
        last_msg_id = session_state.get("last_msg_id", "")
        last_updated = session_state.get("updated_at", "")
        new_count = self._count_new_messages(messages, last_msg_id)
        if new_count < self._min_new_messages and last_updated:
            try:
                last_ts = datetime.fromisoformat(last_updated)
                if (datetime.now() - last_ts).total_seconds() < self._cooldown_seconds:
                    return None
            except (ValueError, TypeError):
                pass

        # 3/4. Summarize — incremental fold when a summary exists (ADR-0107):
        #      feed ONLY the messages after the cursor plus the existing digest,
        #      never re-summarize the whole history from scratch.
        recent = messages[-20:]
        existing = await self.get_session_summary(session_id)
        if existing:
            new_messages = self._messages_after(messages, last_msg_id)
            prompt = self._build_incremental_input(existing, new_messages)
            if prompt is None:
                return None  # nothing new to fold — keep current digest
            summary = await self._call_llm_raw(prompt)
        else:
            transcript = self._build_transcript(recent)
            summary = await self._call_summarization_llm(transcript)
        if not summary:
            return None

        # 5. Save session summary
        await amkdir(self._sessions_dir)
        summary_path = self._sessions_dir / f"{sanitize_filename(session_id)}"
        await awrite_text(summary_path, summary)

        # 6. Update cursor
        await self._update_session_cursor(session_id, messages[-1]["id"])
        logger.info("session summary updated for session=%s (%d chars)", session_id, len(summary))

        # 7. Update global summary (throttled: max every 10 minutes)
        global_state = self._state.get("global", {})
        global_updated = global_state.get("updated_at", "")
        should_update_global = True
        if global_updated:
            try:
                global_ts = datetime.fromisoformat(global_updated)
                if (datetime.now() - global_ts).total_seconds() < 600:
                    should_update_global = False
            except (ValueError, TypeError):
                pass
        if should_update_global:
            await self.update_global_summary()

        return summary

    async def update_global_summary(self) -> str:
        """Aggregate all session summaries into a single global summary."""
        await self._ensure_state_loaded()
        raw_files = await aglob_simple(self._sessions_dir, "*.md")
        if not raw_files:
            return ""

        # Collect mtimes asynchronously for sorting
        file_mtimes: list[tuple[Path, float]] = []
        for p in raw_files:
            try:
                mtime = await astat_mtime(p)
                file_mtimes.append((p, mtime))
            except OSError:
                file_mtimes.append((p, 0.0))
        file_mtimes.sort(key=lambda x: x[1], reverse=True)
        session_files = [f for f, _ in file_mtimes]

        summaries: list[str] = []
        for sf in session_files[:30]:  # max 30 sessions
            try:
                text = (await aread_text(sf))[:300]
                summaries.append(f"[{sf.stem}]: {text}")
            except OSError:
                continue

        if not summaries:
            return ""

        prompt = GLOBAL_SUMMARY_PROMPT.format(summaries="\n\n".join(summaries))
        result = await self._call_llm_raw(prompt)
        if not result:
            return ""

        # Save global summary
        global_path = self._storage.base / "总体系统记忆文档.md"
        await awrite_text(global_path, result)

        global_state = self._state.setdefault("global", {})
        global_state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        global_state["memory_type"] = CognitiveMemoryType.SEMANTIC.value
        global_state["scope"] = MemoryScope.USER.value
        global_state["source"] = "session-summary-aggregation"
        global_state["version"] = max(1, int(global_state.get("version", 0)) + 1)
        await self._save_state()

        # Invalidate memory context cache so the next agent call picks up fresh data
        try:
            from app.services.agent_service import _invalidate_memory_cache
            _invalidate_memory_cache()
        except Exception:
            pass

        logger.info("global summary updated (%d chars)", len(result))
        return result

    async def get_session_summary(self, session_id: str) -> str:
        """Read the cached summary for a session."""
        summary_path = self._sessions_dir / f"{sanitize_filename(session_id)}"
        try:
            if await aexists(summary_path):
                return await aread_text(summary_path)
        except OSError:
            pass
        return ""

    async def get_global_summary(self) -> str:
        """Read the cached global aggregated summary."""
        global_path = self._storage.base / "总体系统记忆文档.md"
        try:
            if await aexists(global_path):
                return await aread_text(global_path)
        except OSError:
            pass
        return ""

    async def write_session_summary(
        self,
        session_id: str,
        summary: str,
        *,
        covered_sequence_start: int = 0,
        covered_sequence_end: int = 0,
        generated_at: float = 0.0,
        source_event_id: str = "",
        force: bool = True,
    ) -> bool:
        """Write summary for a session and refresh cursor timestamp."""
        await amkdir(self._sessions_dir)
        summary_path = self._sessions_dir / f"{sanitize_filename(session_id)}"
        lock_key = str(summary_path)
        lock = self._summary_locks.setdefault(lock_key, asyncio.Lock())
        try:
            async with lock:
                await self._ensure_state_loaded()
                sessions = self._state.setdefault("sessions", {})
                current = sessions.get(session_id, {})
                current_version = SummaryVersion(
                    covered_sequence_start=int(current.get("covered_sequence_start", 0)),
                    covered_sequence_end=int(current.get("covered_sequence_end", 0)),
                    generated_at=float(current.get("summary_generated_at", 0.0)),
                    source_event_id=str(current.get("source_event_id", "")),
                )
                incoming_version = SummaryVersion(
                    covered_sequence_start=covered_sequence_start,
                    covered_sequence_end=covered_sequence_end,
                    generated_at=generated_at,
                    source_event_id=source_event_id,
                )
                if not force and not should_accept_summary(current_version, incoming_version):
                    logger.info(
                        "stale session summary rejected session=%s incoming_end=%d current_end=%d event=%s",
                        session_id, covered_sequence_end, current_version.covered_sequence_end, source_event_id,
                    )
                    return False
                await awrite_text(summary_path, summary or "")
                stored_sequence_start = covered_sequence_start or current_version.covered_sequence_start
                stored_sequence_end = covered_sequence_end or current_version.covered_sequence_end
                sessions[session_id] = {
                    **current,
                    "last_msg_id": current.get("last_msg_id", ""),
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                    "memory_type": CognitiveMemoryType.EPISODIC.value,
                    "scope": MemoryScope.SESSION.value,
                    "source": "session-summary",
                    "version": max(1, int(current.get("version", 0)) + 1),
                    "covered_sequence_start": stored_sequence_start,
                    "covered_sequence_end": stored_sequence_end,
                    "summary_generated_at": generated_at or current_version.generated_at,
                    "source_event_id": source_event_id or current_version.source_event_id,
                }
                await self._save_state()
                return True
        except OSError as exc:
            logger.error("failed to write session summary for session=%s: %s", session_id, exc)
            return False

    async def list_session_summaries(self) -> list[dict[str, Any]]:
        """List all session summaries with metadata."""
        results: list[dict[str, Any]] = []
        await amkdir(self._sessions_dir)
        raw_files = await aglob_simple(self._sessions_dir, "*.md")
        # Collect mtimes asynchronously for sorting
        file_mtimes: list[tuple[Path, float]] = []
        for sf in raw_files:
            try:
                mtime = await astat_mtime(sf)
                file_mtimes.append((sf, mtime))
            except OSError:
                file_mtimes.append((sf, 0.0))
        file_mtimes.sort(key=lambda x: x[1], reverse=True)
        for sf, mtime in file_mtimes:
            try:
                text = (await aread_text(sf))[:200]
                results.append({
                    "session_id": sf.stem,
                    "preview": text[:100].replace("\n", " "),
                    "updated_at": datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
                })
            except OSError:
                continue
        return results

    # ── internal helpers ────────────────────────────────────────────

    async def _get_session_messages(self, session_id: str) -> list[dict[str, Any]]:
        from app.db.session import afetch_all

        try:
            return await afetch_all(
                "SELECT id, sender, content, type, created_at "
                "FROM messages WHERE session_id=$1 AND type!='system' "
                "ORDER BY created_at ASC",
                session_id,
            )
        except Exception as exc:
            logger.error("failed to fetch messages for session=%s: %s", session_id, exc)
            return []

    @staticmethod
    def _count_new_messages(messages: list[dict[str, Any]], last_id: str) -> int:
        if not last_id:
            return len(messages)
        for i, m in enumerate(reversed(messages)):
            if m["id"] == last_id:
                return i
        return len(messages)

    @staticmethod
    def _messages_after(messages: list[dict[str, Any]], last_id: str) -> list[dict[str, Any]]:
        """Return the messages strictly after the persisted cursor.

        ``last_msg_id == ''`` (never summarized) returns everything;
        when the cursor is missing from the fetched set the whole list is
        returned (fail-safe: recompute from scratch rather than drop data).
        """
        if not last_id:
            return list(messages)
        for i, m in enumerate(messages):
            if m["id"] == last_id:
                return messages[i + 1:]
        return list(messages)

    @staticmethod
    def _build_incremental_input(
        existing_summary: str,
        new_messages: list[dict[str, Any]],
    ) -> str | None:
        """Compose the incremental-fold prompt: existing digest + only the new
        turns. Returns None when there is nothing new to fold, so the caller
        keeps the current digest untouched (ADR-0107 change-only rollup).
        """
        new_messages = new_messages[-20:]
        if not new_messages:
            return None
        transcript = SessionMemoryManager._build_transcript(new_messages)
        if not transcript.strip():
            return None
        return SESSION_SUMMARY_INCREMENTAL_PROMPT.format(
            existing_summary=existing_summary[:1200] or "（无既有摘要）",
            transcript=transcript,
        )

    @staticmethod
    def _build_transcript(messages: list[dict[str, Any]], max_chars: int = 4000) -> str:
        if not messages:
            return ""
        lines: list[str] = []
        for m in messages:
            sender = m.get("sender", "unknown")
            content = m.get("content", "")
            if len(content) > 500:
                content = content[:500] + "\n... [已截断]"
            lines.append(f"[{sender}]: {content}")
        total = sum(len(l) for l in lines)
        if total > max_chars:
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

    async def _call_summarization_llm(self, transcript: str) -> str | None:
        prompt = SESSION_SUMMARY_PROMPT.format(transcript=transcript)
        return await self._call_llm_raw(prompt)

    async def _call_llm_raw(self, prompt: str) -> str | None:
        candidates = await self._list_summarization_models()

        for model in candidates:
            provider = model.get("provider", "")
            model_name = model.get("model_name", "")
            api_key = model.get("api_key", "")
            base_url = model.get("base_url", "")

            if provider == "mock" or not api_key:
                continue

            adapter = adapter_manager.get_adapter(provider)
            try:
                result = await adapter.execute_prompt(
                    prompt=prompt,
                    model=model_name,
                    api_key=api_key,
                    base_url=base_url,
                )
                if result and result.strip():
                    return result.strip()
            except Exception as exc:
                logger.warning("session summarization failed (%s/%s): %s", provider, model_name, exc)
                continue

        logger.error("all summarization LLM candidates failed")
        return None

    async def _list_summarization_models(self) -> list[dict[str, str]]:
        """Return all available models for summarization in priority order."""
        from app.db.session import afetch_all
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
            candidates.append({"provider": "openai", "model_name": "gpt-4o-mini", "api_key": OPENAI_API_KEY, "base_url": ""})
        if ANTHROPIC_API_KEY:
            candidates.append({"provider": "anthropic", "model_name": "claude-sonnet-4-6", "api_key": ANTHROPIC_API_KEY, "base_url": ""})

        return candidates

    # ── state persistence ───────────────────────────────────────────

    async def _ensure_state_loaded(self) -> None:
        """Lazily load session state from disk on first access."""
        if not self._state_loaded:
            self._state = await self._load_state()
            self._state_loaded = True

    async def _load_state(self) -> dict[str, Any]:
        try:
            if await aexists(self._state_path):
                return await aread_json(self._state_path)
        except (OSError, ValueError) as exc:
            logger.warning("failed to load session state: %s", exc)
        return {"sessions": {}, "global": {}}

    async def _save_state(self) -> None:
        await amkdir(self._sessions_dir)
        try:
            await awrite_json(self._state_path, self._state)
        except OSError as exc:
            logger.error("failed to save session state: %s", exc)

    async def _update_session_cursor(self, session_id: str, message_id: str) -> None:
        await self._ensure_state_loaded()
        sessions = self._state.setdefault("sessions", {})
        current = sessions.get(session_id, {})
        sessions[session_id] = {
            **current,
            "last_msg_id": message_id,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "memory_type": CognitiveMemoryType.EPISODIC.value,
            "scope": MemoryScope.SESSION.value,
            "source": "session-summary",
            "version": max(1, int(current.get("version", 0)) + 1),
        }
        await self._save_state()

    async def reset_session(self, session_id: str) -> None:
        """Reset cursor and delete summary for a session."""
        await self._ensure_state_loaded()
        self._state.get("sessions", {}).pop(session_id, None)
        await self._save_state()
        summary_path = self._sessions_dir / f"{sanitize_filename(session_id)}"
        try:
            await aunlink(summary_path, missing_ok=True)
        except OSError:
            pass

    async def get_cursor(self, session_id: str) -> str:
        await self._ensure_state_loaded()
        session_state = self._state.get("sessions", {}).get(session_id, {})
        return session_state.get("last_msg_id", "")
