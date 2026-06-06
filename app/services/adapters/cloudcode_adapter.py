from __future__ import annotations

import asyncio
import json
import logging
import signal
from collections.abc import AsyncGenerator
from typing import Any

from app.config import COMMAND_EXECUTE_TIMEOUT, ENABLE_REAL_LLM
from app.services.adapter_manager import BaseAdapter, MockAdapter

logger = logging.getLogger("agenthub.cloudcode")


class CloudCodeAdapter(BaseAdapter):
    """Adapter that wraps an external CLI/subprocess as an AgentHub agent.

    Launches a subprocess (e.g. ``claude`` CLI) and communicates via JSON Lines
    over stdout.  Each JSON Line maps to an AgentHub event through
    :mod:`app.services.event_mapper`.

    Parameters
    ----------
    binary:
        The CLI binary name or path.  Defaults to ``"claude"``.
    default_model:
        Model identifier returned by this adapter.  Defaults to ``"cloud-code"``.
    """

    def __init__(self, binary: str = "claude", default_model: str = "cloud-code") -> None:
        super().__init__()
        self.binary = binary
        self.default_model = default_model
        self._process: asyncio.subprocess.Process | None = None

    # ── Subprocess management ──────────────────────────────────────

    def _build_cmd(self, model: str) -> list[str]:
        """Build the subprocess command line.

        The *model* parameter can carry extra flags when formatted as
        ``"binary --flag1 --flag2"``.
        """
        if model and model != "cloud-code" and model != "ping":
            # Allow model string to carry extra CLI arguments
            return model.split()
        return [self.binary]

    # ── BaseAdapter interface ──────────────────────────────────────

    async def execute_prompt(
        self,
        prompt: str,
        model: str,
        api_key: str = "",
        base_url: str = "",
        **kwargs: Any,
    ) -> str:
        """Non-streaming execution: run subprocess, collect full output."""
        if not ENABLE_REAL_LLM:
            return await MockAdapter().execute_prompt(prompt, model)

        # Compatibility: model="ping" is used for connectivity tests
        if model == "ping":
            return "[CloudCode adapter ready — binary: {}]".format(self.binary)

        cmd = self._build_cmd(model)
        timeout = kwargs.get("timeout", COMMAND_EXECUTE_TIMEOUT)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._process = proc

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")),
                timeout=timeout,
            )

            stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

            if proc.returncode != 0:
                logger.warning(
                    "CloudCode subprocess exit=%d stderr=%s",
                    proc.returncode,
                    stderr[:200],
                )
                return f"[CloudCode 错误 (exit {proc.returncode})]\n{stderr}"

            # Parse JSON Lines: collect all "text" content
            lines = [line.strip() for line in stdout.splitlines() if line.strip()]
            text_parts: list[str] = []
            for line in lines:
                try:
                    obj = json.loads(line)
                    if obj.get("type") == "text":
                        text_parts.append(obj.get("content", ""))
                except json.JSONDecodeError:
                    text_parts.append(line)  # non-JSON line → include as-is

            result = "\n".join(text_parts)
            self.last_usage = {
                "prompt_tokens": max(1, len(prompt) // 4),
                "completion_tokens": max(1, len(result) // 4),
                "total_tokens": max(1, (len(prompt) + len(result)) // 4),
            }
            return result

        except asyncio.TimeoutError:
            if self._process:
                self._process.kill()
                await self._process.wait()
            return f"[CloudCode 执行超时 ({timeout}秒)]"
        finally:
            self._process = None

    async def stream_prompt(
        self,
        prompt: str,
        model: str,
        api_key: str = "",
        base_url: str = "",
    ) -> AsyncGenerator[str, None]:
        """Streaming execution: launch subprocess, yield JSON Lines as they arrive.

        Each yielded string is one JSON Line from the subprocess stdout.
        The caller (agent_service / websocket layer) is responsible for
        deserialising and dispatching via :mod:`app.services.event_mapper`.
        """
        if not ENABLE_REAL_LLM:
            async for chunk in MockAdapter().stream_prompt(prompt, model):
                yield chunk
            return

        cmd = self._build_cmd(model)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._process = proc

            # Write prompt to stdin (non-blocking), then close it
            if proc.stdin:
                proc.stdin.write(prompt.encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()

            # Read stdout line by line
            if proc.stdout:
                async for line in proc.stdout:
                    decoded = line.decode("utf-8", errors="replace").strip()
                    if decoded:
                        yield decoded

            await proc.wait()

            # Log stderr for diagnostics
            if proc.stderr:
                stderr = (await proc.stderr.read()).decode("utf-8", errors="replace")
                if stderr.strip():
                    logger.warning("CloudCode stderr: %s", stderr[:300])

        except asyncio.TimeoutError:
            if self._process:
                self._process.kill()
                await self._process.wait()
            yield json.dumps({"type": "text", "content": "[执行超时]"}, ensure_ascii=False)
        finally:
            self._process = None

        yield ""  # end-of-stream sentinel (matches existing adapter convention)

    def cancel(self) -> None:
        """Cancel the running subprocess by sending SIGTERM."""
        if self._process and self._process.returncode is None:
            self._process.send_signal(signal.SIGTERM)
            logger.info("CloudCode subprocess cancelled (pid=%d)", self._process.pid)
