from __future__ import annotations


def split_prompt_for_adapter(
    prompt: str,
    anchor: str = "符号消息:",
) -> tuple[str, str]:
    """Split a composed prompt into non-overlapping system and user messages."""
    split_idx = prompt.rfind(anchor)
    if split_idx <= 0:
        return "", prompt
    return prompt[:split_idx], prompt[split_idx:]
