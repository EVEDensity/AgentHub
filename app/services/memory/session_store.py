"""
Per-session memory store with append-only semantics.

Each chat session gets its own memory directory:
    {user_memory_dir}/sessions/{session_id}/
        conversation.md    ←  accumulated conversation memory (append-only)
        session.json       ←  metadata (created_at, topic, tags, active status)

Design matches the user's requirements:
- Permanent memory, never auto-deleted
- Session-scoped: each chat session = one memory session
- Append-only: new content appends, never overwrites
- Auto-consolidation: long conversations are compressed (old content summarized)
- New topic detection: triggers new session creation
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.utils.async_file import (
    aexists,
    aread_text,
    awrite_text,
    amkdir,
    aiterdir,
    astat_mtime,
    astat_size,
)

logger = logging.getLogger("agenthub.memory.session_store")

# ── Constants ──────────────────────────────────────────────────────────

# Maximum size of conversation.md before auto-consolidation (characters)
MAX_CONVERSATION_CHARS = int(os.environ.get("AGENTHUB_SESSION_MAX_CHARS", "50000"))

# Target size after consolidation (characters) — keep ~70% of original
TARGET_CONSOLIDATED_CHARS = int(MAX_CONVERSATION_CHARS * 0.7)

# How many recent turns to always keep in full (not consolidated)
KEEP_RECENT_TURNS = int(os.environ.get("AGENTHUB_KEEP_RECENT_TURNS", "10"))


@dataclass
class SessionMemoryInfo:
    """Lightweight session memory metadata."""
    session_id: str
    session_name: str = ""
    topic: str = ""
    created_at: str = ""
    updated_at: str = ""
    conversation_size_chars: int = 0
    turn_count: int = 0
    is_active: bool = True


class SessionMemoryStore:
    """Per-session append-only memory storage.

    Directory layout (under the per-user memory dir):
        sessions/
            {session_id}/
                conversation.md   ←  full conversation memory
                session.json      ←  metadata
    """

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir).resolve()
        self._sessions_dir = self._base / "sessions"

    # ── Properties ───────────────────────────────────────────────────

    @property
    def sessions_dir(self) -> Path:
        return self._sessions_dir

    # ── Session lifecycle ────────────────────────────────────────────

    async def ensure_session(self, session_id: str, session_name: str = "") -> None:
        """Create the session memory directory if it doesn't exist.

        Idempotent — safe to call on every message.
        """
        session_dir = self._sessions_dir / session_id
        if await aexists(session_dir):
            return

        await amkdir(session_dir)

        # Create empty conversation.md
        conv_path = session_dir / "conversation.md"
        header = self._build_header(session_id, session_name)
        await awrite_text(conv_path, header)

        # Create session.json metadata
        meta_path = session_dir / "session.json"
        now = datetime.now().isoformat(timespec="seconds")
        meta = {
            "session_id": session_id,
            "session_name": session_name or session_id,
            "topic": "",
            "created_at": now,
            "updated_at": now,
            "turn_count": 0,
            "is_active": True,
        }
        await awrite_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))

        logger.info("created session memory dir for session=%s", session_id)

    async def append_turn(
        self,
        session_id: str,
        user_message: str,
        agent_response: str,
        sender: str = "user",
        agent_name: str = "",
    ) -> int:
        """Append a conversation turn to the session's memory file.

        Returns the new turn count after appending.

        The format is:
            ## Turn N — 2026-06-09 14:30:00

            ### user (sender)
            message content...

            ### agent (agent_name)
            response content...

        """
        await self.ensure_session(session_id)

        conv_path = self._sessions_dir / session_id / "conversation.md"
        meta = await self._load_meta(session_id)

        turn_num = meta.get("turn_count", 0) + 1
        now = datetime.now()
        now_str = now.isoformat(timespec="seconds")

        # Build turn block
        sender_label = sender or "user"
        agent_label = agent_name or "assistant"

        # Truncate very long messages in memory (keep them complete in DB)
        user_msg_truncated = self._truncate_message(user_message, max_chars=3000)
        agent_msg_truncated = self._truncate_message(agent_response, max_chars=5000)

        turn_block = f"""
## Turn {turn_num} — {now_str}

### {sender_label}
{user_msg_truncated}

### {agent_label}
{agent_msg_truncated}

"""

        # Append to file
        existing = ""
        if await aexists(conv_path):
            existing = await aread_text(conv_path)
        else:
            existing = self._build_header(session_id, meta.get("session_name", ""))

        new_content = existing.rstrip() + turn_block
        await awrite_text(conv_path, new_content)

        # Update metadata
        meta["turn_count"] = turn_num
        meta["updated_at"] = now_str
        await self._save_meta(session_id, meta)

        # Check if consolidation is needed
        conv_size = len(new_content)
        if conv_size > MAX_CONVERSATION_CHARS:
            logger.info(
                "session=%s conversation size %d > %d, triggering consolidation",
                session_id, conv_size, MAX_CONVERSATION_CHARS,
            )
            # Fire-and-forget consolidation (don't block message processing)
            import asyncio
            try:
                asyncio.ensure_future(self._auto_consolidate(session_id))
            except RuntimeError:
                pass

        return turn_num

    async def get_conversation(
        self, session_id: str, max_chars: int = 0, recent_turns: int = 0,
    ) -> str:
        """Read the full conversation memory for a session.

        Args:
            session_id: The session ID.
            max_chars: If > 0, return at most this many characters (from the end).
            recent_turns: If > 0, return only the most recent N turns.

        Returns:
            The conversation content, or empty string if no memory exists.
        """
        conv_path = self._sessions_dir / session_id / "conversation.md"
        if not await aexists(conv_path):
            return ""

        content = await aread_text(conv_path)

        if recent_turns > 0:
            # Extract the last N turns
            import re
            turns = re.split(r"\n(?=## Turn \d+ —)", content)
            # First element is the header (before first turn)
            header = turns[0] if turns else ""
            body_turns = turns[1:] if len(turns) > 1 else []
            selected = body_turns[-recent_turns:]
            content = header + "".join(selected)

        if max_chars > 0 and len(content) > max_chars:
            # Truncate from the beginning
            content = "... [早期内容已截断]\n\n" + content[-max_chars:]

        return content

    async def get_session_info(self, session_id: str) -> Optional[SessionMemoryInfo]:
        """Get metadata for a session memory."""
        meta = await self._load_meta(session_id)
        if not meta:
            return None

        conv_path = self._sessions_dir / session_id / "conversation.md"
        conv_size = 0
        if await aexists(conv_path):
            conv_size = await astat_size(conv_path)

        return SessionMemoryInfo(
            session_id=session_id,
            session_name=meta.get("session_name", session_id),
            topic=meta.get("topic", ""),
            created_at=meta.get("created_at", ""),
            updated_at=meta.get("updated_at", ""),
            conversation_size_chars=conv_size,
            turn_count=meta.get("turn_count", 0),
            is_active=meta.get("is_active", True),
        )

    async def list_sessions(self) -> list[SessionMemoryInfo]:
        """List all memory sessions, sorted by updated_at (newest first)."""
        results: list[SessionMemoryInfo] = []

        if not await aexists(self._sessions_dir):
            return results

        children = await aiterdir(self._sessions_dir)
        for child in children:
            if not child.is_dir():
                continue
            sid = child.name
            info = await self.get_session_info(sid)
            if info:
                results.append(info)

        results.sort(key=lambda s: s.updated_at, reverse=True)
        return results

    async def update_topic(self, session_id: str, topic: str) -> None:
        """Update the topic label for a session."""
        meta = await self._load_meta(session_id)
        if not meta:
            return
        meta["topic"] = topic
        meta["updated_at"] = datetime.now().isoformat(timespec="seconds")
        await self._save_meta(session_id, meta)

    async def set_active(self, session_id: str, active: bool) -> None:
        """Mark a session as active or inactive."""
        meta = await self._load_meta(session_id)
        if not meta:
            return
        meta["is_active"] = active
        meta["updated_at"] = datetime.now().isoformat(timespec="seconds")
        await self._save_meta(session_id, meta)

    async def create_new_session(self, session_id: str, session_name: str = "", topic: str = "") -> SessionMemoryInfo:
        """Explicitly create a new memory session."""
        await self.ensure_session(session_id, session_name)

        if topic:
            await self.update_topic(session_id, topic)

        info = await self.get_session_info(session_id)
        if not info:
            raise RuntimeError(f"Failed to create session memory: {session_id}")
        return info

    async def get_active_session_id(self) -> Optional[str]:
        """Get the most recently updated active session ID."""
        sessions = await self.list_sessions()
        for s in sessions:
            if s.is_active:
                return s.session_id
        return None

    # ── Consolidation ────────────────────────────────────────────────

    async def _auto_consolidate(self, session_id: str) -> None:
        """Automatically consolidate a session's conversation memory.

        Strategy:
        1. Keep the most recent KEEP_RECENT_TURNS turns in full
        2. Replace older turns with a compressed summary
        3. Update the conversation.md file
        """
        content = await self.get_conversation(session_id)
        if not content or len(content) <= MAX_CONVERSATION_CHARS:
            return

        try:
            consolidated = await self._perform_consolidation(session_id, content)
            if consolidated:
                conv_path = self._sessions_dir / session_id / "conversation.md"
                await awrite_text(conv_path, consolidated)
                logger.info(
                    "consolidated session=%s: %d → %d chars",
                    session_id, len(content), len(consolidated),
                )
        except Exception as exc:
            logger.error("consolidation failed for session=%s: %s", session_id, exc)

    async def _perform_consolidation(self, session_id: str, content: str) -> str:
        """Consolidate conversation memory by compressing older turns."""
        import re

        # Split into header + turns
        parts = re.split(r"\n(?=## Turn \d+ —)", content)
        header = parts[0] if parts else ""
        turns = parts[1:] if len(parts) > 1 else []

        if len(turns) <= KEEP_RECENT_TURNS:
            return ""  # nothing to consolidate

        # Split: old turns (to compress) + recent turns (to keep)
        old_turns = turns[:-KEEP_RECENT_TURNS]
        recent_turns = turns[-KEEP_RECENT_TURNS:]

        # Build a simple summary of old turns
        old_summary = self._summarize_turns_basic(old_turns)

        # Rebuild content
        consolidated = (
            header.rstrip()
            + "\n\n"
            + old_summary
            + "\n\n"
            + "".join(recent_turns)
        )

        return consolidated

    def _summarize_turns_basic(self, turns: list[str]) -> str:
        """Create a basic summary of older turns without LLM.

        Extracts turn numbers, timestamps, and first line of each message.
        Falls back to calling the LLM-based summarizer if available.
        """
        import re

        summary_lines = [
            "## 早期对话摘要 (Auto-consolidated)",
            "",
            f"> 以下 {len(turns)} 轮对话已被自动压缩。",
            "",
        ]

        for turn_text in turns:
            # Extract turn header
            header_match = re.match(r"## Turn (\d+) — (.+)\n", turn_text)
            if header_match:
                turn_num = header_match.group(1)
                timestamp = header_match.group(2)
                # Extract first meaningful content line
                body = turn_text[header_match.end():]
                # Get first non-empty line after the "### user" or "### agent" markers
                first_lines: list[str] = []
                for line in body.strip().split("\n"):
                    line = line.strip()
                    if line and not line.startswith("###"):
                        first_lines.append(line[:120])
                        if len(first_lines) >= 2:
                            break

                snippet = " | ".join(first_lines) if first_lines else "(无内容)"
                summary_lines.append(
                    f"- **Turn {turn_num}** ({timestamp}): {snippet}"
                )

        return "\n".join(summary_lines)

    async def trigger_llm_consolidation(self, session_id: str) -> str | None:
        """Trigger an LLM-based consolidation of a session's memory.

        This is a more sophisticated consolidation that uses an LLM to produce
        a coherent summary of the older conversation turns, preserving key
        decisions, facts, and context.

        Returns the consolidated content, or None if consolidation failed.
        """
        content = await self.get_conversation(session_id)
        if not content or len(content) < 5000:
            return None

        from app.services.adapter_manager import adapter_manager
        from app.services.secret_service import decrypt_secret
        from app.db.session import afetch_all

        try:
            # Find a suitable model for consolidation
            candidates = []
            rows = await afetch_all(
                "SELECT provider, model_name, api_key, base_url "
                "FROM model_configs WHERE is_active=1 ORDER BY id DESC LIMIT 3"
            )
            for row in rows:
                key = decrypt_secret(row.get("api_key") or "")
                if key and row.get("provider") != "mock":
                    candidates.append({**row, "api_key": key})

            if not candidates:
                from app.config import OPENAI_API_KEY, ANTHROPIC_API_KEY
                if ANTHROPIC_API_KEY:
                    candidates.append({"provider": "anthropic", "model_name": "claude-sonnet-4-6", "api_key": ANTHROPIC_API_KEY, "base_url": ""})
                elif OPENAI_API_KEY:
                    candidates.append({"provider": "openai", "model_name": "gpt-4o-mini", "api_key": OPENAI_API_KEY, "base_url": ""})

            if not candidates:
                logger.warning("no LLM available for memory consolidation")
                return None

            model = candidates[0]

            prompt = CONSOLIDATION_PROMPT.format(
                session_id=session_id,
                content=content[-15000:],  # Last 15K chars
                keep_turns=KEEP_RECENT_TURNS,
            )

            adapter = adapter_manager.get_adapter(model["provider"])
            result = await adapter.execute_prompt(
                prompt=prompt,
                model=model["model_name"],
                api_key=model["api_key"],
                base_url=model.get("base_url", ""),
            )

            if result and result.strip():
                conv_path = self._sessions_dir / session_id / "conversation.md"
                await awrite_text(conv_path, result.strip())
                return result.strip()

        except Exception as exc:
            logger.error("LLM consolidation failed for session=%s: %s", session_id, exc)

        return None

    # ── Internal helpers ─────────────────────────────────────────────

    @staticmethod
    def _build_header(session_id: str, session_name: str = "") -> str:
        """Build the header for a new conversation.md file."""
        name = session_name or session_id
        now = datetime.now().isoformat(timespec="seconds")
        return f"""# 会话记忆: {name}

> Session ID: `{session_id}`
> 创建时间: {now}
> 此文件以追加模式维护，记录本会话中的所有对话。
> 系统会自动压缩早期内容以控制文件长度。

---

"""

    async def _load_meta(self, session_id: str) -> dict[str, Any]:
        """Load session metadata from session.json."""
        meta_path = self._sessions_dir / session_id / "session.json"
        if not await aexists(meta_path):
            return {}
        try:
            raw = await aread_text(meta_path)
            return json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return {}

    async def _save_meta(self, session_id: str, meta: dict[str, Any]) -> None:
        """Save session metadata to session.json."""
        session_dir = self._sessions_dir / session_id
        await amkdir(session_dir)
        meta_path = session_dir / "session.json"
        await awrite_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))

    @staticmethod
    def _truncate_message(msg: str, max_chars: int) -> str:
        """Truncate a single message to max_chars, preserving structure."""
        if len(msg) <= max_chars:
            return msg
        return msg[:max_chars] + "\n\n... [消息过长，已截断]"


# ── LLM Consolidation Prompt ────────────────────────────────────────────

CONSOLIDATION_PROMPT = """你是一个对话记忆整合系统。请将以下对话记录压缩为精炼的记忆文档。

## 整合规则

1. **保留部分**: 保留最近 {keep_turns} 轮对话的完整内容
2. **压缩部分**: 将更早的对话压缩为结构化摘要，包含：
   - 用户的明确需求和目标
   - 做出的关键决策和结论
   - 生成的重要代码或文件
   - 未解决的遗留问题
   - 用户偏好和工作习惯
3. **格式**: 使用 Markdown，保持清晰的结构层次
4. **不要删除任何有价值的信息**，只是将详细对话转换为简洁的摘要格式

## 原始对话记忆

会话: {session_id}

{content}

## 输出

请输出整合后的完整记忆文档（保持 Markdown 格式）：
"""
