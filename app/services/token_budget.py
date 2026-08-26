from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_MODEL_WINDOWS: tuple[tuple[str, int], ...] = (
    ("gpt-4.1", 1_047_576),
    ("gpt-4o", 128_000),
    ("gpt-5", 400_000),
    ("o1", 200_000),
    ("o3", 200_000),
    ("claude", 200_000),
    ("deepseek", 64_000),
    ("qwen", 128_000),
    ("doubao", 128_000),
    ("glm", 128_000),
    ("kimi", 128_000),
)

_REGISTERED_TOKENIZERS: dict[str, Callable[[str], int]] = {}


def register_model_tokenizer(
    provider: str,
    counter: Callable[[str], int],
    model: str = "",
) -> None:
    key = f"{provider.lower()}:{model.lower()}" if model else provider.lower()
    _REGISTERED_TOKENIZERS[key] = counter


def unregister_model_tokenizer(provider: str, model: str = "") -> None:
    key = f"{provider.lower()}:{model.lower()}" if model else provider.lower()
    _REGISTERED_TOKENIZERS.pop(key, None)


@lru_cache(maxsize=64)
def _local_provider_tokenizer(provider: str, model: str):
    env_provider = re.sub(r"[^A-Z0-9]+", "_", provider.upper()).strip("_")
    configured = os.getenv(f"AGENTHUB_TOKENIZER_{env_provider}_PATH", "").strip()
    if not configured:
        return None
    path = Path(configured)
    tokenizer_file = path / "tokenizer.json" if path.is_dir() else path
    if tokenizer_file.is_file():
        try:
            from tokenizers import Tokenizer

            return Tokenizer.from_file(str(tokenizer_file))
        except (ImportError, OSError, ValueError):
            return None
    # HuggingFace model directory (config.json present, no tokenizer.json):
    # load the fast tokenizer when transformers is available. Optional dep —
    # without it we keep the multilingual estimator.
    if (path / "config.json").is_file():
        try:
            from transformers import AutoTokenizer

            hf = AutoTokenizer.from_pretrained(str(path), local_files_only=True)
            if hf is not None and callable(hf):
                return hf
        except (ImportError, OSError, ValueError, TypeError):
            return None
    return None


@lru_cache(maxsize=64)
def _tiktoken_encoder(model: str):
    try:
        import tiktoken

        try:
            return tiktoken.encoding_for_model(model)
        except KeyError:
            return tiktoken.get_encoding("o200k_base")
    except (ImportError, ValueError):
        return None


def estimate_tokens_multilingual(text: str) -> int:
    """Multilingual estimator: CJK-aware fallback for providers without a
    native tokenizer.

    CJK characters average ~0.9 tokens/char on modern multilingual BPE
    (o200k/Qwen-style); latin ~4 chars/token. Keeping the estimate slightly
    above reality remains conservative for budget enforcement. Exposed so the
    CI tokenizer-precision gate can measure estimator error against a real
    reference tokenizer.
    """
    if not text:
        return 0
    cjk = len(re.findall(r"[\u3400-\u9fff\uf900-\ufaff]", text))
    non_cjk = len(text) - cjk
    return max(1, int(cjk * 0.9) + (non_cjk + 3) // 4)


def count_tokens(text: str, provider: str = "", model: str = "") -> int:
    """Count model input tokens, using the provider tokenizer when available.

    OpenAI-compatible models use tiktoken. Other providers fall back to a
    multilingual estimator until their native tokenizer is installed. The
    fallback counts CJK characters individually and groups latin text, which
    is deliberately conservative for budget enforcement.
    """
    if not text:
        return 0
    provider_key = provider.lower()
    model_key = model.lower()
    custom = _REGISTERED_TOKENIZERS.get(f"{provider_key}:{model_key}") or _REGISTERED_TOKENIZERS.get(provider_key)
    if custom is not None:
        return max(1, int(custom(text)))
    local_tokenizer = _local_provider_tokenizer(provider_key, model_key)
    if local_tokenizer is not None:
        encoded = local_tokenizer.encode(text)
        if isinstance(encoded, list):
            return max(1, len(encoded))
        try:
            return max(1, len(encoded.ids))
        except AttributeError:
            return max(1, len(str(text)) // 2 + 1)
    if provider_key in {"openai", "azure_openai"} or model_key.startswith(("gpt-", "o1", "o3")):
        encoder = _tiktoken_encoder(model or "gpt-4o")
        if encoder is not None:
            return len(encoder.encode(text, disallowed_special=()))
    return estimate_tokens_multilingual(text)


def tokenizer_backend(provider: str = "", model: str = "") -> str:
    provider_key, model_key = provider.lower(), model.lower()
    if f"{provider_key}:{model_key}" in _REGISTERED_TOKENIZERS or provider_key in _REGISTERED_TOKENIZERS:
        return "registered-native"
    if _local_provider_tokenizer(provider_key, model_key) is not None:
        return "local-tokenizer-json"
    if provider_key in {"openai", "azure_openai"} or model_key.startswith(("gpt-", "o1", "o3")):
        return "tiktoken" if _tiktoken_encoder(model or "gpt-4o") is not None else "multilingual-estimator"
    return "multilingual-estimator"


def model_context_window(provider: str = "", model: str = "") -> int:
    override = os.getenv("AGENTHUB_MODEL_CONTEXT_TOKENS", "").strip()
    if override.isdigit():
        return max(4_096, int(override))
    key = f"{provider}/{model}".lower()
    for fragment, window in _MODEL_WINDOWS:
        if fragment in key:
            return window
    return 32_768


@dataclass(frozen=True)
class TokenBudget:
    provider: str
    model: str
    context_window: int
    output_reserve: int
    prompt_limit: int

    @classmethod
    def for_model(
        cls,
        provider: str = "",
        model: str = "",
        *,
        output_reserve: int = 4_096,
    ) -> TokenBudget:
        context_window = model_context_window(provider, model)
        configured_cap = int(os.getenv("AGENTHUB_MAX_PROMPT_TOKENS", "20000"))
        prompt_limit = max(2_048, min(configured_cap, context_window - output_reserve))
        return cls(provider, model, context_window, output_reserve, prompt_limit)

    def section_limit(self, section: str) -> int:
        shares = {
            "history": 0.18,
            "memory": 0.14,
            "preprocess": 0.08,
            "collaboration": 0.10,
            "tools": 0.20,
            "user": 0.30,
        }
        return max(256, int(self.prompt_limit * shares.get(section, 0.10)))


def cognitive_memory_budgets(
    total_tokens: int,
    query: str,
    domain: str = "",
) -> dict[str, int]:
    """Allocate one context pool across the four cognitive memory classes."""
    text = f"{domain} {query}".lower()
    if any(term in text for term in ("research", "分析", "调研", "知识", "比较", "search")):
        shares = {"working": 0.25, "episodic": 0.15, "semantic": 0.50, "procedural": 0.10}
    elif any(term in text for term in ("code", "实现", "修复", "部署", "workflow", "dag", "工具", "sop")):
        shares = {"working": 0.30, "episodic": 0.20, "semantic": 0.15, "procedural": 0.35}
    elif any(term in text for term in ("plan", "规划", "方案", "架构", "复盘")):
        shares = {"working": 0.25, "episodic": 0.30, "semantic": 0.20, "procedural": 0.25}
    else:
        shares = {"working": 0.40, "episodic": 0.30, "semantic": 0.20, "procedural": 0.10}

    total = max(1024, total_tokens)
    budgets = {name: max(128, int(total * share)) for name, share in shares.items()}
    difference = total - sum(budgets.values())
    budgets["working"] += difference
    return budgets


def truncate_to_tokens(
    text: str,
    max_tokens: int,
    provider: str = "",
    model: str = "",
    *,
    preserve_tail: float = 0.75,
    marker: str = "\n... [context truncated] ...\n",
) -> tuple[str, bool]:
    if max_tokens <= 0:
        return "", bool(text)
    if count_tokens(text, provider, model) <= max_tokens:
        return text, False

    # Binary search character length because all tokenizer implementations are
    # monotonic with respect to a prefix/suffix slice.
    low, high = 0, len(text)
    marker_tokens = count_tokens(marker, provider, model)
    target = max(1, max_tokens - marker_tokens)
    while low < high:
        mid = (low + high + 1) // 2
        head_len = int(mid * (1.0 - preserve_tail))
        candidate = text[:head_len] + text[-(mid - head_len):]
        if count_tokens(candidate, provider, model) <= target:
            low = mid
        else:
            high = mid - 1
    head_len = int(low * (1.0 - preserve_tail))
    tail_len = low - head_len
    compacted = text[:head_len] + marker + (text[-tail_len:] if tail_len else "")
    return compacted, True


def fit_prompt(
    prompt: str,
    provider: str = "",
    model: str = "",
    *,
    output_reserve: int = 4_096,
    anchor: str = "",
) -> tuple[str, dict[str, int | bool]]:
    budget = TokenBudget.for_model(provider, model, output_reserve=output_reserve)
    before = count_tokens(prompt, provider, model)
    if before <= budget.prompt_limit:
        return prompt, {"tokens_before": before, "tokens_after": before, "truncated": False}

    if anchor and anchor in prompt:
        index = prompt.rfind(anchor) + len(anchor)
        prefix, dynamic = prompt[:index], prompt[index:]
        prefix_tokens = count_tokens(prefix, provider, model)
        if prefix_tokens >= budget.prompt_limit - 256:
            prefix, _ = truncate_to_tokens(
                prefix,
                budget.prompt_limit - 256,
                provider,
                model,
                preserve_tail=0.25,
            )
        dynamic_budget = max(256, budget.prompt_limit - count_tokens(prefix, provider, model))
        dynamic, _ = truncate_to_tokens(dynamic, dynamic_budget, provider, model)
        result = prefix + dynamic
    else:
        result, _ = truncate_to_tokens(prompt, budget.prompt_limit, provider, model)

    after = count_tokens(result, provider, model)
    return result, {"tokens_before": before, "tokens_after": after, "truncated": True}


def total_tokens(parts: Iterable[str], provider: str = "", model: str = "") -> int:
    return sum(count_tokens(part, provider, model) for part in parts)
