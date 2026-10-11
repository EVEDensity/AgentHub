"""Deterministic Harness feedback limits reconstructed from saved tool results."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from app.services.model_contract import ToolResult


@dataclass(frozen=True, slots=True)
class ToolFeedbackPolicy:
    max_result_chars: int = 10_000
    max_total_results_chars: int = 30_000
    truncation_marker: str = "\n... [结果已截断，超出上下文预算]"

    def __post_init__(self) -> None:
        values = (self.max_result_chars, self.max_total_results_chars)
        if any(type(value) is not int or value < 1 for value in values):
            raise ValueError("tool feedback limits must be positive integers")
        if not isinstance(self.truncation_marker, str):
            raise TypeError("tool feedback truncation marker must be text")

    @classmethod
    def from_config(cls, config: Any = None) -> ToolFeedbackPolicy:
        """Snapshot only the three visible-text settings, never processor state."""
        defaults = cls()
        names = ("max_result_chars", "max_total_results_chars", "truncation_marker")
        return cls(**{name: getattr(config, name, getattr(defaults, name)) for name in names})


def _feedback_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)


def apply_tool_feedback(
    result: ToolResult,
    prior_results: Sequence[ToolResult],
    policy: ToolFeedbackPolicy,
) -> ToolResult:
    """Limit one result using only the already-visible result prefix and policy.

    Structured content is serialized before limiting, in the same order as the
    canonical Harness adapter. Error feedback consumes context too. As in the
    existing result processor, the explanatory marker sits beyond the text
    allowance and contributes to the prefix charged for the following call.
    """
    content = _feedback_text(result.content)
    if len(content) > policy.max_result_chars:
        content = content[:policy.max_result_chars] + policy.truncation_marker
    used = sum(len(_feedback_text(previous.content)) for previous in prior_results)
    remaining = policy.max_total_results_chars - used
    if len(content) > remaining:
        content = content[:max(0, remaining)] + policy.truncation_marker
    return replace(result, content=content)


__all__ = ["ToolFeedbackPolicy", "apply_tool_feedback"]
