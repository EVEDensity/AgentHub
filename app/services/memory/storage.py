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
from app.utils.async_file import (
    aread_text,
    awrite_text,
    aexists,
    aunlink,
    astat_mtime,
    aiterdir,
    amkdir,
    acopy,
    aread_bytes,
    awrite_bytes,
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
        # Minimal sync mkdir — __init__ cannot be async.
        self._base.mkdir(parents=True, exist_ok=True)

    # ── public helpers ──────────────────────────────────────────────

    @property
    def base(self) -> Path:
        return self._base

    @property
    def index_path(self) -> Path:
        return self._base / "MEMORY.md"

    # ── lifecycle ───────────────────────────────────────────────────

    async def _ensure_dir(self) -> None:
        await amkdir(self._base)
        if not await aexists(self.index_path):
            await self._write_index([])

    # ── CRUD ────────────────────────────────────────────────────────

    async def save(
        self,
        name: str,
        description: str,
        type_: MemoryType,
        body: str = "",
        filename: str | None = None,
    ) -> MemoryDocument:
        """Create or overwrite a memory file."""
        await self._ensure_dir()
        fname = filename or sanitize_filename(name)
        path = self._base / fname
        now = datetime.now().isoformat(timespec="seconds")

        existing = None
        if await aexists(path):
            content = await aread_text(path)
            existing = MemoryDocument.parse(content, str(path))
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
        await awrite_text(path, doc.to_markdown())
        await self._refresh_index()
        return doc

    async def get(self, filename: str) -> Optional[MemoryDocument]:
        """Read a single memory file by filename (e.g. 'user_role.md')."""
        await self._ensure_dir()
        path = self._resolve(filename)
        if not path or not await aexists(path):
            return None
        content = await aread_text(path)
        return MemoryDocument.parse(content, str(path))

    async def delete(self, filename: str) -> bool:
        """Move a memory file to trash (30-day recovery window)."""
        await self._ensure_dir()
        path = self._resolve(filename)
        if not path or not await aexists(path):
            return False
        await self._trash_file(path, filename)
        await self._refresh_index()
        return True

    async def permanent_delete(self, filename: str) -> bool:
        """Permanently delete a memory file (no recovery)."""
        await self._ensure_dir()
        path = self._resolve(filename)
        if not path or not await aexists(path):
            return False
        await aunlink(path)
        await self._refresh_index()
        return True

    # ── Trash / Recovery ──────────────────────────────────────────

    @property
    def trash_dir(self) -> Path:
        return self._base / ".trash"

    @property
    def trash_manifest_path(self) -> Path:
        return self.trash_dir / "_manifest.json"

    async def _ensure_trash_dir(self) -> None:
        await amkdir(self.trash_dir)

    async def _trash_file(self, src: Path, original_name: str) -> None:
        """Move a file to the trash directory with a deletion timestamp prefix."""
        await self._ensure_trash_dir()
        import json as _json

        ts = datetime.now().isoformat(timespec="seconds")
        trash_name = f"{ts}__{original_name}"
        dst = self.trash_dir / trash_name
        await acopy(src, dst)
        await aunlink(src)

        # Update manifest
        manifest = await self._read_trash_manifest()
        manifest[trash_name] = {
            "original_name": original_name,
            "deleted_at": ts,
        }
        await self._write_trash_manifest(manifest)

    async def _read_trash_manifest(self) -> dict:
        await self._ensure_trash_dir()
        if not await aexists(self.trash_manifest_path):
            return {}
        try:
            raw = await aread_bytes(self.trash_manifest_path)
            import json as _json
            return _json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    async def _write_trash_manifest(self, manifest: dict) -> None:
        await self._ensure_trash_dir()
        import json as _json
        data = _json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        await awrite_bytes(self.trash_manifest_path, data)

    async def list_trash(self) -> list[dict]:
        """List all files in the trash with deletion time and days remaining."""
        await self._ensure_trash_dir()
        manifest = await self._read_trash_manifest()
        now = datetime.now()
        results: list[dict] = []

        children = await aiterdir(self.trash_dir)
        for child in children:
            if child.name == "_manifest.json":
                continue
            fname = child.name
            meta = manifest.get(fname, {})
            deleted_at_str = meta.get("deleted_at", "")
            try:
                deleted_at = datetime.fromisoformat(deleted_at_str)
            except (ValueError, TypeError):
                deleted_at = datetime.fromtimestamp(await astat_mtime(child))
            days_elapsed = (now - deleted_at).total_seconds() / 86400.0
            days_remaining = max(0.0, 30.0 - days_elapsed)
            results.append({
                "trash_name": fname,
                "original_name": meta.get("original_name", fname),
                "deleted_at": deleted_at_str,
                "days_elapsed": round(days_elapsed, 2),
                "days_remaining": round(days_remaining, 2),
                "expired": days_remaining <= 0,
            })

        results.sort(key=lambda r: r["deleted_at"], reverse=True)
        return results

    async def recover_from_trash(self, trash_name: str) -> bool:
        """Recover a file from trash back to the main memory directory."""
        await self._ensure_trash_dir()
        src = self.trash_dir / trash_name
        if not await aexists(src):
            return False

        manifest = await self._read_trash_manifest()
        meta = manifest.get(trash_name, {})
        original_name = meta.get("original_name", trash_name)

        # Remove timestamp prefix from filename if present
        # Format: 2026-06-06T12:00:00__original.md
        if "__" in trash_name:
            _, _, original_name = trash_name.partition("__")

        dst = self._base / original_name
        # If a file with the same name already exists, append a suffix
        if await aexists(dst):
            base, ext = original_name.rsplit(".", 1) if "." in original_name else (original_name, "md")
            dst = self._base / f"{base}_recovered.{ext}"

        await acopy(src, dst)
        await aunlink(src)

        # Remove from manifest
        manifest.pop(trash_name, None)
        await self._write_trash_manifest(manifest)
        await self._refresh_index()
        return True

    async def purge_trash_item(self, trash_name: str) -> bool:
        """Permanently delete a file from trash."""
        await self._ensure_trash_dir()
        path = self.trash_dir / trash_name
        if not await aexists(path):
            return False
        await aunlink(path)
        manifest = await self._read_trash_manifest()
        manifest.pop(trash_name, None)
        await self._write_trash_manifest(manifest)
        return True

    async def cleanup_trash(self, retention_days: int = 30) -> int:
        """Permanently delete trash files older than retention_days. Returns count of purged files."""
        await self._ensure_trash_dir()
        manifest = await self._read_trash_manifest()
        now = datetime.now()
        purged = 0

        children = await aiterdir(self.trash_dir)
        for child in children:
            if child.name == "_manifest.json":
                continue
            meta = manifest.get(child.name, {})
            deleted_at_str = meta.get("deleted_at", "")
            try:
                deleted_at = datetime.fromisoformat(deleted_at_str)
            except (ValueError, TypeError):
                deleted_at = datetime.fromtimestamp(await astat_mtime(child))
            days_elapsed = (now - deleted_at).total_seconds() / 86400.0
            if days_elapsed >= retention_days:
                await aunlink(child)
                manifest.pop(child.name, None)
                purged += 1

        await self._write_trash_manifest(manifest)
        return purged

    async def list_headers(self, max_files: int = 200) -> list[MemoryHeader]:
        """Scan all .md files (except MEMORY.md) and return their headers.

        Mimics scanMemoryFiles() from the architecture doc:
          - max 200 files
          - reads first 30 lines for frontmatter
          - sorted by mtime, newest first
        """
        await self._ensure_dir()
        results: list[MemoryHeader] = []
        children = await aiterdir(self._base)
        # Pre-compute mtimes asynchronously, then sort newest first
        children_with_mtime: list[tuple[Path, float]] = []
        for child in children:
            mtime = await astat_mtime(child)
            children_with_mtime.append((child, mtime))
        children_with_mtime.sort(key=lambda x: x[1], reverse=True)
        for child, mtime in children_with_mtime:
            if not child.name.endswith(".md") or child.name == "MEMORY.md":
                continue
            if len(results) >= max_files:
                break
            try:
                # Read first 30 lines for frontmatter
                head = await self._read_head(child, 30)
                meta = MemoryMeta.from_frontmatter(head, child.name) if head else None
                if meta is None:
                    meta = MemoryMeta(name=child.stem, description="", type=MemoryType.REFERENCE)

                results.append(
                    MemoryHeader(
                        filename=child.name,
                        path=str(child),
                        mtime=mtime,
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

    async def get_index_content(self) -> str:
        """Read the current MEMORY.md index file."""
        await self._ensure_dir()
        if await aexists(self.index_path):
            return await aread_text(self.index_path)
        return ""

    async def rebuild_index(self) -> str:
        """Rebuild MEMORY.md from current files. Returns the index content."""
        headers = await self.list_headers()
        await self._write_index(headers)
        return await self.get_index_content()

    async def _refresh_index(self) -> None:
        """Silently refresh index without returning content."""
        headers = await self.list_headers()
        await self._write_index(headers)

    async def _write_index(self, headers: list[MemoryHeader]) -> None:
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
        await awrite_text(self.index_path, truncated)

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

    async def _read_head(self, path: Path, n: int) -> str:
        """Read first n lines of a file efficiently."""
        try:
            content = await aread_text(path)
            lines = content.split("\n")[:n]
            return "\n".join(lines) + ("\n" if len(lines) == n and lines else "")
        except (OSError, UnicodeDecodeError, FileNotFoundError):
            return ""
