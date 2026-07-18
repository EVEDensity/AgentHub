from __future__ import annotations

import json
from typing import Any


def build_attachment_context(attachments: list[dict[str, Any]] | None) -> tuple[str, list[dict[str, Any]]]:
    if not attachments:
        return "", []

    blocks: list[str] = []
    clean: list[dict[str, Any]] = []
    max_text_len = 12000

    for idx, item in enumerate(attachments, start=1):
        name = str(item.get("name", f"file_{idx}"))
        file_type = str(item.get("type", "text/plain"))
        size = int(item.get("size", 0) or 0)
        content = str(item.get("content", ""))

        is_image = file_type.startswith("image/") or name.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"))
        if is_image:
            preview = content[:180]
            blocks.append(
                f"[附件图片 {idx}] name={name}, type={file_type}, size={size}\n"
                f"data_url_prefix={preview}"
            )
        else:
            trimmed = content[:max_text_len]
            ext = name.split(".")[-1] if "." in name else "text"
            blocks.append(
                f"[附件文件 {idx}] name={name}, type={file_type}, size={size}\n"
                f"```{ext}\n{trimmed}\n```"
            )

        clean.append({"name": name, "type": file_type, "size": size})

    return "\n\n".join(blocks), clean


def build_quote_context(quote_references: list[dict[str, Any]] | None) -> str:
    """Format quoted chat messages for prompt injection."""
    if not quote_references:
        return ""

    blocks: list[str] = []
    for idx, qr in enumerate(quote_references, start=1):
        original_sender = str(qr.get("originalSender", "unknown"))
        original_timestamp = str(qr.get("originalTimestamp", ""))
        quoted_text = str(qr.get("quotedText", ""))
        is_full_message = bool(qr.get("isFullMessage", False))

        truncation_note = ""
        display_text = quoted_text
        if len(quoted_text) > 2000:
            display_text = quoted_text[:2000] + "\n… [已截断]"
            truncation_note = " (已截断)"

        msg_type = "完整消息" if is_full_message else "消息片段"

        blocks.append(
            f"[引自历史消息 {idx}] 发送者: {original_sender}, "
            f"时间: {original_timestamp}, 类型: {msg_type}{truncation_note}\n"
            f"---\n{display_text}\n---"
        )

    return "[用户引用的历史消息]\n\n" + "\n\n".join(blocks)


def format_conversation_for_prompt(conversation: list[dict]) -> str:
    """Format a multi-turn conversation for prompt injection."""
    parts: list[str] = []
    for turn in conversation:
        role = turn.get("role", "")
        if role == "user":
            parts.append(f"【用户消息】\n{turn.get('content', '')}")
        elif role == "assistant" and "tool_calls" in turn:
            for tc in turn["tool_calls"]:
                parts.append(
                    "【工具调用】\n"
                    f"调用工具: {tc.get('name', 'unknown')}\n"
                    f"参数: {json.dumps(tc.get('arguments', {}), ensure_ascii=False)}"
                )
        elif role == "tool":
            from app.services.tool_executor import tool_executor

            parts.append(tool_executor.build_tool_result_context(turn.get("results", [])))
        elif role == "assistant":
            parts.append(f"【助手回复】\n{turn.get('content', '')}")
    return "\n\n".join(parts)


def estimate_token_usage(user_text: str, model_output: str) -> tuple[int, int, int]:
    """Estimate token counts with a simple CJK-aware heuristic."""

    def _is_cjk(ch: str) -> bool:
        codepoint = ord(ch)
        return (
            0x3400 <= codepoint <= 0x4DBF
            or 0x4E00 <= codepoint <= 0x9FFF
            or 0xF900 <= codepoint <= 0xFAFF
            or 0x3040 <= codepoint <= 0x30FF
            or 0xAC00 <= codepoint <= 0xD7AF
        )

    def _count_tokens(text: str) -> int:
        cjk = sum(1 for ch in text if _is_cjk(ch))
        non_cjk = len(text) - cjk
        return max(1, int(cjk / 1.5 + non_cjk / 4))

    prompt_tokens = _count_tokens(user_text)
    completion_tokens = _count_tokens(model_output)
    total_tokens = prompt_tokens + completion_tokens
    return prompt_tokens, completion_tokens, total_tokens
