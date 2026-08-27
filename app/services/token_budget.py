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


# Per-provider tokens-per-wide-CJK-char calibration, measured against real
# native tokenizers via benchmarks/calibrate_cn_estimator.py (R4). Re-derive
# these constants from fresh assets whenever a family's vocab changes — never
# hand-edit the numbers. Providers absent from this table keep the generic
# conservative 0.9 fallback below.
CN_TOKEN_RATIOS: dict[str, float] = {
    "qwen": 0.61,
    "deepseek": 0.56,
}

# Wide-CJK class: han characters + CJK punctuation (\u3000-\u303F included in
# the 2E80-9FFF band) + fullwidth forms. Native BPE emits these at a similar
# per-family rate, and folding punctuation into the calibrated bucket removes
# the systematic under-count that (non_cjk // 4) produced for fullwidth marks.
_WIDE_CJK_RE = re.compile(r"[\u2E80-\u9FFF\uF900-\uFAFF\uFF00-\uFFEF]")


def estimate_tokens_multilingual(text: str, provider: str = "") -> int:
    """Multilingual estimator: CJK-aware fallback for providers without a
    native tokenizer.

    Wide-CJK compression is family-specific (Qwen BPE ≈0.65 tokens/char,
    DeepSeek V3 ≈0.59 measured in R4; see CN_TOKEN_RATIOS). Remaining ASCII
    text runs ~4 chars/token. Providers with a calibrated constant use it;
    everything else keeps the deliberately conservative 0.9 so budget
    enforcement never under-counts an unknown family.
    """
    if not text:
        return 0
    wide = len(_WIDE_CJK_RE.findall(text))
    ratio = CN_TOKEN_RATIOS.get((provider or "").lower(), 0.9)
    ascii_rest = len(text) - wide
    return max(1, int(wide * ratio) + (ascii_rest + 3) // 4)


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
    return estimate_tokens_multilingual(text, provider_key)


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
    image_count: int = 0,
) -> tuple[str, dict[str, int | bool]]:
    """Fit the text prompt plus inline-image cost into the model's budget.

    Images ride along the same request window, so each one is charged the
    conservative flat :data:`IMAGE_TOKEN_COST` against the prompt limit
    (MM-3 / ADR-0105): the text portion is truncated to whatever room the
    images leave, never the other way around. ``image_count`` beyond the
    turn cap is clamped by callers assembling parts — this function just
    bills what it is told.
    """
    from app.services.tools.multimodal.content_parts import IMAGE_TOKEN_COST

    billed_images = max(0, int(image_count))
    image_tokens = billed_images * IMAGE_TOKEN_COST
    budget = TokenBudget.for_model(provider, model, output_reserve=output_reserve)
    # Reserve the image share first; text keeps at least a floor so the
    # degrade path stays meaningful even under extreme image counts.
    text_limit = max(256, budget.prompt_limit - image_tokens)

    before_text = count_tokens(prompt, provider, model)
    before_total = before_text + image_tokens
    stats: dict[str, int | bool] = {
        "tokens_before": before_total,
        "tokens_after": before_total,
        "truncated": False,
        "image_tokens": image_tokens,
        "images": billed_images,
    }
    if before_text <= text_limit:
        return prompt, stats

    def _count(text: str) -> int:
        return count_tokens(text, provider, model)

    if anchor and anchor in prompt:
        index = prompt.rfind(anchor) + len(anchor)
        prefix, dynamic = prompt[:index], prompt[index:]
        if _count(prefix) >= text_limit - 256:
            prefix, _ = truncate_to_tokens(
                prefix, text_limit - 256, provider, model, preserve_tail=0.25,
            )
        dynamic_budget = max(256, text_limit - _count(prefix))
        dynamic, _ = truncate_to_tokens(dynamic, dynamic_budget, provider, model)
        result = prefix + dynamic
    else:
        result, _ = truncate_to_tokens(prompt, text_limit, provider, model)

    after_text = _count(result)
    stats["tokens_after"] = after_text + image_tokens
    stats["truncated"] = True
    return result, stats


def total_tokens(parts: Iterable[str], provider: str = "", model: str = "") -> int:
    return sum(count_tokens(part, provider, model) for part in parts)
