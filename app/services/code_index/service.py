"""Fail-open facade for optional code indexing.

Index faults are diagnostics. They never replace the existing file search or
glob implementation and callers receive ``None`` when they should fall back.
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Iterable

from app.services.code_index.incremental_indexer import IncrementalIndexer, IndexReport
from app.services.code_index.index_store import CodeIndexStore

logger = logging.getLogger("agenthub.code_index")


class CodeIndexService:
    def __init__(self, workspace_root: Path, *, database_path: Path | None = None) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.database_path = database_path or self.workspace_root / ".agenthub" / "code-index.sqlite3"
        self._store: CodeIndexStore | None = None
        self._background_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agenthub-index")
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._background_executor.shutdown(wait=True, cancel_futures=True)
        if self._store is not None:
            self._store.close()
            self._store = None

    def update_background(self, changed_paths: Iterable[str | Path] | None = None, *, max_files: int | None = None) -> Future[IndexReport | None]:
        """Schedule best-effort indexing without blocking CLI startup."""
        if self._closed:
            raise RuntimeError("code index service is closed")
        return self._background_executor.submit(self.update, changed_paths, max_files=max_files)

    def update(self, changed_paths: Iterable[str | Path] | None = None, *, max_files: int | None = None) -> IndexReport | None:
        try:
            store = self._store or CodeIndexStore(self.database_path)
            self._store = store
            return IncrementalIndexer(self.workspace_root, store).update(changed_paths, max_files=max_files)
        except (OSError, ValueError, RuntimeError) as exc:
            logger.warning("code index unavailable; use file_search/file_glob fallback: %s", type(exc).__name__)
            return None

    def search_symbols(self, query: str, *, limit: int = 50) -> list[dict[str, object]] | None:
        try:
            store = self._store or CodeIndexStore(self.database_path)
            self._store = store
            return store.search_symbols(query, limit=limit)
        except (OSError, ValueError, RuntimeError) as exc:
            logger.warning("code index query unavailable; use file_search/file_glob fallback: %s", type(exc).__name__)
            return None

    def find_symbol(self, query: str, *, limit: int = 50) -> list[dict[str, object]] | None:
        """Best-effort symbol lookup for tool integrations."""
        return self.search_symbols(query, limit=limit)

    def get_definition(self, symbol: str, *, path: str | None = None) -> list[dict[str, object]] | None:
        try:
            store = self._store or CodeIndexStore(self.database_path)
            self._store = store
            return store.get_definition(symbol, path=path)
        except (OSError, ValueError, RuntimeError):
            logger.warning("code index definition lookup unavailable; use file search fallback")
            return None
