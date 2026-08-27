"""Three-tier tool-call-loop circuit breaker.

Extracted from ``tooling.py`` so the loop stays under the R4-4 complexity
gate — this module owns all failure-circuit state and detection logic:

* **Tier 1** (per-tool): same tool fails with the SAME error key for ≥3
  consecutive rounds → dead loop; the agent keeps retrying a tool that can
  never succeed (the most common infinite-loop pattern).
* **Tier 2** (round-level): every tool in a round fails with missing-required
  params for ≥2 consecutive rounds → the model misunderstands the schemas.
* **Tier 3** (round-level): every tool in a round fails for ≥3 consecutive
  rounds → catch-all systemic failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("agenthub.tooling.breaker")

TIER1_ROUNDS = 3
TIER2_ROUNDS = 2
TIER3_ROUNDS = 3


@dataclass
class BreakerEvent:
    """Details attached to a ``circuit_breaker`` tool event."""

    tier: str
    tools: list[dict] = field(default_factory=list)


@dataclass
class ToolLoopCircuitBreaker:
    """Mutable state + detection for one ``_run_tool_call_loop`` invocation."""

    _failure_history: dict[str, dict] = field(default_factory=dict)
    _missing_param_rounds: int = 0
    _all_error_rounds: int = 0

    def reset_round_counters(self) -> None:
        self._missing_param_rounds = 0
        self._all_error_rounds = 0

    def assess(self, tool_results: list[dict], iteration: int) -> BreakerEvent | None:
        """Record one round of results; return an event when a tier trips."""
        all_failed = bool(tool_results) and all(not r.get("success") for r in tool_results)
        missing_param_errors = [
            r for r in tool_results
            if not r.get("success") and r.get("missing_params")
        ]

        # ── Tier 1: per-tool same-error streak ───────────────────────────
        tier1_tools: list[str] = []
        for tr in tool_results:
            tool_name = tr.get("tool_name", "")
            if tr.get("success"):
                self._failure_history.pop(tool_name, None)
                continue
            if not tool_name:
                continue
            error_key = (
                tr.get("error", "")
                or tr.get("missing_params", "")
                or str(tr)
            )[:80]  # first 80 chars fingerprint the failure
            prev = self._failure_history.get(tool_name)
            if prev and prev["error_key"] == error_key:
                prev["consecutive_rounds"] += 1
            else:
                self._failure_history[tool_name] = {
                    "error_key": error_key,
                    "consecutive_rounds": 1,
                }
            rounds = self._failure_history[tool_name]["consecutive_rounds"]
            if rounds >= TIER1_ROUNDS:
                logger.warning(
                    "tool_loop iter=%d: circuit breaker TIER1 — tool '%s' "
                    "failed with same error for %d consecutive rounds",
                    iteration, tool_name, rounds,
                )
                tier1_tools.append(tool_name)
        if tier1_tools:
            return BreakerEvent(tier="tier1", tools=[
                {"tier": "tier1", "tool_name": tn,
                 "rounds": self._failure_history.get(tn, {}).get("consecutive_rounds", 0)}
                for tn in sorted(set(tier1_tools))
            ])

        # ── Tier 2: all-missing-required-params streak ───────────────────
        if missing_param_errors:
            self._missing_param_rounds += 1
            logger.warning(
                "tool_loop iter=%d: %d/%d tools missing required params "
                "(consecutive_missing_rounds=%d)",
                iteration, len(missing_param_errors), len(tool_results),
                self._missing_param_rounds,
            )
            if self._missing_param_rounds >= TIER2_ROUNDS:
                return BreakerEvent(tier="tier2")
        elif all_failed:
            # ── Tier 3: catch-all all-tools-failed ────────────────────────
            self._all_error_rounds += 1
            if self._all_error_rounds >= TIER3_ROUNDS:
                return BreakerEvent(tier="tier3")
        else:
            self.reset_round_counters()
        return None
