from __future__ import annotations

from typing import Any

from app.services.context_compaction import compact_text
from app.services.text_processing import strip_think_tags


def build_conversation_history_transcript(
    rows: list[dict[str, Any]],
    *,
    max_chars: int = 4200,
    max_messages: int = 12,
    max_message_chars: int = 420,
) -> str:
    if not rows:
        return ""

    lines: list[str] = []
    total = 0
    last_line = ""
    for row in rows[:max_messages]:
        sender = compact_text(str(row.get("sender", "unknown")), max_chars=32)
        content = compact_text(strip_think_tags(str(row.get("content", ""))), max_chars=max_message_chars)
        if not sender or not content:
            continue
        line = f"{sender}: {content}"
        if line == last_line:
            continue
        total += len(line)
        lines.append(line)
        last_line = line
        if total > max_chars:
            break

    lines.reverse()
    if not lines:
        return ""
    return "【会话历史】\n" + "\n".join(lines)
