"""Operator-only Native tool loop over one fresh disposable OpenSandbox.

The container and execd are an experimental trusted-code execution boundary.
Command observations and artifact integrity do not establish test correctness,
hostile-workload protection or an independent trusted-test verdict.
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import replace

from app.services.harness_checkpoint import HarnessExecutionContext
from app.services.harness_types import (
    FunctionTool,
    HarnessError,
    HarnessPort,
    HarnessRequest,
    HarnessResult,
)
from app.services.opensandbox_backend import OpenSandboxBackend
from app.services.opensandbox_harness_tools import (
    SandboxAttempt,
    SandboxHarnessLimits,
    bounded_drain,
    consume,
)
from app.services.opensandbox_observations import observed_result

__all__ = ["SandboxHarnessLimits", "SandboxNativeHarness"]


class SandboxNativeHarness:
    def __init__(
        self,
        *,
        execution: HarnessExecutionContext,
        backend: OpenSandboxBackend,
        native_factory: Callable[[Sequence[FunctionTool]], HarnessPort],
        limits: SandboxHarnessLimits | None = None,
        job_id: str | None = None,
    ):
        if not isinstance(execution, HarnessExecutionContext):
            raise HarnessError("sandbox Harness requires a trusted execution context")
        self.execution, self.backend, self.native_factory = (
            execution,
            backend,
            native_factory,
        )
        self.limits = limits or SandboxHarnessLimits()
        try:
            self.job_id = (
                str(uuid.UUID(job_id)) if job_id is not None else str(uuid.uuid4())
            )
        except (ValueError, AttributeError, TypeError):
            raise HarnessError(
                "sandbox Harness requires an opaque UUID job identity"
            ) from None
        self._used = False

    async def execute(self, request: HarnessRequest) -> HarnessResult:
        self._validate_request(request)
        self._used = True
        started = time.monotonic()
        attempt = SandboxAttempt(
            self.backend,
            self.limits,
            asyncio.get_running_loop().time() + request.timeout,
        )
        worker = asyncio.create_task(self._native(request, attempt))
        worker.add_done_callback(consume)
        try:
            done, _ = await asyncio.wait({worker}, timeout=attempt.remaining())
            if not done:
                raise TimeoutError("sandbox Harness deadline expired")
            result = worker.result()
            attempt.ensure_active()
            if attempt.inflight:
                raise HarnessError(
                    "sandbox native Harness returned with a command still running"
                )
            attempt.closed = True
            if not await attempt.cleanup(
                min(attempt.remaining(), self.limits.cleanup_timeout_seconds)
            ):
                raise HarnessError("sandbox destruction was not confirmed")
            if attempt.remaining() <= 0:
                raise TimeoutError("sandbox Harness deadline expired during cleanup")
            return observed_result(
                result, attempt, self.execution, self.job_id, started
            )
        except BaseException:
            attempt.aborted = True
            worker.cancel()
            await bounded_drain(worker)
            await attempt.cleanup(self.limits.cleanup_timeout_seconds)
            raise

    def _validate_request(self, request: HarnessRequest) -> None:
        if (
            self._used
            or request.execution != self.execution
            or request.resume is not None
        ):
            raise HarnessError(
                "sandbox Harness requires its original single-use attempt and refuses resume"
            )
        if (
            type(request.timeout) not in (int, float)
            or not math.isfinite(request.timeout)
            or request.timeout <= 0
        ):
            raise HarnessError("sandbox Harness deadline must be finite and positive")

    async def _native(
        self, request: HarnessRequest, attempt: SandboxAttempt
    ) -> HarnessResult:
        try:
            attempt.sandbox_id = await self.backend.create(self.job_id)
            attempt.ensure_active()
            native = self.native_factory((attempt.tool(),))
            result = await native.execute(
                replace(request, cwd=None, timeout=attempt.remaining())
            )
            attempt.ensure_active()
            if not isinstance(result, HarnessResult) or result.artifacts:
                raise HarnessError(
                    "sandbox native Harness must return plain output without another Artifact bundle"
                )
            return result
        finally:
            if attempt.aborted:
                await attempt.cleanup(self.limits.cleanup_timeout_seconds)
