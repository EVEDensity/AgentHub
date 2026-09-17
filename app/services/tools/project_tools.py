"""Read-only project identity inspection tool."""

from __future__ import annotations

from typing import Any

from app.services.project_manifest import ProjectManifest


async def project_inspect_handler(path: str = ".") -> dict[str, Any]:
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    root = get_workspace_root()
    target = root if path in {"", "."} else resolve_workspace_path(path)
    if target is None:
        return {"success": False, "error": f"路径 '{path}' 超出工作区允许范围"}
    if not target.is_dir():
        return {"success": False, "error": f"'{path}' 不是目录"}
    return {
        "success": True,
        "result": ProjectManifest.discover(target).to_dict(),
        "metadata": {"source": "local-filesystem", "readOnly": True},
    }


__all__ = ["project_inspect_handler"]
