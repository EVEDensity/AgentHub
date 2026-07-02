from __future__ import annotations

from typing import Any

# ── External CLI (JSON Lines) event types ─────────────────────────────


def map_event(
    raw: dict[str, Any],
    session_id: str,
    message_id: str,
    agent_id: str,
    turn_id: str = "",
    thread_id: str = "",
) -> dict[str, Any] | None:
    """Map a single JSON Line from the CLI process into an AgentHub broadcast payload.

    Returns ``None`` for events that should not be broadcast (e.g. internal-only
    ``end`` events which are handled by the adapter's stream loop).

    Supported external event types
    ------------------------------
    * ``text``       → ``message_chunk`` (streaming text)
    * ``assistant``  → ``message_chunk`` (Claude Code streaming text)
    * ``tool_use``   → ``tool_call``
    * ``tool_result`` → ``tool_result``
    * ``end``        → ``None`` (handled internally)
    * ``system``     → ``None`` (handled internally — session init)
    """
    evt_type = raw.get("type", "")

    if evt_type == "text":
        return {
            "event": "message_chunk",
            "sessionId": session_id,
            "messageId": message_id,
            "turnId": turn_id,
            "threadId": thread_id,
            "content": raw.get("content", ""),
            "isFinal": False,
            "sender": agent_id,
        }

    # Claude Code headless mode uses "assistant" events for text output.
    # The content is an array of blocks; we extract text from text blocks.
    if evt_type == "assistant":
        text_parts = _extract_assistant_text(raw)
        if not text_parts:
            return None
        return {
            "event": "message_chunk",
            "sessionId": session_id,
            "messageId": message_id,
            "turnId": turn_id,
            "threadId": thread_id,
            "content": "".join(text_parts),
            "isFinal": False,
            "sender": agent_id,
        }

    if evt_type == "tool_use":
        name = raw.get("name", "unknown")
        tool_use_id = raw.get("id", raw.get("tool_use_id", ""))
        return {
            "event": "tool_call",
            "sessionId": session_id,
            "messageId": message_id,
            "turnId": turn_id,
            "threadId": thread_id,
            "toolCalls": [
                {
                    "name": name,
                    "arguments": _extract_arguments(raw),
                    "status": "calling",
                    "toolUseId": tool_use_id,
                }
            ],
        }

    # Codex / Claude Code tool_result events — surface as tool_result
    if evt_type == "tool_result":
        return {
            "event": "tool_result",
            "sessionId": session_id,
            "messageId": message_id,
            "turnId": turn_id,
            "threadId": thread_id,
            "results": [
                {
                    "tool_name": raw.get("name", raw.get("tool_name", "unknown")),
                    "success": not raw.get("is_error", False),
                    "result": raw.get("content", raw.get("output", "")),
                }
            ],
        }

    # system events (Claude Code session init) — internal only
    if evt_type == "system":
        return None

    if evt_type == "end":
        return None

    return None


# ── Helpers ────────────────────────────────────────────────────────────


def _extract_assistant_text(raw: dict[str, Any]) -> list[str]:
    """Extract text chunks from a Claude Code ``assistant`` event.

    Claude Code wraps its content inside ``message.content``::

        {"type":"assistant","message":{"content":[{"type":"text","text":"..."},...]}}

    Simpler CLI tools (e.g. Codex) may place content directly at the
    top level under ``content``.  We try both locations.
    """
    # Claude Code: content is nested inside "message"
    message = raw.get("message")
    if isinstance(message, dict):
        content_blocks = message.get("content", [])
    else:
        # Simple / legacy: content at top level
        content_blocks = raw.get("content", [])

    if isinstance(content_blocks, str):
        return [content_blocks]
    if not isinstance(content_blocks, list):
        return []
    parts: list[str] = []
    for block in content_blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            if text:
                parts.append(text)
    return parts


def _extract_arguments(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract tool arguments from a CLI JSON Line, varying by tool type."""
    name = raw.get("name", "")

    if name == "read_file":
        return {"path": raw.get("path", "")}
    if name == "write_file":
        return {"path": raw.get("path", ""), "content": raw.get("content", "")}
    if name == "edit_file":
        return {"path": raw.get("path", ""), "diff": raw.get("diff", ""), "old_text": raw.get("old_text", ""), "new_text": raw.get("new_text", "")}
    if name == "run_command":
        return {"cmd": raw.get("cmd", raw.get("command", "")), "cwd": raw.get("cwd", ".")}

    # Generic fallback: use "args" key or entire raw dict minus type/name
    return raw.get("args", raw.get("arguments", {}))


def is_diff_event(raw: dict[str, Any]) -> bool:
    """Check whether the tool_use event is a diff-type event (edit_file)."""
    return raw.get("type") == "tool_use" and raw.get("name") == "edit_file"


def is_terminal_event(raw: dict[str, Any]) -> bool:
    """Check whether the tool_use event is a terminal/command event."""
    return raw.get("type") == "tool_use" and raw.get("name") == "run_command"


def is_write_event(raw: dict[str, Any]) -> bool:
    """Check whether the tool_use event is a file-write event."""
    return raw.get("type") == "tool_use" and raw.get("name") in ("write_file", "edit_file")
