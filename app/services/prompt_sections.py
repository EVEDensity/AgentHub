from __future__ import annotations

from datetime import datetime
from pathlib import Path


def build_shared_context(history: str) -> str:
    if not history:
        return ""
    return (
        "【共享会话上下文】\n"
        "先参考以下历史，再按当前角色回答；不要因角色不完全匹配而拒绝。\n\n"
        f"{history}\n"
        "─── 以上为共享记忆，以下是角色指令 ───\n"
    )


def build_collab_section(collab_ctx: str) -> str:
    return f"\n\n{collab_ctx}" if collab_ctx else ""


def build_date_context(now: datetime | None = None) -> str:
    current = now or datetime.now()
    today_str = current.strftime("%Y年%m月%d日")
    weekday_str = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][current.weekday()]
    return f"【当前日期】{today_str} {weekday_str}。涉及今天、最新、最近时以此为准。\n"


def build_workspace_context(root: Path | None = None, *, max_items: int = 20) -> str:
    if root is None:
        from app.services.workspace_context import get_workspace_root

        root = get_workspace_root()

    if not root.exists():
        return ""

    # Every model path receives the same deterministic project identity. The
    # compact file listing below remains a discovery aid; detailed facts are
    # available through the read-only ``project_inspect`` tool.
    from app.services.project_manifest import ProjectManifest

    manifest = ProjectManifest.discover(root)

    lines = [f"工作区: {root}", "可用文件工具: file_read/file_write/file_write_batch/file_edit/file_patch/file_search/file_glob/mkdir/code_execute"]
    try:
        items = sorted(root.iterdir(), key=lambda p: (p.is_dir(), p.name.lower()))[:max_items]
        for path in items:
            kind = "dir" if path.is_dir() else "file"
            size = ""
            if path.is_file():
                try:
                    bytes_size = path.stat().st_size
                    size = f" ({bytes_size:,} bytes)" if bytes_size < 1024 else f" ({bytes_size / 1024:.0f} KB)"
                except OSError:
                    pass
            lines.append(f"- {kind}: {path.name}{size}")
        if len(items) >= max_items:
            lines.append("- ... 使用 file_glob 或 file_read 查看更多")
    except OSError:
        pass

    return manifest.to_prompt() + "\n【工作区文件系统】\n" + "\n".join(lines) + "\n"
