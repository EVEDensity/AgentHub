from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agenthub.local_discovery")

# ═══════════════════════════════════════════════════════════════════════
# Discovery map — configuration for each supported local CLI agent
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class LocalAgentCandidate:
    """Normalised representation of a discovered local CLI agent."""

    adapter_type: str          # e.g. "local_claude"
    display_name: str          # e.g. "Claude Code"
    binary: str                # CLI binary name (e.g. "claude")
    install_path: str          # resolved absolute path (or empty if not found)
    version: str               # version string from --version (or empty)
    installed: bool            # True if binary found on PATH
    healthy: bool              # True if --version executed successfully
    error_message: str         # non-empty when health check fails
    capabilities: list[str]    # e.g. ["code_generation", "file_ops"]
    headless_command: str      # human-readable headless invocation example


DISCOVERY_MAP: dict[str, dict[str, Any]] = {
    "local_claude": {
        "display_name": "Claude Code",
        "binary": "claude",
        "check_flags": ["--version"],
        "capabilities": [
            "code_generation",
            "code_review",
            "file_ops",
            "shell_exec",
            "web_search",
        ],
        "headless_command": 'claude -p "<prompt>" --output-format stream-json',
        "description": "Anthropic 官方 CLI Agent，支持 subprocess 无头模式调用",
    },
    "local_codex": {
        "display_name": "Codex CLI",
        "binary": "codex",
        "check_flags": ["--version"],
        "capabilities": [
            "code_generation",
            "code_review",
            "shell_exec",
        ],
        "headless_command": 'codex exec "<prompt>" --json',
        "description": "OpenAI Codex CLI，支持 one-shot exec 模式和结构化输出",
    },
    "local_openclaw": {
        "display_name": "OpenClaw",
        "binary": "openclaw-cli",
        "check_flags": ["--version"],
        "capabilities": [
            "code_generation",
            "file_ops",
            "web_search",
        ],
        "headless_command": 'openclaw-cli --json "<prompt>"',
        "description": "开源 TypeScript CLI Agent，支持 MCP 和多后端切换",
    },
}


# ═══════════════════════════════════════════════════════════════════════
# Discovery & health-check
# ═══════════════════════════════════════════════════════════════════════


async def discover_local_agents() -> list[LocalAgentCandidate]:
    """Scan the system PATH for supported local AI CLI tools.

    Each entry in :data:`DISCOVERY_MAP` is checked:
    1. ``shutil.which()`` to locate the binary on PATH.
    2. ``<binary> --version`` subprocess to verify the tool is runnable.

    Returns a flat list of :class:`LocalAgentCandidate` — one per
    configured entry, regardless of whether it was found.  The caller
    inspects ``.installed`` and ``.healthy`` to decide what to show.
    """
    candidates: list[LocalAgentCandidate] = []

    for adapter_type, cfg in DISCOVERY_MAP.items():
        binary: str = cfg["binary"]
        check_flags: list[str] = cfg.get("check_flags", ["--version"])

        install_path = shutil.which(binary) or ""
        installed = bool(install_path)

        version = ""
        healthy = False
        error_message = ""

        if installed:
            version, healthy, error_message = await _check_health(
                install_path, check_flags
            )

        candidates.append(
            LocalAgentCandidate(
                adapter_type=adapter_type,
                display_name=cfg["display_name"],
                binary=binary,
                install_path=install_path,
                version=version,
                installed=installed,
                healthy=healthy,
                error_message=error_message,
                capabilities=list(cfg.get("capabilities", [])),
                headless_command=cfg.get("headless_command", ""),
            )
        )

    installed_count = sum(1 for c in candidates if c.installed)
    healthy_count = sum(1 for c in candidates if c.healthy)
    logger.info(
        "Local agent discovery complete: %d candidates, %d installed, %d healthy",
        len(candidates), installed_count, healthy_count,
    )
    return candidates


async def check_agent_health(
    binary: str,
    adapter_type: str,
    check_flags: list[str] | None = None,
) -> tuple[str, bool, str]:
    """Check whether *binary* is runnable by invoking ``<binary> --version``.

    Returns ``(version, healthy, error_message)``.
    """
    if check_flags is None:
        cfg = DISCOVERY_MAP.get(adapter_type, {})
        check_flags = cfg.get("check_flags", ["--version"])

    return await _check_health(binary, check_flags)


async def _check_health(
    binary: str,
    check_flags: list[str],
    timeout: float = 15.0,
) -> tuple[str, bool, str]:
    """Run ``<binary> <check_flags>`` and capture the first line of output.

    Returns ``(version, healthy, error_message)``.
    """
    import sys as _sys
    import subprocess as _sp

    # ── Windows: .CMD / .BAT shims (npm/pnpm global installs) cannot be
    # executed directly via CreateProcess — they must go through cmd.exe.
    # Without this, asyncio.create_subprocess_exec raises
    # OSError: [WinError 193] / [WinError 2] and healthy stays False
    # even though the CLI is perfectly functional.
    is_windows = _sys.platform == "win32"
    is_cmd_shim = binary.lower().endswith((".cmd", ".bat"))

    if is_windows and is_cmd_shim:
        # list 形式传参,避免 cmd.exe 把 "<path> --version" 当作单条命令名
        exec_cmd: list[str] = ["cmd.exe", "/c", binary, *check_flags]
        creation_flags = getattr(_sp, "CREATE_NO_WINDOW", 0)
    else:
        exec_cmd = [binary, *check_flags]
        creation_flags = 0

    try:
        proc = await asyncio.create_subprocess_exec(
            *exec_cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creation_flags if creation_flags else None,
        )
    except NotImplementedError:
        # ── Fallback for event loops that don't support subprocesses ──
        # (e.g. _WindowsSelectorEventLoop used by some uvicorn configs).
        # Use synchronous subprocess.run() in a thread to avoid blocking
        # the event loop.
        return await _check_health_sync(exec_cmd, creation_flags, timeout)
    except FileNotFoundError:
        return "", False, f"二进制文件未找到: {binary}"
    except PermissionError:
        return "", False, f"无执行权限: {binary}"
    except Exception as exc:
        return "", False, str(exc)[:300]

    try:
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return "", False, f"健康检查超时 ({timeout}s)"

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        combined = stdout or stderr
        # Take the first non-empty line as the version string
        version = ""
        for line in combined.splitlines():
            line = line.strip()
            if line:
                version = line[:200]  # cap at 200 chars
                break

        if proc.returncode == 0:
            return version, True, ""
        else:
            return version, False, stderr[:300] or f"exit code {proc.returncode}"

    except Exception as exc:
        return "", False, str(exc)[:300]


async def _check_health_sync(
    exec_cmd: list[str],
    creation_flags: int = 0,
    timeout: float = 15.0,
) -> tuple[str, bool, str]:
    """Fallback health check using synchronous subprocess.run().

    Used when the running event loop does not support async subprocesses
    (e.g. :class:`_WindowsSelectorEventLoop` on Windows).
    """
    import subprocess as _sp
    import asyncio as _asyncio

    def _run() -> tuple[str, bool, str]:
        try:
            kwargs: dict = dict(
                stdin=_sp.DEVNULL,
                stdout=_sp.PIPE,
                stderr=_sp.PIPE,
                timeout=timeout,
            )
            if creation_flags:
                kwargs["creationflags"] = creation_flags

            result = _sp.run(exec_cmd, **kwargs)

            stdout = result.stdout.decode("utf-8", errors="replace").strip()
            stderr = result.stderr.decode("utf-8", errors="replace").strip()

            combined = stdout or stderr
            version = ""
            for line in combined.splitlines():
                line = line.strip()
                if line:
                    version = line[:200]
                    break

            if result.returncode == 0:
                return version, True, ""
            else:
                return version, False, stderr[:300] or f"exit code {result.returncode}"

        except FileNotFoundError:
            return "", False, f"二进制文件未找到: {exec_cmd[0]}"
        except PermissionError:
            return "", False, f"无执行权限: {exec_cmd[0]}"
        except _sp.TimeoutExpired:
            return "", False, f"健康检查超时 ({timeout}s)"
        except Exception as exc:
            return "", False, str(exc)[:300]

    return await _asyncio.to_thread(_run)


# ═══════════════════════════════════════════════════════════════════════
# Registration helper — avoid duplicate insertions
# ═══════════════════════════════════════════════════════════════════════


async def register_local_agent(
    candidate: LocalAgentCandidate,
    user_id: str,
    domain: str = "",
    agent_id: str = "",
    risk_level: str = "L1",
    duty_note: str = "",
    base_model_name: str = "",
) -> dict[str, Any]:
    """Insert a discovered local agent into ``agent_registry``.

    If *agent_id* is empty it is derived from the adapter_type
    (e.g. ``"local_claude"`` → ``"claude-code-local"``).

    Returns a dict with ``status``, ``agentId``, and optionally
    ``error`` if the insert failed.
    """
    from app.db.session import aexecute, afetch_one
    from app.services.secret_service import encrypt_secret

    effective_id = agent_id.strip() or _default_agent_id(candidate.adapter_type)

    # Check for existing registration (per-user)
    existing = await afetch_one(
        "SELECT agent_id FROM agent_registry WHERE agent_id=$1 AND user_id=$2",
        effective_id, user_id,
    )
    if existing:
        return {
            "status": "skipped",
            "agentId": effective_id,
            "reason": "已注册，跳过重复添加",
        }

    try:
        await aexecute(
            "INSERT INTO agent_registry("
            "agent_id,user_id,domain,status,adapter_type,base_model_name,"
            "risk_level,duty_note,display_name,capability_tags,base_url,api_key"
            ") VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
            effective_id,
            user_id,
            domain.strip() or _default_domain(candidate.adapter_type),
            "sleeping",  # status will be updated after health check
            candidate.adapter_type,
            base_model_name.strip() or candidate.display_name,
            risk_level,
            duty_note.strip() or f"本地 {candidate.display_name} Agent (v{candidate.version})" if candidate.version else f"本地 {candidate.display_name} Agent",
            candidate.display_name,
            str(candidate.capabilities).replace("'", '"'),  # JSON-safe list
            "",  # base_url — not applicable for subprocess adapters
            encrypt_secret(""),  # no API key for local agents
        )
        return {"status": "success", "agentId": effective_id}
    except Exception as exc:
        logger.warning("Failed to register local agent %s: %s", effective_id, exc)
        return {"status": "error", "agentId": effective_id, "error": str(exc)[:300]}


def _default_agent_id(adapter_type: str) -> str:
    """Derive a stable default agent_id from the adapter type."""
    mapping = {
        "local_claude": "claude-code-local",
        "local_codex": "codex-cli-local",
        "local_openclaw": "openclaw-local",
    }
    return mapping.get(adapter_type, adapter_type.replace("_", "-") + "-local")


def _default_domain(adapter_type: str) -> str:
    """Derive a stable default domain from the adapter type."""
    mapping = {
        "local_claude": "codegen",
        "local_codex": "codegen",
        "local_openclaw": "codegen",
    }
    return mapping.get(adapter_type, "general")
