from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.services.memory.models import (
    MEMORY_TYPE_DESCRIPTIONS,
    MemoryDocument,
    MemoryHeader,
    MemoryMeta,
    MemoryType,
    sanitize_filename,
)


class MemoryStorage:
    """File-based CRUD operations for memory files in a project-local .claude/memory dir.

    Directory layout:
        .claude/memory/
            MEMORY.md              # Index file
            <name>.md              # Individual memory files
    """

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir).resolve()
        self._ensure_dir()

    # ── public helpers ──────────────────────────────────────────────

    @property
    def base(self) -> Path:
        return self._base

    @property
    def index_path(self) -> Path:
        return self._base / "MEMORY.md"

    # ── lifecycle ───────────────────────────────────────────────────

    def _ensure_dir(self) -> None:
        self._base.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self._write_index([])

    # ── CRUD ────────────────────────────────────────────────────────

    def save(
        self,
        name: str,
        description: str,
        type_: MemoryType,
        body: str = "",
        filename: str | None = None,
    ) -> MemoryDocument:
        """Create or overwrite a memory file."""
        fname = filename or sanitize_filename(name)
        path = self._base / fname
        now = datetime.now().isoformat(timespec="seconds")

        existing = None
        if path.exists():
            existing = MemoryDocument.parse(path.read_text(encoding="utf-8"), str(path))
            created_at = existing.meta.created_at or now
        else:
            created_at = now

        meta = MemoryMeta(
            name=name,
            description=description,
            type=type_,
            created_at=created_at,
            updated_at=now,
        )
        doc = MemoryDocument(meta=meta, body=body, file_path=str(path))
        path.write_text(doc.to_markdown(), encoding="utf-8")
        self._refresh_index()
        return doc

    def get(self, filename: str) -> Optional[MemoryDocument]:
        """Read a single memory file by filename (e.g. 'user_role.md')."""
        path = self._resolve(filename)
        if not path or not path.exists():
            return None
        content = path.read_text(encoding="utf-8")
        return MemoryDocument.parse(content, str(path))

    def delete(self, filename: str) -> bool:
        """Delete a memory file by filename."""
        path = self._resolve(filename)
        if not path or not path.exists():
            return False
        path.unlink()
        self._refresh_index()
        return True

    def list_headers(self, max_files: int = 200) -> list[MemoryHeader]:
        """Scan all .md files (except MEMORY.md) and return their headers.

        Mimics scanMemoryFiles() from the architecture doc:
          - max 200 files
          - reads first 30 lines for frontmatter
          - sorted by mtime, newest first
        """
        results: list[MemoryHeader] = []
        for child in sorted(self._base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not child.name.endswith(".md") or child.name == "MEMORY.md":
                continue
            if len(results) >= max_files:
                break
            try:
                stat = child.stat()
                # Read first 30 lines for frontmatter
                head = self._read_head(child, 30)
                meta = MemoryMeta.from_frontmatter(head, child.name) if head else None
                if meta is None:
                    meta = MemoryMeta(name=child.stem, description="", type=MemoryType.REFERENCE)

                results.append(
                    MemoryHeader(
                        filename=child.name,
                        path=str(child),
                        mtime=stat.st_mtime,
                        description=meta.description,
                        type=meta.type,
                        name=meta.name,
                        created_at=meta.created_at,
                        updated_at=meta.updated_at,
                    )
                )
            except (OSError, ValueError):
                continue
        return results

    # ── MEMORY.md index ─────────────────────────────────────────────

    def get_index_content(self) -> str:
        """Read the current MEMORY.md index file."""
        if self.index_path.exists():
            return self.index_path.read_text(encoding="utf-8")
        return ""

    def rebuild_index(self) -> str:
        """Rebuild MEMORY.md from current files. Returns the index content."""
        headers = self.list_headers()
        self._write_index(headers)
        return self.get_index_content()

    def _refresh_index(self) -> None:
        """Silently refresh index without returning content."""
        headers = self.list_headers()
        self._write_index(headers)

    def _write_index(self, headers: list[MemoryHeader]) -> None:
        """Generate and write the MEMORY.md index file."""
        lines = [
            "# 记忆索引 (Memory Index)",
            "",
            f"*最后更新: {datetime.now().isoformat(timespec='seconds')}*",
            "",
            "## 概述",
            "",
            "本目录存储跨会话的持久化记忆。每一条记忆是一个 `.md` 文件，",
            "包含 YAML frontmatter（name, description, type）。",
            "",
            "| 文件名 | 名称 | 类型 | 描述 | 更新于 |",
            "|--------|------|------|------|--------|",
        ]
        for h in headers:
            updated = h.updated_at or datetime.fromtimestamp(h.mtime).isoformat(timespec="seconds") if h.mtime else "-"
            # Truncate description for table
            desc = h.description[:60] + "…" if len(h.description) > 60 else h.description
            lines.append(f"| {h.filename} | {h.name} | {h.type.value} | {desc} | {updated} |")

        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("### 记忆类型说明")
        lines.append("")
        for mt, desc in MEMORY_TYPE_DESCRIPTIONS.items():
            lines.append(f"- **{mt.value}**: {desc}")
        lines.append("")
        lines.append("> 此文件由系统自动维护。手动修改可能被覆盖。")

        content = "\n".join(lines)

        # Truncation protection: max 200 lines, 25KB
        MAX_LINES = 200
        MAX_BYTES = 25_000
        cl_lines = content.split("\n")
        if len(cl_lines) > MAX_LINES or len(content.encode("utf-8")) > MAX_BYTES:
            # Truncate table rows if too long
            cl_lines = cl_lines[:MAX_LINES]
            cl_lines.append("")
            cl_lines.append("> ⚠️ 索引已截断（超过限制）。")

        truncated = "\n".join(cl_lines)
        self.index_path.write_text(truncated, encoding="utf-8")

    # ── internals ───────────────────────────────────────────────────

    def _resolve(self, filename: str) -> Path | None:
        """Resolve a filename to an absolute path, with path-traversal protection."""
        p = self._base / filename
        try:
            p = p.resolve()
        except (OSError, RuntimeError):
            return None
        # Ensure it's still under the base dir
        if not str(p).startswith(str(self._base)):
            return None
        return p

    def _read_head(self, path: Path, n: int) -> str:
        """Read first n lines of a file efficiently."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines: list[str] = []
                for _ in range(n):
                    line = f.readline()
                    if not line:
                        break
                    lines.append(line)
                return "".join(lines)
        except (OSError, UnicodeDecodeError):
            return ""
