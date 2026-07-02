"""
Multi-level prompt cache for reducing repeated prompt construction cost.

Problem: Every LLM call rebuilds 4-8KB prompts from scratch — reading settings
from disk, querying the DB for conversation history, rebuilding tool definitions,
and formatting massive system instruction blocks.  In a tool-call loop (up to
5 iterations) this multiplies the waste, with 80-90% of the prompt being
identical across iterations.

Solution: Three-tier cache that operates at different lifetimes:

  Tier 1 (request-scoped) — System prompt prefix for the current agent+session.
      Reused across tool-call loop iterations.  The conversation content
      (which changes per iteration) is appended to the cached prefix.

  Tier 2 (TTL-scoped) — Cross-request caches for settings, conversation
      history, and tool definitions.  Short TTL ensures freshness while
      eliminating redundant disk I/O and DB queries within a burst.

  Tier 3 (process-lifetime) — Tool section definitions.  These only change
      when tool code is modified (i.e. server restart), so they are cached
      for the process lifetime.

Usage:
    from app.services.prompt_cache import prompt_cache

    # Get cached system prefix for the current request
    prefix = prompt_cache.get_system_prefix(cache_key)
    if prefix is None:
        prefix = build_expensive_system_prefix(...)
        prompt_cache.set_system_prefix(cache_key, prefix)

    # The final prompt is: cached_prefix + dynamic_conversation_content
    prompt = prefix + conv_text
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

logger = logging.getLogger("agenthub.prompt_cache")

# ── Cache entry helpers ──────────────────────────────────────────────


def _now() -> float:
    return time.monotonic()


def _is_fresh(ts: float, ttl: float) -> bool:
    return (_now() - ts) < ttl


def _make_key(*parts: str) -> str:
    """Deterministic cache key from string parts."""
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


# ── Prompt Cache ──────────────────────────────────────────────────────


class PromptCache:
    """Multi-level cache for prompt construction components.

    All caches are in-process only (no Redis dependency).  They survive
    for the lifetime of the uvicorn worker process and are naturally
    cleared on restart (which is also when tool code / settings change).
    """

    def __init__(self) -> None:
        # ── Tier 1: System prompt prefix (request-scoped, short TTL) ─
        # Maps cache_key → (timestamp, prefix_text)
        # The prefix is everything before the variable user-content section.
        # TTL is short (60s) — long enough to cover a tool-call loop
        # (typically < 30s) but not so long that stale state lingers.
        self._prefix: dict[str, tuple[float, str]] = {}
        self._PREFIX_TTL = 60.0

        # ── Tier 2a: Settings cache ──────────────────────────────────
        # _load_settings() reads settings.json from disk on every
        # build_prompt() call.  The file changes rarely; a 30s TTL
        # eliminates redundant I/O during rapid-fire messages.
        self._settings: tuple[float, dict[str, Any]] = (0.0, {})
        self._SETTINGS_TTL = 30.0

        # ── Tier 2b: Conversation history cache ──────────────────────
        # Maps session_id → (timestamp, history_text)
        # DB query avoided on rapid successive messages in the same session.
        self._history: dict[str, tuple[float, str]] = {}
        self._HISTORY_TTL = 5.0  # short — conversation changes quickly

        # ── Tier 3: Tool section cache ───────────────────────────────
        # Maps (tools_fingerprint, agent_id) → tool_section_text
        # Tool definitions don't change without a server restart.
        self._tool_section: dict[str, str] = {}

    # ── System prefix (Tier 1) ───────────────────────────────────────

    def get_system_prefix(self, cache_key: str) -> str | None:
        """Return the cached system prefix, or None if expired/missing."""
        entry = self._prefix.get(cache_key)
        if entry is None:
            return None
        ts, text = entry
        if _is_fresh(ts, self._PREFIX_TTL):
            logger.debug("prompt_cache: system_prefix HIT key=%s", cache_key[:12])
            return text
        del self._prefix[cache_key]
        logger.debug("prompt_cache: system_prefix EXPIRED key=%s", cache_key[:12])
        return None

    def set_system_prefix(self, cache_key: str, text: str) -> None:
        """Store the system prefix for reuse across tool-call iterations."""
        self._prefix[cache_key] = (_now(), text)
        logger.debug(
            "prompt_cache: system_prefix SET key=%s len=%d",
            cache_key[:12], len(text),
        )

    @staticmethod
    def make_prefix_key(
        agent_id: str,
        domain: str,
        tools_enabled: bool,
        available_tools: tuple[str, ...] | None,
        session_id: str,
        preprocess_context: str,
        model_provider: str,
        model_name: str,
    ) -> str:
        """Build a cache key that captures all static prefix inputs.

        The key intentionally EXCLUDES the user message content and
        conversation history (which change across iterations).
        """
        tools_fp = ",".join(sorted(available_tools)) if available_tools else "*"
        return _make_key(
            agent_id,
            domain,
            "1" if tools_enabled else "0",
            tools_fp,
            session_id,
            hashlib.sha256(preprocess_context.encode()).hexdigest()[:16],
            model_provider,
            model_name,
        )

    # ── Settings (Tier 2a) ────────────────────────────────────────────

    def get_settings(self) -> dict[str, Any] | None:
        """Return cached settings if fresh, else None."""
        ts, data = self._settings
        if data and _is_fresh(ts, self._SETTINGS_TTL):
            return data
        return None

    def set_settings(self, data: dict[str, Any]) -> None:
        self._settings = (_now(), data)

    def invalidate_settings(self) -> None:
        self._settings = (0.0, {})

    # ── Conversation history (Tier 2b) ────────────────────────────────

    def get_history(self, session_id: str) -> str | None:
        """Return cached conversation history if fresh, else None."""
        entry = self._history.get(session_id)
        if entry is None:
            return None
        ts, text = entry
        if _is_fresh(ts, self._HISTORY_TTL):
            return text
        del self._history[session_id]
        return None

    def set_history(self, session_id: str, text: str) -> None:
        self._history[session_id] = (_now(), text)

    def invalidate_history(self, session_id: str | None = None) -> None:
        if session_id is None:
            self._history.clear()
        else:
            self._history.pop(session_id, None)

    # ── Tool section (Tier 3) ─────────────────────────────────────────

    def get_tool_section(self, agent_id: str, available_tools: tuple[str, ...] | None) -> str | None:
        """Return cached tool section, or None.  Process-lifetime cache."""
        key = _make_key(
            agent_id,
            ",".join(sorted(available_tools)) if available_tools else "*",
        )
        return self._tool_section.get(key)

    def set_tool_section(self, agent_id: str, available_tools: tuple[str, ...] | None, text: str) -> None:
        key = _make_key(
            agent_id,
            ",".join(sorted(available_tools)) if available_tools else "*",
        )
        self._tool_section[key] = text

    # ── Lifecycle ─────────────────────────────────────────────────────

    def clear(self) -> None:
        """Clear all caches (useful for testing)."""
        self._prefix.clear()
        self._settings = (0.0, {})
        self._history.clear()
        self._tool_section.clear()
        logger.debug("prompt_cache: all caches cleared")

    def stats(self) -> dict[str, Any]:
        """Return cache statistics for debugging."""
        return {
            "prefix_entries": len(self._prefix),
            "history_entries": len(self._history),
            "tool_section_entries": len(self._tool_section),
            "settings_cached": bool(self._settings[1]),
        }


# ── Module-level singleton ────────────────────────────────────────────

prompt_cache = PromptCache()
