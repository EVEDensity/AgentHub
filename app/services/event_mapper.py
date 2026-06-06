from __future__ import annotations

from typing import Any

# ── External CLI (JSON Lines) event types ─────────────────────────────


def map_event(
    raw: dict[str, Any],
    session_id: str,
    message_id: str,
    agent_id: str,
) -> dict[str, Any] | None:
    """Map a single JSON Line from the CLI process into an AgentHub broadcast payload.

    Returns ``None`` for events that should not be broadcast (e.g. internal-only
    ``end`` events which are handled by the adapter's stream loop).

    Supported external event types
    ------------------------------
    * ``text``       → ``message_chunk`` (streaming text)
    * ``tool_use``   → ``tool_call``
    * ``end``        → ``None`` (handled internally)
    """
    evt_type = raw.get("type", "")

    if evt_type == "text":
        return {
            "event": "message_chunk",
            "sessionId": session_id,
            "messageId": message_id,
            "content": raw.get("content", ""),
            "isFinal": False,
            "sender": agent_id,
        }

    if evt_type == "tool_use":
        name = raw.get("name", "unknown")
        return {
            "event": "tool_call",
            "sessionId": session_id,
            "messageId": message_id,
            "toolCalls": [
                {
                    "name": name,
                    "arguments": _extract_arguments(raw),
                    "status": "calling",
                }
            ],
        }

    if evt_type == "end":
        return None

    return None


# ── Helpers ────────────────────────────────────────────────────────────


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
