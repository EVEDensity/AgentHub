from __future__ import annotations

from app.services.token_budget import TokenBudget, count_tokens, fit_prompt, truncate_to_tokens


def test_count_tokens_handles_cjk_and_latin() -> None:
    assert count_tokens("hello world", "unknown", "unknown") > 0
    assert count_tokens("企业多智能体协作平台", "unknown", "unknown") >= 8


def test_truncate_to_tokens_preserves_head_and_tail() -> None:
    text = "HEAD-" + ("中" * 5000) + "-TAIL"
    result, truncated = truncate_to_tokens(text, 200, "unknown", "unknown")
    assert truncated is True
    assert result.startswith("HEAD-")
    assert result.endswith("-TAIL")
    assert count_tokens(result, "unknown", "unknown") <= 200


def test_fit_prompt_applies_model_budget_to_all_prompt_types(monkeypatch) -> None:
    monkeypatch.setenv("AGENTHUB_MAX_PROMPT_TOKENS", "2048")
    prompt = "system rules\n符号消息: " + ("任务上下文" * 4000)
    result, stats = fit_prompt(prompt, "unknown", "custom-model", anchor="符号消息:")
    budget = TokenBudget.for_model("unknown", "custom-model")
    assert stats["truncated"] is True
    assert count_tokens(result, "unknown", "custom-model") <= budget.prompt_limit
    assert result.startswith("system rules")
