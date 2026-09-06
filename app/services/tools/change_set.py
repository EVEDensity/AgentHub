"""Atomic multi-file change application for the workspace toolchain."""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from app.services.tools._common import validate_expected_sha256


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def apply_change_set_handler(changes: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply several text-file replacements as one recoverable transaction.

    Every item must include ``path``, ``content`` and ``expected_sha256``.
    An empty expected hash means the file must not exist yet.  All files are
    preflighted before the first write; if any write fails, prior bytes and
    modes are restored before returning an error.
    """
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    if not isinstance(changes, list) or not changes:
        return {"success": False, "error": "changes 必须是非空数组"}
    if len(changes) > 20:
        return {"success": False, "error": "一次最多处理 20 个文件"}

    root = get_workspace_root()
    prepared: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(changes):
        if not isinstance(item, dict):
            return {"success": False, "error": f"changes[{index}] 必须是对象"}
        path = str(item.get("path") or "").strip()
        if not path:
            return {"success": False, "error": f"changes[{index}].path 不能为空"}
        if path in seen:
            return {"success": False, "error": f"重复路径: {path}"}
        seen.add(path)
        content = item.get("content")
        if not isinstance(content, str):
            return {"success": False, "error": f"changes[{index}].content 必须是字符串"}
        safe = resolve_workspace_path(path)
        if safe is None:
            return {"success": False, "error": f"路径 '{path}' 超出工作区允许范围"}
        if safe.exists() and safe.is_dir():
            return {"success": False, "error": f"'{path}' 是目录，不能写入"}
        expected = item.get("expected_sha256")
        ok, error = validate_expected_sha256(safe, expected)
        if not ok:
            return {"success": False, "error": error, "error_type": "conflict", "path": path}
        before = safe.read_bytes() if safe.is_file() else None
        prepared.append({
            "path": path,
            "safe": safe,
            "content": content,
            "before": before,
            "mode": safe.stat().st_mode if safe.exists() else None,
        })

    # Keep the transaction journal under the workspace so it never crosses
    # filesystem boundaries during atomic replacement.
    transaction = root / ".agenthub" / "change-transactions" / uuid.uuid4().hex
    transaction.mkdir(parents=True, exist_ok=True)
    written: list[dict[str, Any]] = []
    try:
        for item in prepared:
            safe = item["safe"]
            safe.parent.mkdir(parents=True, exist_ok=True)
            temp_path = transaction / f"{len(written)}.tmp"
            temp_path.write_text(item["content"], encoding="utf-8")
            os.replace(temp_path, safe)
            written.append(item)
            if item["mode"] is not None:
                os.chmod(safe, item["mode"])
        results = []
        for item in prepared:
            data = item["content"].encode("utf-8")
            results.append({
                "path": item["path"],
                "sha256": _digest_bytes(data),
                "size_bytes": len(data),
            })
        return {
            "success": True,
            "result": f"已原子写入 {len(results)} 个文件",
            "metadata": {"transaction_id": transaction.name, "files": results},
        }
    except (OSError, UnicodeError) as exc:
        rollback_errors: list[str] = []
        for item in reversed(written):
            safe = item["safe"]
            try:
                if item["before"] is None:
                    safe.unlink(missing_ok=True)
                else:
                    safe.write_bytes(item["before"])
                    if item["mode"] is not None:
                        os.chmod(safe, item["mode"])
            except OSError:
                rollback_errors.append(item["path"])
        return {
            "success": False,
            "error": f"变更集写入失败，已回滚{('，回滚失败: ' + ', '.join(rollback_errors)) if rollback_errors else ''}: {exc}",
            "error_type": "transaction",
        }
    finally:
        shutil.rmtree(transaction, ignore_errors=True)


__all__ = ["apply_change_set_handler"]
