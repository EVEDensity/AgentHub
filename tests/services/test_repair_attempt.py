from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

from app.cli.main import _doctor_report
from app.services.harness_service import (
    HarnessResult,
    RepairAttemptRunner,
    RepairAttemptState,
    RepairErrorType,
    classify_repair_error,
)
from app.services.tools.sandbox_executor import SandboxResult


class RepairAttemptTests(unittest.TestCase):
    def _failed(self, message: str = "syntax error") -> HarnessResult:
        return HarnessResult(
            sandbox=SandboxResult(False, "", message, 1, 1, "subprocess", message)
        )

    def test_error_matrix_and_independent_verifier_rollback(self) -> None:
        self.assertEqual(classify_repair_error("ModuleNotFoundError: missing dependency"), RepairErrorType.DEPENDENCY)
        calls: list[str] = []

        async def planner(_attempt, _result):
            return "replace invalid syntax"

        async def applier(_attempt):
            calls.append("apply")
            return self._failed("still failing")

        async def verifier(_result):
            return False

        async def rollback(_attempt):
            calls.append("rollback")

        async def run() -> None:
            attempt, result = await RepairAttemptRunner(
                planner, applier, verifier, rollback, token_budget=100
            ).run(self._failed(), "SyntaxError: api_key=secret")
            self.assertIs(attempt.state, RepairAttemptState.ROLLED_BACK)
            self.assertFalse(result.sandbox.success)
            self.assertEqual(calls, ["apply", "rollback"])
            self.assertNotIn("secret", attempt.root_cause)

        asyncio.run(run())

    def test_token_budget_fails_closed_before_side_effect(self) -> None:
        calls: list[str] = []

        async def planner(_attempt, _result):
            return "x" * 100

        async def applier(_attempt):
            calls.append("apply")
            return self._failed()

        async def verifier(_result):
            return True

        async def run() -> None:
            attempt, _ = await RepairAttemptRunner(
                planner, applier, verifier, token_budget=1
            ).run(self._failed(), "syntax error")
            self.assertIs(attempt.state, RepairAttemptState.BUDGET_EXHAUSTED)
            self.assertEqual(calls, [])

        asyncio.run(run())


class DoctorReportTests(unittest.TestCase):
    def test_report_is_structured_and_does_not_include_secret_values(self) -> None:
        report = _doctor_report(Path.cwd())
        self.assertIn(report["status"], {"ok", "degraded"})
        self.assertIn("checks", report)
        serialized = str(report)
        self.assertNotIn("AGENTHUB_CLI_MODEL_API_KEY=", serialized)

