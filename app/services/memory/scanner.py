from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional

from app.services.memory.models import MemoryHeader, MemoryType
from app.services.memory.storage import MemoryStorage


class MemoryScanner:
    """Memory scanning and freshness tracking.

    Mimics memoryScan.ts and memoryAge.ts from the architecture document.
    """

    def __init__(self, storage: MemoryStorage) -> None:
        self._storage = storage

    # ── scanning ────────────────────────────────────────────────────

    def scan(self, max_files: int = 200) -> list[MemoryHeader]:
        """Scan all memory files and return headers sorted by mtime (newest first)."""
        return self._storage.list_headers(max_files=max_files)

    def filter_by_type(self, type_: MemoryType, max_files: int = 200) -> list[MemoryHeader]:
        """Return only memories of a specific type."""
        return [h for h in self.scan(max_files=max_files) if h.type == type_]

    def format_manifest(self, headers: Optional[list[MemoryHeader]] = None) -> str:
        """Format scan results as a text manifest (formatMemoryManifest equivalent)."""
        if headers is None:
            headers = self.scan()
        if not headers:
            return "(无记忆文件)"

        lines = ["可用记忆:", ""]
        for h in headers:
            freshness = self.freshness_text(h.mtime)
            lines.append(
                f"  - {h.filename} | {h.name} | type={h.type.value} | "
                f"{h.description[:50]}{freshness}"
            )
        return "\n".join(lines)

    # ── freshness (memoryAge.ts equivalent) ─────────────────────────

    @staticmethod
    def _age_days(mtime: float) -> int:
        """Calculate how many days ago a file was modified."""
        modified = datetime.fromtimestamp(mtime)
        now = datetime.now()
        delta = now - modified
        return delta.days

    def freshness_text(self, mtime: float) -> str:
        """Return a freshness warning string.

        - 0 days (today): no warning
        - 1 day (yesterday): no warning
        - ≥2 days: append warning
        """
        days = self._age_days(mtime)
        if days >= 2:
            return f" ⚠️ 此记忆已有 {days} 天，请验证是否仍然有效"
        return ""

    def freshness_note(self, mtime: float) -> str:
        """Wrap freshness warning in a system-reminder tag."""
        text = self.freshness_text(mtime)
        if not text:
            return ""
        return f"<system-reminder>{text}</system-reminder>"
