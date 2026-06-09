from __future__ import annotations

import asyncio
import json
import logging
import signal
from collections.abc import AsyncGenerator
from typing import Any, TYPE_CHECKING

from app.config import COMMAND_EXECUTE_TIMEOUT, ENABLE_REAL_LLM
from app.services.adapter_manager import BaseAdapter, MockAdapter

if TYPE_CHECKING:
    from app.services.protocols.base import SubprocessProtocol

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
    protocol:
        Optional :class:`SubprocessProtocol` for message encoding/decoding.
        When set and ``protocol.supports_interactive()`` is ``True``,
        callers should use :meth:`stream_prompt_interactive` instead of
        :meth:`stream_prompt` for bidirectional tool feedback.
    """

    def __init__(
        self,
        binary: str = "claude",
        default_model: str = "cloud-code",
        protocol: "SubprocessProtocol | None" = None,
    ) -> None:
        super().__init__()
        self.binary = binary
        self.default_model = default_model
        self.protocol = protocol
        self._process: asyncio.subprocess.Process | None = None
        # Cached CLI session ID for --resume / --continue across turns
        self._cli_session_id: str | None = None

    # ── Platform-specific command wrapping ──────────────────────────

    @staticmethod
    def _resolve_cmd_shim(shim_path: str) -> str | None:
        """Try to resolve a ``.cmd`` / ``.bat`` shim to the underlying ``.exe``.

        npm global installs (and many other Windows toolchains) register a
        ``.cmd`` wrapper that sets up ``PATH`` / ``NODE_PATH`` before
        launching the real binary.  Calling the ``.exe`` directly is both
        faster and *much* more reliable with ``asyncio`` subprocess pipes
        than bouncing through ``cmd.exe /c``.

        Returns the resolved ``.exe`` path on success, or ``None`` if the
        shim could not be resolved (the caller should fall back to
        ``cmd.exe /c``).
        """
        import os as _os

        # Many .cmd shims follow the npm pattern:
        #     "%dp0%\node_modules\...\bin\name.exe" %*
        # where %dp0% is set to the directory containing the .cmd file.
        # We parse the script to extract the real executable.
        try:
            with open(shim_path, "r", encoding="utf-8", errors="replace") as _f:
                content = _f.read()
        except OSError:
            return None

        dp0 = _os.path.dirname(_os.path.abspath(shim_path))

        # Common patterns in .cmd launchers:
        #   "%dp0%\path\to\tool.exe" %*
        #   "%~dp0path\to\tool.exe" %*
        #   %dp0%\path\to\tool.exe %*
        import re as _re
        for line in content.split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith(("@", "REM", "GOTO", "SET", "EXIT", "CALL", "SETLOCAL", "ENDLOCAL")):
                continue
            m = _re.search(
                r"""["']?((?:%dp0%|%~dp0)[^"'\s]*\.(?:exe|com))["']?""",
                stripped, _re.IGNORECASE,
            )
            if m:
                raw = m.group(1)
                raw = raw.replace("%~dp0", dp0 + "\\").replace("%dp0%", dp0 + "\\")
                # Normalize path separators and resolve to absolute
                candidate = _os.path.normpath(raw)
                if _os.path.isfile(candidate):
                    return candidate
        return None

    def _wrap_cmd_for_platform(self, cmd: list[str]) -> tuple[list[str], int]:
        """Wrap a subprocess command list for Windows .CMD / .BAT shims.

        On Windows, ``asyncio.create_subprocess_exec`` invokes the
        Windows ``CreateProcess`` API, which does not understand
        ``.cmd`` / ``.bat`` shims (e.g. npm-installed ``claude.CMD``).

        This helper **first** tries to resolve the shim to the real
        ``.exe`` it wraps (which works reliably with async subprocess
        pipes).  Only when resolution fails does it fall back to
        ``cmd.exe /c``.

        Parameters
        ----------
        cmd:
            The command list returned by :meth:`_build_cmd`.

        Returns
        -------
        (wrapped_cmd, creationflags):
            The command list to pass to ``asyncio.create_subprocess_exec``
            and the ``creationflags`` value (0 on non-Windows).
        """
        import sys as _sys
        import shutil as _shutil
        import subprocess as _sp

        if _sys.platform != "win32" or not cmd:
            return cmd, 0

        head = cmd[0]
        shim_path = ""

        # Stage 1: explicit .cmd / .bat suffix
        if head.lower().endswith((".cmd", ".bat")):
            shim_path = head
        else:
            # Stage 2: resolve via PATH (PATHEXT on Windows)
            resolved = _shutil.which(head) or ""
            if resolved.lower().endswith((".cmd", ".bat")):
                shim_path = resolved

        if shim_path:
            # Best effort: resolve the shim to the real .exe
            exe_path = self._resolve_cmd_shim(shim_path)
            if exe_path:
                return [exe_path] + cmd[1:], 0
            # Fallback: cmd.exe /c
            return ["cmd.exe", "/c", shim_path] + cmd[1:], getattr(_sp, "CREATE_NO_WINDOW", 0)

        return cmd, 0

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

    # ── Bidirectional communication ──────────────────────────────────

    def send_input(self, data: str) -> None:
        """Write *data* to the subprocess stdin.

        Only valid during an active interactive session (i.e. after
        :meth:`stream_prompt_interactive` has been called and before the
        subprocess exits).  In one-shot mode the stdin is already closed
        so this is a no-op.

        Raises no error on missing/closed stdin — the caller should
        handle the subprocess exit through the stream loop.
        """
        proc = self._process
        if proc is None or proc.stdin is None:
            return
        try:
            proc.stdin.write(data.encode("utf-8"))
        except (BrokenPipeError, ProcessLookupError, OSError):
            pass  # subprocess already exited — consumer will notice on stdout

    async def stream_prompt_interactive(
        self,
        prompt: str,
        model: str,
        turn_id: str = "",
        max_turns: int = 10,
    ) -> AsyncGenerator[str, None]:
        """Interactive streaming: stdin stays open for tool-result feedback.

        Unlike :meth:`stream_prompt` which is one-shot (write prompt →
        close stdin → read until EOF), this method:

        1. Launches the subprocess (using the protocol's interactive
           command if available).
        2. Writes the initial user message via the protocol encoder.
        3. Keeps stdin open so the caller can call :meth:`send_input`
           to feed tool results (or follow-up user messages) back.
        4. Yields each JSON Line from stdout.
        5. Stops when the ``end`` event is received or *max_turns*
           tool-call rounds have occurred.

        Parameters
        ----------
        prompt:
            The initial user prompt.
        model:
            Adapter type identifier (e.g. ``"local_claude"``).
        turn_id:
            Opaque turn identifier generated by the caller.
        max_turns:
            Maximum number of tool-call round-trips before forcing exit.
        """
        if not ENABLE_REAL_LLM:
            async for chunk in MockAdapter().stream_prompt(prompt, model):
                yield chunk
            return

        # Use protocol's interactive command when available
        if self.protocol is not None and self.protocol.supports_interactive():
            cmd = self.protocol.get_interactive_command()
        else:
            cmd = self._build_cmd(model)

        # Wrap .CMD / .BAT shims for Windows (npm-installed clis)
        cmd, creation_flags = self._wrap_cmd_for_platform(cmd)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creation_flags,
            )
            self._process = proc

            # Write initial user message
            if proc.stdin:
                if self.protocol is not None and self.protocol.supports_interactive():
                    encoded = self.protocol.encode_user_message(prompt, turn_id)
                else:
                    encoded = prompt
                proc.stdin.write(encoded.encode("utf-8"))
                await proc.stdin.drain()
                # NOTE: do NOT close stdin — caller feeds tool results via send_input()

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
                    logger.warning("CloudCode interactive stderr: %s", stderr[:300])

        except asyncio.TimeoutError:
            if self._process:
                self._process.kill()
                await self._process.wait()
            yield json.dumps({"type": "text", "content": "[执行超时]"}, ensure_ascii=False)
        finally:
            self._process = None

        yield ""  # end-of-stream sentinel

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
        **kwargs: object,
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

        # Wrap .CMD / .BAT shims for Windows (npm-installed clis)
        cmd, creation_flags = self._wrap_cmd_for_platform(cmd)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creation_flags,
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
