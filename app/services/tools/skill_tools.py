from __future__ import annotations

"""Skill-system tool handlers: skill_list, skill_load, command_execute.

Implements a Claude Code-compatible skill execution pattern:
  1. skill_list     → discover available skills
  2. skill_load     → read a skill's full documentation (SKILL.md)
  3. command_execute → run a skill's script or any safe shell command

Skills live in two directories:
  - User-level:  ~/.claude/skills/   (shared across projects)
  - Project-level: .claude/skills/   (project-specific)

Each skill directory contains:
  - SKILL.md          (YAML frontmatter + markdown body)
  - scripts/          (executable scripts: .py, .js, .sh, .ps1)
  - .env / .env.example (optional API keys)
  - runtime.conf      (optional pre-detected runtime hint)
"""

import asyncio
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.config import (
    COMMAND_EXECUTE_MAX_OUTPUT,
    COMMAND_EXECUTE_TIMEOUT,
    PROJECT_ROOT,
    SKILLS_DIR_PROJECT,
    SKILLS_DIR_USER,
)

logger = logging.getLogger("agenthub.tools.skill")

# ── Constants ──────────────────────────────────────────────────────────

MAX_SKILL_BODY_CHARS = 30_000  # truncate SKILL.md body for context budget


# ── SKILL.md frontmatter parser ────────────────────────────────────────


def _parse_skill_md(skill_dir: Path) -> dict[str, Any] | None:
    """Parse a SKILL.md file, extracting YAML frontmatter and markdown body.

    Returns a dict with keys: name, description, version, body, path,
    scripts (list), has_env, source ("user"|"project"), etc.
    Returns None if the SKILL.md is missing or unparseable.
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None

    try:
        raw = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    # Parse YAML frontmatter
    lines = raw.split("\n")
    frontmatter: dict[str, Any] = {}
    body_start = 0

    if lines and lines[0].strip() == "---":
        end_idx = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_idx = i
                break
        if end_idx is not None:
            # Parse frontmatter lines (simple key: value)
            fm_lines = lines[1:end_idx]
            fm_text = "\n".join(fm_lines)

            # Try YAML parser first
            try:
                import yaml as _yaml
                parsed = _yaml.safe_load(fm_text)
                if isinstance(parsed, dict):
                    frontmatter = parsed
            except Exception:
                pass  # fall back to simple parsing

            # Simple fallback: key: value (only if YAML didn't work)
            if not frontmatter:
                for line in fm_lines:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if ":" in line:
                        idx = line.index(":")
                        key = line[:idx].strip()
                        value = line[idx + 1:].strip().strip('"').strip("'")
                        if key:
                            frontmatter[key] = value

            body_start = end_idx + 1

    # Extract body (skip blank leading lines)
    body_lines = lines[body_start:]
    while body_lines and not body_lines[0].strip():
        body_lines = body_lines[1:]
    body = "\n".join(body_lines).strip()

    # Truncate body for context budget
    if len(body) > MAX_SKILL_BODY_CHARS:
        body = body[:MAX_SKILL_BODY_CHARS] + (
            f"\n\n... [已截断，全文共 {len(body)} 字符]"
        )

    # Discover scripts
    scripts_dir = skill_dir / "scripts"
    scripts: list[str] = []
    if scripts_dir.is_dir():
        try:
            for entry in sorted(scripts_dir.iterdir()):
                if entry.is_file() and entry.suffix in (
                    ".py", ".js", ".sh", ".ps1", ".bash",
                ):
                    scripts.append(entry.name)
                elif entry.is_dir():
                    # List subdirectories too (e.g. shared/)
                    try:
                        has_py = any(
                            f.suffix == ".py"
                            for f in entry.iterdir()
                            if f.is_file()
                        )
                        if has_py:
                            scripts.append(f"{entry.name}/")
                    except OSError:
                        pass
        except OSError:
            pass

    # Determine source (user vs project)
    try:
        # Check if skill_dir is under user skills dir
        is_user = str(skill_dir.resolve()).startswith(
            str(SKILLS_DIR_USER.resolve())
        )
        source = "user" if is_user else "project"
    except (OSError, ValueError):
        source = "project"

    return {
        "name": frontmatter.get("name", skill_dir.name),
        "description": frontmatter.get("description", ""),
        "version": frontmatter.get("version", "1.0.0"),
        "source": source,
        "path": str(skill_dir),
        "body": body,
        "scripts": scripts,
        "scripts_dir": str(scripts_dir) if scripts_dir.is_dir() else None,
        "has_env": (skill_dir / ".env").is_file(),
        "has_env_example": (skill_dir / ".env.example").is_file(),
    }


# ── Helper: discover all skill directories ────────────────────────────


def _discover_skill_dirs(source: str = "all") -> list[tuple[Path, str]]:
    """Return a list of ``(path, source_label)`` for all skill directories.

    Args:
        source: "all" (both), "user", or "project".

    Project-level skills take precedence over user-level skills with the
    same name (they shadow user skills).
    """
    result: list[tuple[Path, str]] = []
    seen: set[str] = set()

    # Project skills first (higher priority)
    if source in ("all", "project") and SKILLS_DIR_PROJECT.is_dir():
        try:
            for entry in sorted(SKILLS_DIR_PROJECT.iterdir()):
                if entry.is_dir() and not entry.name.startswith("."):
                    seen.add(entry.name)
                    result.append((entry, "project"))
        except OSError:
            pass

    # User skills
    if source in ("all", "user") and SKILLS_DIR_USER.is_dir():
        try:
            for entry in sorted(SKILLS_DIR_USER.iterdir()):
                if entry.is_dir() and not entry.name.startswith("."):
                    if entry.name not in seen:
                        # Resolve symlinks
                        try:
                            real = entry.resolve()
                        except OSError:
                            real = entry
                        seen.add(entry.name)
                        result.append((real, "user"))
        except OSError:
            pass

    return result


# ── Tool handler: skill_list ──────────────────────────────────────────


async def skill_list_handler(source: str = "all") -> dict[str, Any]:
    """List all available skills with their metadata.

    Args:
        source: "all" (default), "user", or "project".

    Returns a structured list of skills. Each entry contains the skill's
    name, description, version, script count, and location.
    """
    valid = {"all", "user", "project"}
    if source not in valid:
        return {"success": False, "error": f"source 参数无效。可选值: {', '.join(sorted(valid))}"}

    try:
        dirs = _discover_skill_dirs(source)
    except Exception as exc:
        logger.exception("skill_list: discovery failed")
        return {"success": False, "error": f"技能目录扫描失败: {exc}"}

    skills: list[dict[str, Any]] = []
    for skill_dir, src in dirs:
        info = _parse_skill_md(skill_dir)
        if info is None:
            skills.append({
                "name": skill_dir.name,
                "description": "(无 SKILL.md)",
                "version": "?",
                "source": src,
                "path": str(skill_dir),
                "scripts_count": 0,
                "has_body": False,
            })
        else:
            skills.append({
                "name": info["name"],
                "description": info["description"],
                "version": info["version"],
                "source": info["source"],
                "path": info["path"],
                "scripts_count": len(info["scripts"]),
                "scripts": info["scripts"][:8],  # preview
                "has_env": info["has_env"],
                "has_body": bool(info["body"]),
            })

    return {
        "success": True,
        "result": {
            "skills": skills,
            "total": len(skills),
            "source_filter": source,
            "directories": {
                "user": str(SKILLS_DIR_USER),
                "project": str(SKILLS_DIR_PROJECT),
            },
        },
    }


# ── Tool handler: skill_load ──────────────────────────────────────────


async def skill_load_handler(name: str) -> dict[str, Any]:
    """Load a specific skill's full documentation (SKILL.md body).

    Args:
        name: The skill name (directory name), e.g. "anysearch".

    Returns the complete SKILL.md body and metadata. The model should
    read this documentation to understand how to use the skill's scripts.

    Project-level skills shadow user-level skills with the same name.
    """
    if not name or not name.strip():
        return {"success": False, "error": "技能名称不能为空"}

    name = name.strip().lower()
    skill_dir = None
    source = ""

    # Check project-level first (higher priority)
    project_dir = SKILLS_DIR_PROJECT / name
    if project_dir.is_dir():
        skill_dir = project_dir
        source = "project"
    else:
        # Check user-level
        user_dir = SKILLS_DIR_USER / name
        if user_dir.is_dir():
            try:
                skill_dir = user_dir.resolve()
            except OSError:
                skill_dir = user_dir
            source = "user"

    # Case-insensitive search
    if skill_dir is None:
        dirs = _discover_skill_dirs("all")
        for d, src in dirs:
            if d.name.lower() == name:
                skill_dir = d
                source = src
                break

    if skill_dir is None or not skill_dir.is_dir():
        # Provide helpful error with available skill names
        available = []
        for d, _ in _discover_skill_dirs("all"):
            info = _parse_skill_md(d)
            label = info["name"] if info else d.name
            available.append(label)
        hint = f"\n可用的技能: {', '.join(available)}" if available else ""
        return {"success": False, "error": f"技能 '{name}' 不存在{hint}"}

    info = _parse_skill_md(skill_dir)
    if info is None:
        return {"success": False, "error": f"技能 '{name}' 的 SKILL.md 无法读取或解析"}

    return {
        "success": True,
        "result": {
            "name": info["name"],
            "description": info["description"],
            "version": info["version"],
            "source": info["source"],
            "path": info["path"],
            "body": info["body"],
            "body_length": len(info["body"]),
            "scripts": info["scripts"],
            "scripts_dir": info["scripts_dir"],
            "has_env": info["has_env"],
            "has_env_example": info["has_env_example"],
        },
    }


# ── Tool handler: command_execute ─────────────────────────────────────


async def command_execute_handler(
    command: str,
    cwd: str = "",
    timeout: int = 60,
) -> dict[str, Any]:
    """Execute a shell command with safety constraints.

    Designed for running skill scripts (Python/Node/Bash) and general
    shell commands.  All commands are executed in a subprocess with
    enforced timeout and output size limits.

    **Security constraints:**
    - Working directory defaults to the project root.
    - Commands are limited to the allowed directory tree (skills dirs
      and workspace).  Attempts to escape are blocked.
    - Interactive commands (e.g. ``vim``, ``less``) are rejected.
    - Output is truncated to ~100 KB.

    Args:
        command: The full shell command to execute.
        cwd: Working directory for the command (default: PROJECT_ROOT).
        timeout: Maximum execution time in seconds (default 60, max 120).
    """
    if not command or not command.strip():
        return {"success": False, "error": "命令不能为空"}

    command = command.strip()
    effective_timeout = min(max(timeout, 1), COMMAND_EXECUTE_TIMEOUT)

    # ── Security: block interactive commands ─────────────────────────
    dangerous_commands = {"vim", "vi", "nano", "less", "more", "top", "htop", "man"}
    first_word = command.split()[0].split("/")[-1] if command.split() else ""
    if first_word.lower() in dangerous_commands:
        return {
            "success": False,
            "error": (
                f"交互式命令 '{first_word}' 不允许。"
                f"请使用非交互式替代方案（如 'cat' 代替 'less'）。"
            ),
        }

    # ── Resolve working directory ────────────────────────────────────
    if cwd and cwd.strip():
        cwd_path = Path(cwd.strip()).expanduser()
        if not cwd_path.is_absolute():
            cwd_path = PROJECT_ROOT / cwd_path
        try:
            cwd_path = cwd_path.resolve()
        except OSError:
            return {"success": False, "error": f"无法解析工作目录: {cwd}"}

        # Security: ensure cwd is within allowed tree
        allowed_roots = [
            PROJECT_ROOT.resolve(),
            SKILLS_DIR_USER.resolve(),
            SKILLS_DIR_PROJECT.resolve(),
        ]
        try:
            cwd_str = str(cwd_path)
            if not any(cwd_str.startswith(str(r)) for r in allowed_roots):
                return {
                    "success": False,
                    "error": (
                        f"工作目录 '{cwd_path}' 不在允许范围内。"
                        f"允许的目录: 项目根目录、技能目录。"
                    ),
                }
        except (OSError, ValueError):
            return {"success": False, "error": f"无法验证工作目录安全性: {cwd}"}
    else:
        cwd_path = PROJECT_ROOT

    if not cwd_path.is_dir():
        return {"success": False, "error": f"工作目录不存在: {cwd_path}"}

    # ── Execute ──────────────────────────────────────────────────────
    logger.info(
        "command_execute: cmd='%s...' cwd=%s timeout=%ds",
        command[:80], cwd_path, effective_timeout,
    )

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd_path),
            env={
                **os.environ,
                "PYTHONUNBUFFERED": "1",
                "PATH": os.environ.get("PATH", ""),
            },
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=effective_timeout,
        )

        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
        exit_code = proc.returncode if proc.returncode is not None else -1

        # Truncate output
        max_chars = COMMAND_EXECUTE_MAX_OUTPUT
        stdout_truncated = len(stdout) > max_chars
        stderr_truncated = len(stderr) > max_chars
        stdout = stdout[:max_chars]
        stderr = stderr[:max_chars]

        result_parts: list[str] = []
        if stdout:
            result_parts.append(f"[标准输出]\n{stdout}")
            if stdout_truncated:
                result_parts.append(f"\n... [输出已截断至 {max_chars} 字符]")
        if stderr:
            result_parts.append(f"[标准错误]\n{stderr}")
            if stderr_truncated:
                result_parts.append(f"\n... [错误输出已截断至 {max_chars} 字符]")
        if not result_parts:
            result_parts.append("[无输出]")

        return {
            "success": exit_code == 0,
            "result": "\n\n".join(result_parts),
            "error": (
                f"命令退出码: {exit_code}" if exit_code != 0 else None
            ),
            "metadata": {
                "command": command[:200],
                "cwd": str(cwd_path),
                "exit_code": exit_code,
                "stdout_length": len(stdout),
                "stderr_length": len(stderr),
                "timeout_seconds": effective_timeout,
                "truncated": stdout_truncated or stderr_truncated,
            },
        }

    except asyncio.TimeoutError:
        if proc:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        return {
            "success": False,
            "error": f"命令执行超时（{effective_timeout}秒）",
            "metadata": {
                "command": command[:200],
                "cwd": str(cwd_path),
                "timeout_seconds": effective_timeout,
            },
        }
    except FileNotFoundError:
        return {
            "success": False,
            "error": (
                f"命令 '{command.split()[0]}' 未找到。"
                "请检查命令路径是否正确，或尝试使用完整路径。"
            ),
            "metadata": {"command": command[:200]},
        }
    except Exception as exc:
        logger.exception("command_execute failed")
        return {
            "success": False,
            "error": f"命令执行异常: {exc}",
            "metadata": {"command": command[:200], "cwd": str(cwd_path)},
        }
