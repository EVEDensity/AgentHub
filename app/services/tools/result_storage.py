from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("agenthub.tools.result_storage")


@dataclass
class ContentBudgetConfig:
    """Configuration for tool result content budget.

    Modeled on FUNCTION_CALLING_IMPLEMENTATION.md §9.1:
    `processToolResultBlock` and `applyContentBudget`.
    """
    max_result_chars: int = 10_000        # Max chars per individual tool result
    max_total_results_chars: int = 30_000  # Total char budget per conversation turn
    truncation_marker: str = "\n... [结果已截断，超出上下文预算]"


class ResultStorage:
    """Truncates and budgets tool results for LLM context injection.

    Prevents context overflow from large tool results (e.g. reading
    a 500KB log file via file_read).

    The budget resets at the start of each tool-call loop iteration
    (i.e., each user message) and accumulates across parallel tool
    executions within a single turn.
    """

    def __init__(self, config: ContentBudgetConfig | None = None) -> None:
        self.config = config or ContentBudgetConfig()
        self._used_budget: int = 0

    # ── Public API ────────────────────────────────────────────────────

    def process(self, result: dict[str, Any]) -> dict[str, Any]:
        """Apply content budget to a single tool result.

        Returns a (possibly truncated) shallow copy of the result dict.
        The ``result`` and ``error`` fields are truncated if they exceed
        the budget.

        Truncation priority:
          1. If total budget exceeded → drop result body, keep error if present
          2. If individual result too large → truncate to max_result_chars
          3. If this result would exceed total budget → truncate to remaining
        """
        if not result:
            return result

        # Shallow copy so we don't mutate the original
        processed = dict(result)

        # ── Truncate the result body ─────────────────────────────────
        result_data = processed.get("result")
        if isinstance(result_data, str) and len(result_data) > self.config.max_result_chars:
            processed["result"] = (
                result_data[:self.config.max_result_chars]
                + self.config.truncation_marker
            )
            processed["result_truncated"] = True
            logger.debug(
                "result_storage: truncated '%s' result from %d → %d chars",
                processed.get("tool_name", "?"), len(result_data),
                self.config.max_result_chars,
            )

        if isinstance(result_data, dict) or isinstance(result_data, list):
            serialized = json.dumps(result_data, ensure_ascii=False)
            new_chars = len(serialized)
            if new_chars > self.config.max_result_chars:
                processed["result"] = {
                    "truncated": True,
                    "preview": serialized[:self.config.max_result_chars],
                    "original_size": len(serialized),
                }
                processed["result_truncated"] = True

        # ── Check total budget ────────────────────────────────────────
        result_text = self._extract_text(processed)
        new_chars = len(result_text)

        if self._used_budget + new_chars > self.config.max_total_results_chars:
            remaining = self.config.max_total_results_chars - self._used_budget
            if remaining <= 0:
                # Budget exhausted — drop result body entirely
                processed["result"] = self.config.truncation_marker
                processed["result_truncated"] = True
            else:
                # Truncate to remaining budget
                processed["result"] = result_text[:remaining] + self.config.truncation_marker
                processed["result_truncated"] = True
            logger.debug(
                "result_storage: budget exhausted for '%s' (used=%d/%d)",
                processed.get("tool_name", "?"),
                self._used_budget, self.config.max_total_results_chars,
            )

        # ── Track budget ──────────────────────────────────────────────
        final_text = self._extract_text(processed)
        self._used_budget += len(final_text)

        return processed

    def reset_budget(self) -> None:
        """Reset budget counter for a new conversation turn.

        Should be called at the start of each ``_run_tool_call_loop``
        invocation.
        """
        self._used_budget = 0

    @property
    def used_budget(self) -> int:
        """Current accumulated result character count."""
        return self._used_budget

    @property
    def remaining_budget(self) -> int:
        """Remaining character budget for this turn."""
        return max(0, self.config.max_total_results_chars - self._used_budget)

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_text(result: dict[str, Any]) -> str:
        """Extract the text content of a result for budget accounting."""
        result_data = result.get("result", "")
        if isinstance(result_data, str):
            return result_data
        if result_data is not None:
            return json.dumps(result_data, ensure_ascii=False)
        error_text = result.get("error", "")
        if isinstance(error_text, str):
            return error_text
        return ""
