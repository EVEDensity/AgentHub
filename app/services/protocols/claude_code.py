from __future__ import annotations

import json
from typing import Any

from app.services.protocols.base import SubprocessProtocol


class ClaudeCodeProtocol(SubprocessProtocol):
    """Protocol for Anthropic's Claude Code CLI headless JSON-RPC mode.

    Claude Code supports a bidirectional JSON Lines protocol when
    launched **without** ``-p``::

        claude --output-format stream-json --input-format stream-json

    Message format (input)
    ----------------------

    .. code-block:: json

        {"type":"user","message":{"role":"user","content":[{"type":"text","text":"..."}]}}

    Tool result format (input)
    --------------------------

    .. code-block:: json

        {"type":"tool_result","tool_use_id":"tool_001","content":"..."}

    Event types (output)
    --------------------
    * ``assistant`` — text output (content array with text blocks)
    * ``tool_use`` — tool invocation request
    * ``tool_result`` — result of a tool execution
    * ``end`` — final event with reason + optional usage
    * ``system`` — session init (contains session_id for ``--resume``)
    """

    adapter_type = "local_claude"

    # Interactive mode is the primary mode for Claude Code.
    # One-shot ``-p`` mode is a fallback used when the caller explicitly
    # opts out of tool feedback (e.g. simple chat without tools).

    def supports_interactive(self) -> bool:
        return True

    def get_interactive_command(self) -> list[str]:
        # --verbose is required for stream-json output in interactive mode
        # (same requirement as -p/--print mode)
        return ["claude", "--output-format", "stream-json",
                "--input-format", "stream-json", "--verbose"]

    # ── Message encoding ────────────────────────────────────────────

    def encode_user_message(
        self,
        content: str,
        turn_id: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> str:
        """Encode a user message as a Claude Code JSON-RPC ``user`` event."""
        payload: dict[str, Any] = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": content}],
            },
        }
        return json.dumps(payload, ensure_ascii=False) + "\n"

    def encode_tool_result(
        self,
        tool_use_id: str,
        tool_name: str,
        result: dict[str, Any],
        is_error: bool = False,
    ) -> str:
        """Encode a tool execution result as a Claude Code ``tool_result`` event."""
        result_content = result.get("result", "")
        if is_error:
            result_content = result.get("error", str(result_content))

        # Serialize non-string results
        if not isinstance(result_content, str):
            result_content = json.dumps(result_content, ensure_ascii=False)

        payload: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": result_content,
        }
        if is_error:
            payload["is_error"] = True

        return json.dumps(payload, ensure_ascii=False) + "\n"

    # ── Session management ──────────────────────────────────────────

    def extract_session_id(self, raw: dict[str, Any]) -> str | None:
        """Extract the Claude Code session ID from an event.

        Looks in these locations (in priority order):
        1. ``raw["session_id"]`` — present on ``system`` init events
        2. ``raw["uuid"]`` — fallback unique identifier
        """
        sid = raw.get("session_id")
        if sid:
            return str(sid)
        uuid_val = raw.get("uuid")
        if uuid_val:
            return str(uuid_val)
        return None
