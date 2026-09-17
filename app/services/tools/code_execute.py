"""Code-execution builtin tool (sandboxed subprocess runner).

Split out of ``builtin_tools.py``.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.services.tools._common import (
    CODE_EXECUTE_INSTALL_TIMEOUT,
    CODE_EXECUTE_TIMEOUT,
    MAX_CODE_OUTPUT_CHARS,
)
from app.services.tools.sandbox_executor import sandbox_executor
from app.utils.async_file import awrite_text

logger = logging.getLogger("agenthub.tools.builtin.code_execute")

async def code_execute_handler(
    code: str,
    language: str = "python",
    timeout: int = 30,
    cwd: str = ".",
) -> dict[str, Any]:
    """Execute code in a sandboxed subprocess within the workspace.

    The working directory is the user's per-session workspace (or a
    subdirectory within it), so scripts can access, import, and test
    files the agent has written.  Unlike the old implementation that
    ran in a throw-away temp directory, this gives the agent a genuine
    write→execute→debug loop.

    Supports: python, bash
    """
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    if not code or not code.strip():
        return {"success": False, "error": "代码内容不能为空"}

    lang = language.lower()
    if lang in ("sh", "shell"):
        lang = "bash"
    if lang not in ("python", "bash"):
        return {"success": False, "error": f"不支持的语言: {language}。支持: python, bash"}

    # ── Remote sandbox execution (P0.1-B) ──────────────────────────────
    # When SANDBOX_MODE is "remote" or "auto", try the Go sandbox-service
    # first. Remote mode runs code in an isolated Docker container without
    # workspace access. Auto mode falls back to subprocess on failure.
    if sandbox_executor.mode in ("remote", "auto"):
        try:
            remote_result = await sandbox_executor._execute_remote(
                code, lang, min(timeout, CODE_EXECUTE_TIMEOUT)
            )
            stdout = sandbox_executor.sanitize_output(remote_result.stdout)[:MAX_CODE_OUTPUT_CHARS]
            stderr = sandbox_executor.sanitize_output(remote_result.stderr)[:MAX_CODE_OUTPUT_CHARS]

            result_parts: list[str] = []
            if stdout:
                result_parts.append(f"[标准输出]\n{stdout}")
            if stderr:
                result_parts.append(f"[标准错误]\n{stderr}")
            if remote_result.exit_code != 0:
                result_parts.append(f"[退出码: {remote_result.exit_code}]")
            if not result_parts:
                result_parts.append("[无输出]")

            return {
                "success": remote_result.success,
                "result": "\n\n".join(result_parts),
                "metadata": {
                    "language": lang,
                    "exit_code": remote_result.exit_code,
                    "stdout_length": len(stdout),
                    "stderr_length": len(stderr),
                    "duration_ms": remote_result.duration_ms,
                    "sandbox_mode": "remote",
                },
            }
        except Exception as exc:
            if sandbox_executor.mode == "remote":
                return {"success": False, "error": f"远程沙盒执行失败: {exc}"}
            # auto: fall through to subprocess
            logger.warning("code_execute: remote sandbox failed, using subprocess: %s", exc)

    # ── Resolve working directory ─────────────────────────────────────
    ws_root = get_workspace_root()
    if cwd and cwd.strip() and cwd.strip() != ".":
        safe_cwd = resolve_workspace_path(cwd.strip())
        if safe_cwd is None:
            return {"success": False, "error": f"工作目录 '{cwd}' 超出工作区允许范围"}
        if not safe_cwd.exists():
            safe_cwd.mkdir(parents=True, exist_ok=True)
        work_dir = str(safe_cwd)
        work_dir_rel = str(safe_cwd.relative_to(ws_root))
    else:
        work_dir = str(ws_root)
        work_dir_rel = "."

    # ── Detect install commands → use longer timeout ─────────────────
    code_stripped = code.strip()
    is_install_cmd = _is_install_command(code_stripped, lang)
    effective_timeout = min(
        timeout,
        CODE_EXECUTE_INSTALL_TIMEOUT if is_install_cmd else CODE_EXECUTE_TIMEOUT,
    )

    # ── Create .agenthub_exec/ scratch dir inside workspace ───────────
    # Scripts are written here (not system /tmp) so they sit alongside
    # workspace files and can import sibling modules naturally.
    exec_dir = ws_root / ".agenthub_exec"
    try:
        exec_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        exec_dir = Path(tempfile.mkdtemp(prefix="agenthub_exec_"))

    try:
        if lang == "python":
            # ── Python execution ────────────────────────────────────
            script_path = exec_dir / "script.py"
            await awrite_text(script_path, code, encoding="utf-8")
            cmd = _build_python_cmd(ws_root, script_path)
        else:
            # ── Bash execution ──────────────────────────────────────
            if _is_one_liner(code_stripped):
                # Single command (e.g. "pip install flask", "npm install"):
                # run directly via bash -c in the workspace.
                script_path = None
                cmd = ["bash", "-lc", code_stripped]
            else:
                script_path = exec_dir / "script.sh"
                await awrite_text(script_path, code, encoding="utf-8")
                cmd = ["bash", str(script_path)]

        proc = await _run_subprocess(cmd, effective_timeout, cwd=work_dir)

        stdout = sandbox_executor.sanitize_output(proc.get("stdout", ""))[:MAX_CODE_OUTPUT_CHARS]
        stderr = sandbox_executor.sanitize_output(proc.get("stderr", ""))[:MAX_CODE_OUTPUT_CHARS]
        exit_code = proc.get("exit_code", -1)

        result_parts: list[str] = []
        if stdout:
            result_parts.append(f"[标准输出]\n{stdout}")
        if stderr:
            result_parts.append(f"[标准错误]\n{stderr}")
        if exit_code != 0:
            result_parts.append(f"[退出码: {exit_code}]")
        if not result_parts:
            result_parts.append("[无输出]")

        metadata: dict[str, Any] = {
            "language": lang,
            "exit_code": exit_code,
            "stdout_length": len(stdout),
            "stderr_length": len(stderr),
            "timeout_seconds": effective_timeout,
            "cwd": work_dir_rel,
            "is_install": is_install_cmd,
        }

        return {
            "success": exit_code == 0,
            "result": "\n\n".join(result_parts),
            "metadata": metadata,
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"代码执行超时 ({effective_timeout}秒){' (安装命令)' if is_install_cmd else ''}",
            "metadata": {
                "language": lang,
                "timeout_seconds": effective_timeout,
                "cwd": work_dir_rel,
                "is_install": is_install_cmd,
            },
        }
    except Exception as exc:
        logger.exception("code_execute failed")
        return {"success": False, "error": f"代码执行异常: {exc}"}
    finally:
        # Clean up the script file (leave exec_dir for future runs)
        if lang == "python" and script_path:
            try:
                script_path.unlink(missing_ok=True)
            except OSError:
                pass
        elif lang == "bash" and script_path:
            try:
                script_path.unlink(missing_ok=True)
            except OSError:
                pass


def _is_install_command(code: str, lang: str) -> bool:
    """Detect whether *code* is a dependency installation command."""
    first_line = code.split("\n")[0].strip().lower()
    install_prefixes = (
        "pip install", "pip3 install", "python -m pip install",
        "npm install", "npm i ", "npm ci",
        "yarn add", "yarn install",
        "pnpm install", "pnpm add",
        "poetry install", "poetry add",
        "conda install",
        "gem install",
        "cargo install", "cargo add",
        "go get", "go install",
    )
    return any(first_line.startswith(p) for p in install_prefixes)


def _is_one_liner(code: str) -> bool:
    """Heuristic: is *code* a single shell command (not a multi-line script)?"""
    lines = [l for l in code.split("\n") if l.strip() and not l.strip().startswith("#")]
    if len(lines) > 1:
        return False
    # If there are no common script markers (shebang, function, if/while/for),
    # treat it as a single command.
    script_keywords = ("#!/", "function ", "if ", "while ", "for ", "case ", "do ", "then ")
    combined = "\n".join(lines)
    return not any(combined.strip().startswith(kw) for kw in script_keywords)


def _build_python_cmd(ws_root: Path, script_path: Path) -> list[str]:
    """Build the python command, auto-activating workspace venv if present."""
    # Check for workspace .venv
    venv_python = ws_root / ".venv" / "Scripts" / "python.exe"  # Windows
    if not venv_python.exists():
        venv_python = ws_root / ".venv" / "bin" / "python"       # Unix
    if venv_python.exists():
        return [str(venv_python), str(script_path)]
    return ["python", str(script_path)]


async def _run_subprocess(cmd: list[str], timeout: int, cwd: str) -> dict[str, Any]:
    """Run a subprocess with timeout and return stdout/stderr/exit_code."""
    proc = None
    try:
        proc = await __import__("asyncio").create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
        )
        stdout_bytes, stderr_bytes = await __import__("asyncio").wait_for(
            proc.communicate(), timeout=timeout
        )
        return {
            "stdout": stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else "",
            "stderr": stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else "",
            "exit_code": proc.returncode if proc.returncode is not None else -1,
        }
    except __import__("asyncio").TimeoutError:
        if proc:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        raise subprocess.TimeoutExpired(cmd, timeout)


# ── file_search ────────────────────────────────────────────────────────
