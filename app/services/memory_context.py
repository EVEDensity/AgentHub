from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.token_budget import count_tokens, truncate_to_tokens


@dataclass(frozen=True)
class MemoryContextSection:
    name: str
    text: str
    priority: int


def _normalized(text: str) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]+", "", text).lower()


def _features(text: str) -> set[str]:
    normalized = _normalized(text)
    if len(normalized) < 12:
        return {normalized} if normalized else set()
    return {normalized[index:index + 8] for index in range(0, len(normalized) - 7, 4)}


def similarity(left: str, right: str) -> float:
    left_norm, right_norm = _normalized(left), _normalized(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm in right_norm or right_norm in left_norm:
        return min(len(left_norm), len(right_norm)) / max(len(left_norm), len(right_norm))
    left_set, right_set = _features(left), _features(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / max(1, min(len(left_set), len(right_set)))


def deduplicate_text(text: str, references: list[str], threshold: float = 0.82) -> str:
    blocks = [block.strip() for block in re.split(r"\n{2,}", text) if block.strip()]
    kept: list[str] = []
    for block in blocks:
        if any(similarity(block, reference) >= threshold for reference in references if reference):
            continue
        if any(similarity(block, previous) >= threshold for previous in kept):
            continue
        kept.append(block)
    return "\n\n".join(kept)


def build_memory_context(
    sections: list[MemoryContextSection],
    *,
    exclude_texts: list[str] | None = None,
    max_tokens: int = 3_000,
    provider: str = "",
    model: str = "",
) -> tuple[str, dict[str, int | bool]]:
    ordered = sorted(sections, key=lambda section: section.priority)
    references = [text for text in (exclude_texts or []) if text]
    before = sum(count_tokens(section.text, provider, model) for section in ordered)
    output: list[str] = []
    truncated = False

    for section in ordered:
        unique = deduplicate_text(section.text, references)
        if not unique:
            continue
        rendered = f"[{section.name}]\n{unique}"
        remaining = max_tokens - count_tokens("\n\n".join(output), provider, model)
        if remaining <= 8:
            truncated = True
            break
        rendered, was_truncated = truncate_to_tokens(
            rendered, remaining, provider, model, preserve_tail=0.65,
        )
        truncated = truncated or was_truncated
        output.append(rendered)
        references.append(unique)

    result = "\n\n".join(output)
    after = count_tokens(result, provider, model)
    return result, {
        "tokens_before": before,
        "tokens_after": after,
        "truncated": truncated,
    }
