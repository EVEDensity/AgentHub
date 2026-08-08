# ─────────────────────────────────────────────────────────────────────
# SandboxExecutor — code execution abstraction with isolation support (P0.1-B)
# ─────────────────────────────────────────────────────────────────────
# Three execution modes (SANDBOX_MODE env var):
#   subprocess — local subprocess (dev default, no isolation, workspace access)
#   remote     — call Go sandbox-service HTTP API (isolated Docker container)
#   auto       — try remote first, fall back to subprocess on failure (prod)
#
# OutputSanitizer filters sensitive information from execution output:
#   off    — no filtering
#   basic  — filter secrets (AWS keys, GitHub tokens, private keys, JWTs)
#   strict — basic + IP masking + file path masking
# ─────────────────────────────────────────────────────────────────────
from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal

import httpx

logger = logging.getLogger("agenthub.tools.sandbox")

SandboxMode = Literal["subprocess", "remote", "auto"]


# ── Result type ────────────────────────────────────────────────────────


@dataclass
class SandboxResult:
    """Result of a sandboxed code execution."""

    success: bool
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    mode: str  # "subprocess" | "remote"
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "mode": self.mode,
            "error": self.error,
        }


# ── Output Sanitizer ───────────────────────────────────────────────────


class OutputSanitizer:
    """Filter sensitive information from execution output.

    Three levels:
      - off:    no filtering
      - basic:  filter secrets (AWS keys, GitHub tokens, private keys, JWTs)
      - strict: basic + IP masking + file path masking
    """

    # Secret patterns (always filtered in basic+)
    SECRET_PATTERNS: ClassVar[dict[str, re.Pattern]] = {
        "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
        "aws_secret_key": re.compile(r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])"),
        "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9]{36}"),
        "private_key": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |)PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |DSA |OPENSSH |)PRIVATE KEY-----"),
        "jwt": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
        "slack_token": re.compile(r"xox[bp]-[A-Za-z0-9-]{10,}"),
        "google_api_key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    }

    # Strict-only patterns
    IP_V4_PATTERN = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
    FILE_PATH_PATTERN = re.compile(r"/(?:home|Users|root|var|opt|etc)/[^\s\"']+")
    EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")

    REDACTION_MAP: ClassVar[dict[str, str]] = {
        "aws_access_key": "[REDACTED:AWS_KEY]",
        "aws_secret_key": "[REDACTED:AWS_SECRET]",
        "github_token": "[REDACTED:GITHUB_TOKEN]",
        "private_key": "[REDACTED:PRIVATE_KEY]",
        "jwt": "[REDACTED:JWT]",
        "slack_token": "[REDACTED:SLACK_TOKEN]",
        "google_api_key": "[REDACTED:GOOGLE_KEY]",
    }

    def sanitize(self, text: str, level: str = "basic") -> str:
        """Sanitize text according to the given level.

        Args:
            text: The output text to sanitize.
            level: "off", "basic", or "strict".

        Returns:
            Sanitized text with sensitive info replaced.
        """
        if not text or level == "off":
            return text

        result = text

        # Basic: filter secrets
        for name, pattern in self.SECRET_PATTERNS.items():
            result = pattern.sub(self.REDACTION_MAP.get(name, "[REDACTED]"), result)

        # Strict: also mask IPs, file paths, emails
        if level == "strict":
            result = self.IP_V4_PATTERN.sub("[REDACTED:IP]", result)
            result = self.FILE_PATH_PATTERN.sub("[REDACTED:PATH]", result)
            result = self.EMAIL_PATTERN.sub("[REDACTED:EMAIL]", result)

        return result


# ── Sandbox Executor ───────────────────────────────────────────────────


class SandboxExecutor:
    """Execute code in subprocess or remote sandbox mode.

    Mode is controlled by SANDBOX_MODE env var:
      - subprocess: local subprocess (dev, no isolation)
      - remote:     Go sandbox-service via HTTP (isolated container)
      - auto:       try remote, fall back to subprocess (default)
    """

    def __init__(self) -> None:
        self.mode: SandboxMode = os.getenv("SANDBOX_MODE", "auto")  # type: ignore
        self.service_url = os.getenv(
            "SANDBOX_SERVICE_URL", "http://sandbox-service:8097"
        )
        self.sanitize_level = os.getenv("SANDBOX_OUTPUT_SANITIZE_LEVEL", "basic")
        self._sanitizer = OutputSanitizer()
        # Remote connection timeout (short for auto mode fast-failover)
        self._remote_connect_timeout = float(
            os.getenv("SANDBOX_REMOTE_CONNECT_TIMEOUT", "2.0")
        )
        self._remote_read_timeout = float(
            os.getenv("SANDBOX_REMOTE_READ_TIMEOUT", "60.0")
        )

    async def execute(
        self,
        code: str,
        language: str = "python",
        timeout: float = 30.0,
        cwd: str | None = None,
    ) -> SandboxResult:
        """Execute code in the configured mode.

        Args:
            code: The code to execute.
            language: "python" or "bash".
            timeout: Maximum execution time in seconds.
            cwd: Working directory (subprocess mode only; remote mode
                 runs in an isolated container without workspace access).

        Returns:
            SandboxResult with stdout/stderr/exit_code.
        """
        if self.mode == "remote":
            return await self._execute_remote(code, language, timeout)
        elif self.mode == "subprocess":
            return await self._execute_subprocess(code, language, timeout, cwd)
        else:  # auto
            try:
                return await self._execute_remote(code, language, timeout)
            except Exception as exc:  # noqa: BLE001 - auto mode intentionally falls back
                logger.warning(
                    "sandbox: remote execution failed (%s), falling back to subprocess",
                    exc,
                )
                result = await self._execute_subprocess(code, language, timeout, cwd)
                # Mark that this was a fallback
                return SandboxResult(
                    success=result.success,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    exit_code=result.exit_code,
                    duration_ms=result.duration_ms,
                    mode="subprocess(fallback)",
                    error=f"remote failed: {exc}",
                )

    async def _execute_subprocess(
        self,
        code: str,
        language: str,
        timeout: float,
        cwd: str | None = None,
    ) -> SandboxResult:
        """Execute code via local subprocess."""
        start = time.monotonic()
        lang = "bash" if language in ("sh", "shell", "bash") else "python"

        # Write code to a temp file in cwd (if given) or system temp
        suffix = ".py" if lang == "python" else ".sh"
        exec_dir = Path(cwd) if cwd else Path(tempfile.gettempdir())
        try:
            exec_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            exec_dir = Path(tempfile.gettempdir())

        script_path = exec_dir / f"agenthub_exec_{int(start * 1000)}{suffix}"
        try:
            script_path.write_text(code, encoding="utf-8")

            if lang == "python":
                cmd = ["python", str(script_path)]
            else:
                cmd = ["bash", str(script_path)]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(exec_dir) if cwd else None,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.CancelledError:
                try:
                    if proc.returncode is None:
                        proc.kill()
                    await proc.wait()
                except (OSError, ProcessLookupError):
                    logger.debug("sandbox child was already stopped", exc_info=True)
                raise
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except (OSError, ProcessLookupError):
                    logger.debug("sandbox child cleanup failed", exc_info=True)
                elapsed = int((time.monotonic() - start) * 1000)
                return SandboxResult(
                    success=False,
                    stdout="",
                    stderr=f"execution timed out after {timeout}s",
                    exit_code=-1,
                    duration_ms=elapsed,
                    mode="subprocess",
                    error="timeout",
                )

            elapsed = int((time.monotonic() - start) * 1000)
            return SandboxResult(
                success=(proc.returncode == 0),
                stdout=stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else "",
                stderr=stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else "",
                exit_code=proc.returncode if proc.returncode is not None else -1,
                duration_ms=elapsed,
                mode="subprocess",
            )
        finally:
            try:
                script_path.unlink(missing_ok=True)
            except OSError:
                pass

    async def _execute_remote(
        self,
        code: str,
        language: str,
        timeout: float,
    ) -> SandboxResult:
        """Execute code via Go sandbox-service HTTP API.

        Calls POST {service_url}/v1/execute with the code and returns
        the result. The sandbox-service creates an isolated Docker
        container, executes the code, and cleans up.
        """
        payload = {
            "code": code,
            "language": language,
            "timeout": int(min(timeout, 300)),  # sandbox-service caps at 300s
        }

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=self._remote_connect_timeout,
                    read=max(self._remote_read_timeout, timeout + 5),
                    write=10.0,
                    pool=5.0,
                )
            ) as client:
                resp = await client.post(
                    f"{self.service_url}/v1/execute",
                    json=payload,
                )
                elapsed = int((time.monotonic() - start) * 1000)

                if resp.status_code != 200:
                    error_body = resp.text[:500]
                    return SandboxResult(
                        success=False,
                        stdout="",
                        stderr=error_body,
                        exit_code=-1,
                        duration_ms=elapsed,
                        mode="remote",
                        error=f"HTTP {resp.status_code}",
                    )

                data = resp.json()
                return SandboxResult(
                    success=data.get("success", False),
                    stdout=data.get("stdout", ""),
                    stderr=data.get("stderr", ""),
                    exit_code=data.get("exit_code", -1),
                    duration_ms=data.get("duration_ms", elapsed),
                    mode="remote",
                )
        except httpx.ConnectError as exc:
            raise ConnectionError(f"cannot connect to sandbox-service at {self.service_url}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise TimeoutError(f"sandbox-service request timed out: {exc}") from exc

    def sanitize_output(self, text: str) -> str:
        """Sanitize output text using the configured sanitize level."""
        return self._sanitizer.sanitize(text, self.sanitize_level)


# ── Singleton ──────────────────────────────────────────────────────────

sandbox_executor = SandboxExecutor()
