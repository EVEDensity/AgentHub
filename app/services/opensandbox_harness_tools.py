"""Attempt-owned shell observations; no host files, tokens or local fallback."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from app.services.harness_types import FunctionTool, HarnessError
from app.services.opensandbox_backend import OpenSandboxBackend
from app.services.opensandbox_protocol import OpenSandboxError


@dataclass(frozen=True, slots=True)
class SandboxHarnessLimits:
    max_command_seconds: float = 30.0
    max_script_bytes: int = 16_384
    max_output_bytes: int = 65_536
    max_commands: int = 8
    cleanup_timeout_seconds: float = 15.0

    def __post_init__(self):
        for value in (self.max_command_seconds, self.cleanup_timeout_seconds):
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 < value <= 300
            ):
                raise HarnessError(
                    "sandbox command and cleanup deadlines must be bounded"
                )
        bounds = (
            (self.max_script_bytes, 16_384),
            (self.max_output_bytes, 1_048_576),
            (self.max_commands, 32),
        )
        if any(
            type(value) is not int or not 1 <= value <= maximum
            for value, maximum in bounds
        ):
            raise HarnessError(
                "sandbox command, output and call limits must be bounded"
            )


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def consume(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()


async def bounded_drain(task: asyncio.Task, seconds: float = 0.25) -> None:
    try:
        await asyncio.wait({task}, timeout=seconds)
    except asyncio.CancelledError:
        return


class SandboxAttempt:
    def __init__(
        self, backend: OpenSandboxBackend, limits: SandboxHarnessLimits, deadline: float
    ):
        self.backend, self.limits, self.deadline = backend, limits, deadline
        self.sandbox_id: str | None = None
        self.commands: list[dict] = []
        self.aborted = False
        self.closed = False
        self.fatal = False
        self.inflight = False
        self.cleanup_confirmed = False
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task | None = None

    def remaining(self) -> float:
        return max(0.0, self.deadline - asyncio.get_running_loop().time())

    def ensure_active(self) -> None:
        task = asyncio.current_task()
        if self.aborted or self.closed or (task is not None and task.cancelling()):
            raise asyncio.CancelledError
        if self.remaining() <= 0:
            raise TimeoutError("sandbox Harness deadline expired")

    def sanitize(self, text: str) -> str:
        return text.replace(self.backend.config.api_key, "[REDACTED]")

    def validate(self, arguments: Mapping) -> dict:
        if not isinstance(arguments, Mapping) or set(arguments) - {
            "script",
            "timeout_seconds",
        }:
            raise ValueError("run_shell only accepts script and timeout_seconds")
        script = arguments.get("script")
        if not isinstance(script, str) or not script.strip() or "\x00" in script:
            raise ValueError("run_shell requires a nonempty shell script")
        if len(script.encode("utf-8")) > self.limits.max_script_bytes:
            raise ValueError("run_shell script exceeds its byte limit")
        timeout = arguments.get("timeout_seconds", self.limits.max_command_seconds)
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("run_shell timeout must be finite and positive")
        return {
            "script": script,
            "timeout_seconds": min(timeout, self.limits.max_command_seconds),
        }

    def tool(self) -> FunctionTool:
        return FunctionTool(
            "run_shell",
            self.run,
            self.validate,
            "Execute a bounded shell script in this attempt's disposable Linux container. "
            "The initial cwd is /workspace; commands may access the container filesystem. "
            "Output and exit code are observations, not a test verdict.",
            {
                "type": "object",
                "properties": {
                    "script": {"type": "string"},
                    "timeout_seconds": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "maximum": self.limits.max_command_seconds,
                    },
                },
                "required": ["script"],
                "additionalProperties": False,
            },
        )

    async def run(self, arguments: Mapping) -> str:
        arguments = self.validate(arguments)
        async with self._lock:
            self.ensure_active()
            if len(self.commands) >= self.limits.max_commands or self.fatal:
                self.fatal = True
                raise HarnessError(
                    "sandbox command budget or execution availability is exhausted"
                )
            observation = self._observation(arguments)
            self.commands.append(observation)
            self.inflight = True
            try:
                await self._command(arguments, observation)
                self.ensure_active()
            finally:
                observation["finishedAt"] = utc_now()
                self.inflight = False
            return json.dumps(observation, sort_keys=True, separators=(",", ":"))

    def _observation(self, arguments: Mapping) -> dict:
        return {
            "sequence": len(self.commands) + 1,
            "scriptSha256": "sha256:"
            + hashlib.sha256(arguments["script"].encode()).hexdigest(),
            "commandId": None,
            "startedAt": utc_now(),
            "finishedAt": None,
            "timeoutSeconds": min(arguments["timeout_seconds"], self.remaining()),
            "exitCode": None,
            "combinedOutput": "",
            "outputStream": "combined",
            "outputBytes": 0,
            "outputTruncated": False,
            "status": "started",
        }

    async def _command(self, arguments: Mapping, observation: dict) -> None:
        script = "cd /workspace || exit 125\n" + arguments["script"]
        try:
            result = await self.backend.execute(
                self.sandbox_id,
                ["bash", "--noprofile", "--norc", "-c", script],
                timeout_seconds=observation["timeoutSeconds"],
            )
        except asyncio.CancelledError:
            observation["status"] = "cancelled"
            raise
        except OpenSandboxError as error:
            self.fatal = True
            observation.update(
                status="backend_error", errorCode=error.code, exitCode=error.exit_code
            )
            if error.cleanup_action == "confirmed_sandbox_destroy":
                self.cleanup_confirmed = True
            return
        raw = self.sanitize(result.output).encode("utf-8")
        limited = len(raw) > self.limits.max_output_bytes
        self.fatal = self.fatal or limited
        observation.update(
            commandId=result.execution_id,
            exitCode=result.exit_code,
            combinedOutput=raw[: self.limits.max_output_bytes].decode(
                "utf-8", errors="ignore"
            ),
            outputBytes=len(raw),
            outputTruncated=limited,
            status="output_limit" if limited else "completed",
        )

    async def cleanup(self, seconds: float) -> bool:
        if self.cleanup_confirmed:
            return True
        if self.sandbox_id is None:
            return False
        # Retry only this owned sandbox after an uncertain/cancelled ACK.
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(
                self.backend.destroy(self.sandbox_id)
            )
            self._cleanup_task.add_done_callback(consume)
        try:
            done, _ = await asyncio.wait(
                {self._cleanup_task}, timeout=max(0.0, seconds)
            )
            if done:
                if self._cleanup_task.cancelled():
                    return False
                result = self._cleanup_task.result()
                self.cleanup_confirmed = (
                    result is not None and result.action == "confirmed_sandbox_destroy"
                )
        except Exception:  # noqa: BLE001 - failure retains an explicit unconfirmed cleanup
            return False
        if not self.cleanup_confirmed and not self._cleanup_task.done():
            self._cleanup_task.cancel()
        return self.cleanup_confirmed
