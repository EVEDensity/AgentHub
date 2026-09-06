"""Bounded developer workflow tools: symbols, tests, quality and diagnostics."""

from __future__ import annotations

import ast
import asyncio
import json
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

MAX_OUTPUT = 8000


def _workspace_path(value: str = ".") -> tuple[Path | None, Path]:
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path
    root = get_workspace_root()
    return (resolve_workspace_path(value) if value != "." else root), root


async def ast_symbols_handler(path: str, include_private: bool = False) -> dict[str, Any]:
    safe, root = _workspace_path(path)
    if safe is None or not safe.is_file():
        return {"success": False, "error": f"文件不存在或超出工作区: {path}"}
    if safe.suffix != ".py":
        return {"success": False, "error": "ast_symbols 当前支持 Python 文件"}
    try:
        tree = ast.parse(safe.read_text(encoding="utf-8"), filename=str(safe))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        return {"success": False, "error": f"Python AST 解析失败: {exc}"}
    symbols: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not include_private and node.name.startswith("_"):
                continue
            symbols.append({"name": node.name, "kind": "class" if isinstance(node, ast.ClassDef) else "function", "line": node.lineno, "end_line": getattr(node, "end_lineno", node.lineno)})
    symbols.sort(key=lambda item: (item["line"], item["name"]))
    return {"success": True, "result": {"path": str(safe.relative_to(root)), "symbols": symbols}}


async def test_discover_handler(path: str = ".") -> dict[str, Any]:
    safe, root = _workspace_path(path)
    if safe is None or not safe.exists():
        return {"success": False, "error": f"路径不存在或超出工作区: {path}"}
    files: list[str] = []
    for candidate in safe.rglob("*"):
        if not candidate.is_file() or any(part.startswith(".") for part in candidate.relative_to(root).parts):
            continue
        name = candidate.name.lower()
        if name.startswith("test_") or name.endswith("_test.py") or ".test." in name or name.endswith(".spec.ts") or name.endswith(".spec.tsx"):
            files.append(str(candidate.relative_to(root)).replace("\\", "/"))
    scripts: dict[str, Any] = {}
    package_json = root / "package.json"
    if package_json.is_file():
        try:
            payload = json.loads(package_json.read_text(encoding="utf-8"))
            scripts = payload.get("scripts", {}) if isinstance(payload.get("scripts"), dict) else {}
        except (OSError, ValueError):
            scripts = {}
    return {"success": True, "result": {"path": str(safe.relative_to(root)), "test_files": sorted(files)[:500], "scripts": scripts, "pytest_available": shutil.which("pytest") is not None}}


async def formatter_handler(path: str = ".", formatter: str = "auto", check: bool = True) -> dict[str, Any]:
    safe, root = _workspace_path(path)
    if safe is None:
        return {"success": False, "error": f"路径超出工作区: {path}"}
    if formatter == "auto":
        formatter = "ruff" if safe.suffix == ".py" and shutil.which("ruff") else "prettier" if shutil.which("prettier") else ""
    commands = {"ruff": ["ruff", "format"], "black": ["black"], "prettier": ["prettier", "--write"]}
    if formatter not in commands or shutil.which(commands[formatter][0]) is None:
        return {"success": False, "error": "未找到可用 formatter；支持 ruff、black、prettier"}
    target = str(safe)
    args = [commands[formatter][0], "format" if formatter == "ruff" else "--check" if check and formatter == "black" else "--check" if check and formatter == "prettier" else "--write", target] if formatter != "ruff" else ["ruff", "format", "--check" if check else "--diff", target]
    if not check and formatter == "prettier":
        args = ["prettier", "--write", target]
    proc = await asyncio.create_subprocess_exec(*args, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = await proc.communicate()
    text = (out + err).decode("utf-8", errors="replace")[:MAX_OUTPUT]
    return {"success": proc.returncode == 0, "result": text or "格式检查通过", "metadata": {"formatter": formatter, "check": check, "exit_code": proc.returncode}}


async def type_check_handler(path: str = ".", checker: str = "auto") -> dict[str, Any]:
    safe, root = _workspace_path(path)
    if safe is None:
        return {"success": False, "error": f"路径超出工作区: {path}"}
    if checker == "auto":
        checker = "mypy" if shutil.which("mypy") else "pyright" if shutil.which("pyright") else "tsc" if shutil.which("tsc") else ""
    executable = {"mypy": "mypy", "pyright": "pyright", "tsc": "tsc"}.get(checker)
    if not executable or shutil.which(executable) is None:
        return {"success": False, "error": "未找到类型检查器；支持 mypy、pyright、tsc"}
    args = [executable, str(safe)] if checker != "tsc" else [executable, "--noEmit"]
    proc = await asyncio.create_subprocess_exec(*args, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = await proc.communicate()
    return {"success": proc.returncode == 0, "result": (out + err).decode("utf-8", errors="replace")[:MAX_OUTPUT], "metadata": {"checker": checker, "exit_code": proc.returncode}}


async def package_manager_handler(manager: str, action: str = "list", package: str = "", apply: bool = False) -> dict[str, Any]:
    allowed = {"npm": {"list", "install", "update"}, "pip": {"list", "install", "update"}, "pnpm": {"list", "install", "update"}, "yarn": {"list", "install", "update"}}
    if manager not in allowed or action not in allowed[manager]:
        return {"success": False, "error": "仅支持 npm/pnpm/yarn/pip 的 list/install/update"}
    if action in {"install", "update"} and not apply:
        return {"success": True, "result": "这是预览模式；传 apply=true 才会修改依赖", "metadata": {"dry_run": True, "manager": manager, "action": action, "package": package}}
    executable = shutil.which(manager)
    if executable is None:
        return {"success": False, "error": f"未找到 {manager} 可执行文件"}
    args = [manager, "list"] if action == "list" else [manager, action] + ([package] if package else [])
    proc = await asyncio.create_subprocess_exec(*args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = await proc.communicate()
    return {"success": proc.returncode == 0, "result": (out + err).decode("utf-8", errors="replace")[:MAX_OUTPUT], "metadata": {"manager": manager, "action": action, "dry_run": False, "exit_code": proc.returncode}}


async def log_tail_handler(path: str, lines: int = 100) -> dict[str, Any]:
    safe, root = _workspace_path(path)
    if safe is None or not safe.is_file():
        return {"success": False, "error": f"日志文件不存在或超出工作区: {path}"}
    try:
        content = safe.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, min(lines, 1000)):]
    except OSError as exc:
        return {"success": False, "error": f"读取日志失败: {exc}"}
    return {"success": True, "result": "\n".join(content), "metadata": {"path": str(safe.relative_to(root)), "lines": len(content)}}


async def process_list_handler() -> dict[str, Any]:
    if shutil.which("tasklist"):
        args = ["tasklist", "/FO", "CSV", "/NH"]
    elif shutil.which("ps"):
        args = ["ps", "-eo", "pid,comm,%cpu,%mem", "--no-headers"]
    else:
        return {"success": False, "error": "系统不支持进程列表命令"}
    proc = await asyncio.create_subprocess_exec(*args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = await proc.communicate()
    return {"success": proc.returncode == 0, "result": (out + err).decode("utf-8", errors="replace")[:MAX_OUTPUT]}


async def port_check_handler(host: str = "127.0.0.1", port: int = 80, timeout: float = 1.0) -> dict[str, Any]:
    try:
        with socket.create_connection((host, int(port)), timeout=max(0.1, min(timeout, 5.0))):
            return {"success": True, "result": f"{host}:{port} 可连接", "metadata": {"open": True}}
    except OSError as exc:
        return {"success": True, "result": f"{host}:{port} 不可连接: {exc}", "metadata": {"open": False}}


async def service_health_handler(url: str, timeout: float = 5.0) -> dict[str, Any]:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=max(0.1, min(timeout, 15.0))) as client:
            response = await client.get(url)
        return {"success": response.is_success, "result": response.text[:MAX_OUTPUT], "metadata": {"status_code": response.status_code, "url": url}}
    except httpx.HTTPError as exc:
        return {"success": False, "error": f"服务健康检查失败: {exc}"}


async def change_plan_handler(changes: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(changes, list) or not changes:
        return {"success": False, "error": "changes 必须是非空数组"}
    plan = [{"step": i + 1, "path": str(item.get("path") or ""), "operation": str(item.get("operation") or "write"), "verification": str(item.get("verification") or "lint/test/diff")} for i, item in enumerate(changes) if isinstance(item, dict)]
    if len(plan) != len(changes) or any(not item["path"] for item in plan):
        return {"success": False, "error": "每个变更必须包含 path"}
    return {"success": True, "result": {"steps": plan, "count": len(plan)}}


async def audit_report_handler(attempt_id: str = "") -> dict[str, Any]:
    """Aggregate persisted Attempt manifests and restore audits."""
    from app.services.workspace_context import get_workspace_root
    root = get_workspace_root()
    store = root / ".agenthub" / "attempt-snapshots"
    stores = [store / attempt_id] if attempt_id else sorted(store.glob("att-*"))
    reports: list[dict[str, Any]] = []
    for item in stores:
        if not item.is_dir():
            continue
        report: dict[str, Any] = {"attempt_id": item.name}
        for filename, key in (("manifest.json", "manifest"), ("restore-audit.json", "restore")):
            path = item / filename
            if path.is_file():
                try:
                    report[key] = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    report[key] = {"error": "invalid json"}
        reports.append(report)
    return {"success": True, "result": {"attempts": reports, "count": len(reports)}}


__all__ = [name for name in globals() if name.endswith("_handler")]
