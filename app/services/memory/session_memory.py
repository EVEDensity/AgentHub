from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.config import MEMORY_DIR
from app.db.session import dict_rows
from app.services.adapter_manager import adapter_manager
from app.services.memory.models import MemoryType, sanitize_filename
from app.services.memory.storage import MemoryStorage

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

    def __init__(self, storage: Optional[MemoryStorage] = None) -> None:
        self._storage = storage or MemoryStorage(MEMORY_DIR)
        self._sessions_dir = self._storage.base / "sessions"
        self._state_path = self._sessions_dir / ".session_state.json"
        self._state: dict[str, Any] = self._load_state()
        # Throttle: min new messages + time cooldown
        self._min_new_messages = int(os.environ.get("AGENTHUB_MEMORY_MIN_MSG", "4"))
        self._cooldown_seconds = 300  # 5 minutes between updates per session

    # ── public API ──────────────────────────────────────────────────

    async def update_session_summary(self, session_id: str) -> str | None:
        """Summarize recent messages in a session.

        Throttled: skipped if fewer than _min_new_messages since last update
        AND less than _cooldown_seconds since last update.
        """
        # 1. Fetch recent messages
        messages = self._get_session_messages(session_id)
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

        # 3. Build transcript (last 20 messages only)
        recent = messages[-20:]
        transcript = self._build_transcript(recent)

        # 4. Call LLM for summarization
        summary = await self._call_summarization_llm(transcript)
        if not summary:
            return None

        # 5. Save session summary
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        summary_path = self._sessions_dir / f"{sanitize_filename(session_id)}"
        summary_path.write_text(summary, encoding="utf-8")

        # 6. Update cursor
        self._update_session_cursor(session_id, messages[-1]["id"])
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
        session_files = sorted(
            self._sessions_dir.glob("*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not session_files:
            return ""

        summaries: list[str] = []
        for sf in session_files[:30]:  # max 30 sessions
            try:
                text = sf.read_text(encoding="utf-8")[:300]
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
        global_path.write_text(result, encoding="utf-8")

        self._state.setdefault("global", {})["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._save_state()

        # Invalidate memory context cache so the next agent call picks up fresh data
        try:
            from app.services.agent_service import _invalidate_memory_cache
            _invalidate_memory_cache()
        except Exception:
            pass

        logger.info("global summary updated (%d chars)", len(result))
        return result

    def get_session_summary(self, session_id: str) -> str:
        """Read the cached summary for a session."""
        summary_path = self._sessions_dir / f"{sanitize_filename(session_id)}"
        try:
            if summary_path.exists():
                return summary_path.read_text(encoding="utf-8")
        except OSError:
            pass
        return ""

    def get_global_summary(self) -> str:
        """Read the cached global aggregated summary."""
        global_path = self._storage.base / "总体系统记忆文档.md"
        try:
            if global_path.exists():
                return global_path.read_text(encoding="utf-8")
        except OSError:
            pass
        return ""

    def write_session_summary(self, session_id: str, summary: str) -> None:
        """Write summary for a session and refresh cursor timestamp."""
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        summary_path = self._sessions_dir / f"{sanitize_filename(session_id)}"
        try:
            summary_path.write_text(summary or "", encoding="utf-8")
            sessions = self._state.setdefault("sessions", {})
            current = sessions.get(session_id, {})
            sessions[session_id] = {
                "last_msg_id": current.get("last_msg_id", ""),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            self._save_state()
        except OSError as exc:
            logger.error("failed to write session summary for session=%s: %s", session_id, exc)

    def list_session_summaries(self) -> list[dict[str, Any]]:
        """List all session summaries with metadata."""
        results: list[dict[str, Any]] = []
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        for sf in sorted(
            self._sessions_dir.glob("*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            try:
                text = sf.read_text(encoding="utf-8")[:200]
                mtime = sf.stat().st_mtime
                results.append({
                    "session_id": sf.stem,
                    "preview": text[:100].replace("\n", " "),
                    "updated_at": datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
                })
            except OSError:
                continue
        return results

    # ── internal helpers ────────────────────────────────────────────

    def _get_session_messages(self, session_id: str) -> list[dict[str, Any]]:
        try:
            return dict_rows(
                "SELECT id, sender, content, type, created_at "
                "FROM messages WHERE session_id=? AND type!='system' "
                "ORDER BY created_at ASC",
                (session_id,),
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
        candidates = self._list_summarization_models()

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

    def _list_summarization_models(self) -> list[dict[str, str]]:
        """Return all available models for summarization in priority order."""
        from app.services.secret_service import decrypt_secret

        candidates: list[dict[str, str]] = []
        seen: set[str] = set()

        try:
            rows = dict_rows(
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
            agent_rows = dict_rows(
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

    def _load_state(self) -> dict[str, Any]:
        try:
            if self._state_path.exists():
                return json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("failed to load session state: %s", exc)
        return {"sessions": {}, "global": {}}

    def _save_state(self) -> None:
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._state_path.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error("failed to save session state: %s", exc)

    def _update_session_cursor(self, session_id: str, message_id: str) -> None:
        sessions = self._state.setdefault("sessions", {})
        sessions[session_id] = {
            "last_msg_id": message_id,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._save_state()

    def reset_session(self, session_id: str) -> None:
        """Reset cursor and delete summary for a session."""
        self._state.get("sessions", {}).pop(session_id, None)
        self._save_state()
        summary_path = self._sessions_dir / f"{sanitize_filename(session_id)}"
        try:
            summary_path.unlink(missing_ok=True)
        except OSError:
            pass

    def get_cursor(self, session_id: str) -> str:
        session_state = self._state.get("sessions", {}).get(session_id, {})
        return session_state.get("last_msg_id", "")
