from __future__ import annotations

import inspect
import logging
import re
import time

from app.services.harness_types import (
    HarnessResult,
    RepairApplier,
    RepairAttempt,
    RepairAttemptState,
    RepairErrorType,
    RepairPlanner,
    RepairRollback,
    Verifier,
)

logger=logging.getLogger("agenthub.harness.repair")

_SECRET_PATTERN = re.compile(r"(?i)(api[_-]?key|token|authorization|bearer)\s*[:=]\s*[^\s,;]+")

def classify_repair_error(error: object) -> RepairErrorType:
    """Classify a tool/verifier failure without exposing raw error text."""
    text = str(error).lower()
    if any(token in text for token in ("syntaxerror", "syntax error", "parse error")):
        return RepairErrorType.SYNTAX
    if any(token in text for token in ("modulenotfound", "no module named", "dependency", "importerror")):
        return RepairErrorType.DEPENDENCY
    if any(token in text for token in ("assertionerror", "test failed", "pytest", "test_failure")):
        return RepairErrorType.TEST_FAILURE
    if any(token in text for token in ("permission denied", "access denied", "permission")):
        return RepairErrorType.PERMISSION
    if any(token in text for token in ("timed out", "timeout", "deadline")):
        return RepairErrorType.TIMEOUT
    if any(token in text for token in ("workspace conflict", "sha256", "concurrent modification", "conflict")):
        return RepairErrorType.WORKSPACE_CONFLICT
    return RepairErrorType.UNKNOWN

def _safe_repair_message(error: object) -> str:
    """Keep repair state useful while removing common credential formats."""
    value = _SECRET_PATTERN.sub(r"\1=<redacted>", str(error))
    return value[:500]

class RepairAttemptRunner:
    """Execute at most one bounded repair transaction and verify it independently.

    The planner and applier are injected so this class never invents model
    prompts or bypasses the Harness tool-permission boundary. A failed
    verification invokes rollback when supplied; no automatic second attempt
    is performed by this runner.
    """

    def __init__(
        self,
        planner: RepairPlanner,
        applier: RepairApplier,
        verifier: Verifier,
        rollback: RepairRollback | None = None,
        *,
        max_attempts: int = 1,
        token_budget: int = 1024,
        time_budget_seconds: float = 60.0,
        side_effect_budget: int = 1,
    ) -> None:
        if max_attempts < 1 or token_budget < 1 or time_budget_seconds <= 0 or side_effect_budget < 1:
            raise ValueError("repair budgets must be positive")
        self._planner = planner
        self._applier = applier
        self._verifier = verifier
        self._rollback = rollback
        self._max_attempts = max_attempts
        self._token_budget = token_budget
        self._time_budget = time_budget_seconds
        self._side_effect_budget = side_effect_budget

    async def run(
        self,
        failed_result: HarnessResult,
        error: object,
        *,
        attempt: int = 1,
    ) -> tuple[RepairAttempt, HarnessResult]:
        started = time.monotonic()
        error_type = classify_repair_error(error)
        current = RepairAttempt(
            attempt=attempt,
            error_type=error_type,
            token_budget=self._token_budget,
            time_budget_seconds=self._time_budget,
            side_effect_budget=self._side_effect_budget,
            root_cause=_safe_repair_message(error),
        ).transition(RepairAttemptState.ROOT_CAUSE_EXTRACTED)
        if current.attempt > self._max_attempts:
            return current.transition(RepairAttemptState.BUDGET_EXHAUSTED, message="repair attempt limit exhausted"), failed_result
        try:
            plan_value = self._planner(current, failed_result)
            plan = await plan_value if inspect.isawaitable(plan_value) else plan_value
            if not plan:
                return current.transition(RepairAttemptState.FAILED, message="no repair plan produced"), failed_result
            plan_text = str(plan)[:2000]
            estimated_tokens = max(1, len(plan_text) // 4)
            current = current.transition(RepairAttemptState.PLAN_READY, plan=plan_text, tokens_used=estimated_tokens)
            if current.budget_exhausted:
                return current.transition(RepairAttemptState.BUDGET_EXHAUSTED), failed_result
            current = current.transition(RepairAttemptState.APPLYING, side_effects_used=1)
            repaired = self._applier(current)
            repaired_result = await repaired if inspect.isawaitable(repaired) else repaired
            current = current.transition(RepairAttemptState.VERIFYING, elapsed_seconds=time.monotonic() - started)
            verified = self._verifier(repaired_result)
            ok = await verified if inspect.isawaitable(verified) else bool(verified)
            elapsed = time.monotonic() - started
            if elapsed > self._time_budget:
                raise TimeoutError("repair time budget exhausted")
            if ok:
                return current.transition(RepairAttemptState.SUCCEEDED, elapsed_seconds=elapsed), repaired_result
            await self._rollback_safely(current)
            return current.transition(RepairAttemptState.ROLLED_BACK, elapsed_seconds=elapsed, message="independent verifier rejected repair"), failed_result
        except TimeoutError:
            await self._rollback_safely(current)
            return current.transition(RepairAttemptState.BUDGET_EXHAUSTED, elapsed_seconds=time.monotonic() - started, message="repair time budget exhausted"), failed_result
        except Exception as exc:  # noqa: BLE001 - repair fails closed
            await self._rollback_safely(current)
            return current.transition(RepairAttemptState.FAILED, elapsed_seconds=time.monotonic() - started, message=f"repair failed: {type(exc).__name__}"), failed_result

    async def _rollback_safely(self, attempt: RepairAttempt) -> None:
        if self._rollback is None:
            return
        try:
            rollback = self._rollback(attempt)
            if inspect.isawaitable(rollback):
                await rollback
        except Exception:
            logger.exception("repair rollback failed")

    async def run_loop(self, failed_result: HarnessResult, error: object) -> tuple[RepairAttempt, HarnessResult]:
        """Retry failed repairs up to ``max_attempts`` with rollback between tries."""
        latest_attempt: RepairAttempt | None = None
        result = failed_result
        for number in range(1, self._max_attempts + 1):
            latest_attempt, result = await self.run(result, error, attempt=number)
            if latest_attempt.state is RepairAttemptState.SUCCEEDED:
                return latest_attempt, result
            if latest_attempt.state in {RepairAttemptState.BUDGET_EXHAUSTED, RepairAttemptState.FAILED}:
                return latest_attempt, result
        assert latest_attempt is not None
        return latest_attempt.transition(RepairAttemptState.BUDGET_EXHAUSTED, message="repair attempt limit exhausted"), result
