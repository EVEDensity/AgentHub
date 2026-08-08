from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.services.tools.sandbox_executor import SandboxResult


class HarnessError(RuntimeError):
    """Raised when a Harness cannot execute a bounded WorkUnit request."""


@dataclass(frozen=True)
class HarnessRequest:
    """Request-scoped input for one bounded Harness execution."""

    code: str
    language: str
    timeout: float
    cwd: Path | None = None


@dataclass(frozen=True)
class HarnessResult:
    """Execution output plus loop metadata owned by the Harness."""

    sandbox: SandboxResult
    iterations: int = 1
    tool_calls: int = 0


class SandboxPort(Protocol):
    async def execute(
        self,
        code: str,
        language: str = "python",
        timeout: float = 30.0,
        cwd: str | None = None,
    ) -> SandboxResult: ...


class HarnessPort(Protocol):
    """Replaceable model/tool loop boundary used by Runner."""

    async def execute(self, request: HarnessRequest) -> HarnessResult: ...


class SandboxHarness:
    """Minimal Harness implementation backed by the isolated Sandbox port.

    It deliberately performs one bounded execution. Model calls, function
    calling, tools, checkpoints, and retries can be added behind this contract
    without giving Runner a second execution state machine.
    """

    def __init__(self, sandbox: SandboxPort) -> None:
        self._sandbox = sandbox

    async def execute(self, request: HarnessRequest) -> HarnessResult:
        if request.timeout <= 0:
            raise HarnessError("Harness timeout must be positive")
        result = await self._sandbox.execute(
            request.code,
            language=request.language,
            timeout=request.timeout,
            cwd=str(request.cwd) if request.cwd is not None else None,
        )
        return HarnessResult(sandbox=result)


__all__ = [
    "HarnessError",
    "HarnessPort",
    "HarnessRequest",
    "HarnessResult",
    "SandboxHarness",
    "SandboxPort",
]
