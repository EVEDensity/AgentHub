from __future__ import annotations

import unittest
from pathlib import Path

from app.services.harness_service import HarnessError, HarnessRequest, SandboxHarness
from app.services.tools.sandbox_executor import SandboxResult


class FakeSandbox:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def execute(self, code: str, **kwargs: object) -> SandboxResult:
        self.calls.append({"code": code, **kwargs})
        return SandboxResult(
            success=True,
            stdout="ok",
            stderr="",
            exit_code=0,
            duration_ms=1,
            mode="fake",
        )


class HarnessServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_sandbox_harness_forwards_request_and_adds_loop_metadata(self) -> None:
        sandbox = FakeSandbox()
        result = await SandboxHarness(sandbox).execute(
            HarnessRequest(
                code="print('ok')",
                language="python",
                timeout=5,
                cwd=Path("workspace"),
            )
        )

        self.assertTrue(result.sandbox.success)
        self.assertEqual(result.iterations, 1)
        self.assertEqual(result.tool_calls, 0)
        self.assertEqual(
            sandbox.calls,
            [{"code": "print('ok')", "language": "python", "timeout": 5, "cwd": "workspace"}],
        )

    async def test_sandbox_harness_rejects_non_positive_timeout(self) -> None:
        with self.assertRaisesRegex(HarnessError, "timeout must be positive"):
            await SandboxHarness(FakeSandbox()).execute(
                HarnessRequest(code="", language="python", timeout=0)
            )
