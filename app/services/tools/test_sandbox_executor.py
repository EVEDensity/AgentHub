# ─────────────────────────────────────────────────────────────────────
# Unit tests for SandboxExecutor + OutputSanitizer (P0.1-B / Sprint 3)
# ─────────────────────────────────────────────────────────────────────
# Run: pytest app/services/tools/test_sandbox_executor.py -v
# ─────────────────────────────────────────────────────────────────────
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# Ensure app is importable when running from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from app.services.tools.sandbox_executor import (
    OutputSanitizer,
    SandboxExecutor,
    SandboxResult,
    sandbox_executor,
)


# ═══════════════════════════════════════════════════════════════════════
#  OutputSanitizer Tests
# ═══════════════════════════════════════════════════════════════════════


class TestOutputSanitizer:
    """Test sensitive information filtering."""

    def setup_method(self):
        self.sanitizer = OutputSanitizer()

    def test_off_level_no_filtering(self):
        """off level should not filter anything."""
        text = "AWS key: AKIAIOSFODNN7EXAMPLE"
        assert self.sanitizer.sanitize(text, "off") == text

    def test_aws_access_key_filtered(self):
        """basic level should filter AWS access keys."""
        text = "Using key AKIAIOSFODNN7EXAMPLE for upload"
        result = self.sanitizer.sanitize(text, "basic")
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "[REDACTED:AWS_KEY]" in result

    def test_github_token_filtered(self):
        """basic level should filter GitHub tokens."""
        text = "ghp_abcdefghijklmnopqrstuvwxyz0123456789AB"
        result = self.sanitizer.sanitize(text, "basic")
        assert "ghp_" not in result
        assert "[REDACTED:GITHUB_TOKEN]" in result

    def test_jwt_filtered(self):
        """basic level should filter JWTs."""
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        text = f"Authorization: Bearer {jwt}"
        result = self.sanitizer.sanitize(text, "basic")
        assert jwt not in result
        assert "[REDACTED:JWT]" in result

    def test_private_key_filtered(self):
        """basic level should filter private keys."""
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----"
        result = self.sanitizer.sanitize(text, "basic")
        assert "MIIEpAIBAAKCAQEA" not in result
        assert "[REDACTED:PRIVATE_KEY]" in result

    def test_strict_level_masks_ip(self):
        """strict level should mask IPv4 addresses."""
        text = "Connecting to 192.168.1.100 on port 8080"
        result = self.sanitizer.sanitize(text, "strict")
        assert "192.168.1.100" not in result
        assert "[REDACTED:IP]" in result

    def test_strict_level_masks_file_path(self):
        """strict level should mask file paths."""
        text = "Error reading /home/user/secret/config.json"
        result = self.sanitizer.sanitize(text, "strict")
        assert "/home/user/secret/config.json" not in result
        assert "[REDACTED:PATH]" in result

    def test_strict_level_masks_email(self):
        """strict level should mask email addresses."""
        text = "Contact admin@company.com for details"
        result = self.sanitizer.sanitize(text, "strict")
        assert "admin@company.com" not in result
        assert "[REDACTED:EMAIL]" in result

    def test_basic_level_does_not_mask_ip(self):
        """basic level should NOT mask IPs (only strict does)."""
        text = "IP: 10.0.0.1"
        result = self.sanitizer.sanitize(text, "basic")
        assert "10.0.0.1" in result

    def test_empty_text(self):
        """sanitize should handle empty text."""
        assert self.sanitizer.sanitize("", "basic") == ""
        assert self.sanitizer.sanitize(None, "basic") is None  # type: ignore

    def test_multiple_secrets(self):
        """sanitize should handle multiple secrets in one text."""
        text = "key=AKIAIOSFODNN7EXAMPLE token=ghp_abcdefghijklmnopqrstuvwxyz0123456789AB"
        result = self.sanitizer.sanitize(text, "basic")
        assert "AKIA" not in result
        assert "ghp_" not in result


# ═══════════════════════════════════════════════════════════════════════
#  SandboxExecutor Tests
# ═══════════════════════════════════════════════════════════════════════


class TestSandboxExecutorSubprocess:
    """Test subprocess execution mode."""

    @pytest.mark.asyncio
    async def test_subprocess_python_hello(self):
        """subprocess mode should execute Python code."""
        executor = SandboxExecutor()
        result = await executor._execute_subprocess("print('hello sandbox')", "python", 10)
        assert result.success is True
        assert "hello sandbox" in result.stdout
        assert result.exit_code == 0
        assert result.mode == "subprocess"

    @pytest.mark.asyncio
    @pytest.mark.skipif(sys.platform == "win32", reason="bash path handling differs on Windows")
    async def test_subprocess_bash_echo(self):
        """subprocess mode should execute bash code (Linux/macOS only)."""
        executor = SandboxExecutor()
        result = await executor._execute_subprocess("echo 'bash works'", "bash", 10)
        assert result.success is True
        assert "bash works" in result.stdout
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_subprocess_error_exit_code(self):
        """subprocess mode should capture non-zero exit codes."""
        executor = SandboxExecutor()
        result = await executor._execute_subprocess(
            "import sys; sys.exit(1)", "python", 10
        )
        assert result.success is False
        assert result.exit_code == 1

    @pytest.mark.asyncio
    async def test_subprocess_stderr(self):
        """subprocess mode should capture stderr."""
        executor = SandboxExecutor()
        result = await executor._execute_subprocess(
            "import sys; sys.stderr.write('error msg\\n')", "python", 10
        )
        assert "error msg" in result.stderr

    @pytest.mark.asyncio
    async def test_subprocess_timeout(self):
        """subprocess mode should handle timeout."""
        executor = SandboxExecutor()
        result = await executor._execute_subprocess(
            "import time; time.sleep(10)", "python", 1
        )
        assert result.success is False
        assert "timed out" in result.stderr.lower() or result.error == "timeout"


class TestSandboxExecutorRemote:
    """Test remote execution mode (mocked HTTP)."""

    @pytest.mark.asyncio
    async def test_remote_success(self):
        """remote mode should call sandbox-service and return result."""
        executor = SandboxExecutor()
        executor.service_url = "http://mock-sandbox:8097"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "stdout": "hello from sandbox\n",
            "stderr": "",
            "exit_code": 0,
            "duration_ms": 150,
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await executor._execute_remote("print('hi')", "python", 30)

        assert result.success is True
        assert "hello from sandbox" in result.stdout
        assert result.exit_code == 0
        assert result.mode == "remote"

    @pytest.mark.asyncio
    async def test_remote_connection_error(self):
        """remote mode should raise ConnectionError when service is down."""
        executor = SandboxExecutor()
        executor.service_url = "http://unreachable:8097"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            with pytest.raises(ConnectionError):
                await executor._execute_remote("print('hi')", "python", 30)

    @pytest.mark.asyncio
    async def test_remote_http_error(self):
        """remote mode should return error result on HTTP 500."""
        executor = SandboxExecutor()
        executor.service_url = "http://mock-sandbox:8097"

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await executor._execute_remote("print('hi')", "python", 30)

        assert result.success is False
        assert "HTTP 500" in result.error


class TestSandboxExecutorAuto:
    """Test auto mode (remote → subprocess fallback)."""

    @pytest.mark.asyncio
    async def test_auto_fallback_to_subprocess(self):
        """auto mode should fall back to subprocess when remote fails."""
        executor = SandboxExecutor()
        executor.mode = "auto"
        executor.service_url = "http://unreachable:8097"

        # Mock remote to fail
        with patch.object(
            executor,
            "_execute_remote",
            side_effect=ConnectionError("service down"),
        ):
            result = await executor.execute("print('fallback')", "python", 10)

        assert result.success is True
        assert "fallback" in result.stdout
        assert "subprocess(fallback)" in result.mode
        assert "remote failed" in result.error

    @pytest.mark.asyncio
    async def test_auto_uses_remote_when_available(self):
        """auto mode should use remote when it's available."""
        executor = SandboxExecutor()
        executor.mode = "auto"

        remote_result = SandboxResult(
            success=True,
            stdout="from remote\n",
            stderr="",
            exit_code=0,
            duration_ms=50,
            mode="remote",
        )

        with patch.object(
            executor, "_execute_remote", return_value=remote_result
        ):
            result = await executor.execute("print('hi')", "python", 10)

        assert result.success is True
        assert "from remote" in result.stdout
        assert result.mode == "remote"

    @pytest.mark.asyncio
    async def test_remote_only_mode_raises_on_failure(self):
        """remote mode should NOT fall back; it should raise."""
        executor = SandboxExecutor()
        executor.mode = "remote"
        executor.service_url = "http://unreachable:8097"

        with patch.object(
            executor,
            "_execute_remote",
            side_effect=ConnectionError("service down"),
        ):
            with pytest.raises(ConnectionError):
                await executor.execute("print('hi')", "python", 10)


class TestSandboxExecutorSanitize:
    """Test that sanitize_output uses configured level."""

    def test_sanitize_output_basic(self):
        """sanitize_output should filter with configured level."""
        executor = SandboxExecutor()
        executor.sanitize_level = "basic"
        result = executor.sanitize_output("key=AKIAIOSFODNN7EXAMPLE")
        assert "AKIA" not in result

    def test_sanitize_output_off(self):
        """sanitize_output with off level should not filter."""
        executor = SandboxExecutor()
        executor.sanitize_level = "off"
        text = "key=AKIAIOSFODNN7EXAMPLE"
        result = executor.sanitize_output(text)
        assert result == text

    def test_singleton_exists(self):
        """Module-level singleton should be instantiated."""
        assert sandbox_executor is not None
        assert isinstance(sandbox_executor, SandboxExecutor)
