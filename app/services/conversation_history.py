from __future__ import annotations

from typing import Any

from app.services.text_processing import strip_think_tags


def build_conversation_history_transcript(
    rows: list[dict[str, Any]],
    *,
    max_chars: int = 5000,
    max_messages: int = 16,
    max_message_chars: int = 600,
) -> str:
    if not rows:
        return ""

    lines: list[str] = []
    total = 0
    for row in rows[:max_messages]:
        sender = str(row.get("sender", "unknown"))
        content = strip_think_tags(str(row.get("content", "")))
        if len(content) > max_message_chars:
            content = content[:max_message_chars] + "..."
        line = f"{sender}: {content}"
        total += len(line)
        lines.append(line)
        if total > max_chars:
            break

    lines.reverse()
    return "【会话历史】\n" + "\n".join(lines)
