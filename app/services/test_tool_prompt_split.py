from __future__ import annotations

from app.services.prompt_messages import split_prompt_for_adapter


def test_static_prefix_is_not_duplicated_in_user_prompt() -> None:
    full = "STATIC-SYSTEM\nmemory and tools\n符号消息: dynamic request"
    system_prompt, user_prompt = split_prompt_for_adapter(full)
    assert system_prompt == "STATIC-SYSTEM\nmemory and tools\n"
    assert user_prompt == "符号消息: dynamic request"
    assert "STATIC-SYSTEM" not in user_prompt
    assert system_prompt + user_prompt == full


def test_unsplittable_prompt_remains_user_only() -> None:
    system_prompt, user_prompt = split_prompt_for_adapter("plain request")
    assert system_prompt == ""
    assert user_prompt == "plain request"
