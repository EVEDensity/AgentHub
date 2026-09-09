"""SQLite persistence for the optional code index."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.services.code_index.symbol_extractor import ExtractionResult


class CodeIndexStore:
    """Own a workspace-local SQLite index without storing source text."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        self._connection.close()

    def _initialize(self) -> None:
        self._connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                file_hash TEXT NOT NULL,
                language TEXT NOT NULL,
                indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS definitions (
                path TEXT NOT NULL,
                symbol TEXT NOT NULL,
                qualified_name TEXT NOT NULL,
                kind TEXT NOT NULL,
                line_start INTEGER NOT NULL,
                line_end INTEGER NOT NULL,
                signature TEXT NOT NULL,
                PRIMARY KEY(path, qualified_name, line_start)
            );
            CREATE TABLE IF NOT EXISTS references_index (
                path TEXT NOT NULL,
                symbol TEXT NOT NULL,
                kind TEXT NOT NULL,
                line INTEGER NOT NULL,
                column_number INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS imports_index (
                path TEXT NOT NULL,
                module TEXT NOT NULL,
                symbol TEXT NOT NULL,
                line INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_definitions_symbol ON definitions(symbol);
            CREATE INDEX IF NOT EXISTS idx_references_symbol ON references_index(symbol);
            CREATE INDEX IF NOT EXISTS idx_imports_module ON imports_index(module);
            """
        )
        self._connection.commit()

    def file_hash(self, relative_path: str) -> str | None:
        row = self._connection.execute("SELECT file_hash FROM files WHERE path=?", (relative_path,)).fetchone()
        return str(row["file_hash"]) if row is not None else None

    def remove_file(self, relative_path: str) -> None:
        with self._connection:
            for table in ("definitions", "references_index", "imports_index", "files"):
                self._connection.execute(f"DELETE FROM {table} WHERE path=?", (relative_path,))

    def replace_file(self, relative_path: str, file_hash: str, result: ExtractionResult) -> None:
        with self._connection:
            self.remove_file(relative_path)
            self._connection.execute(
                "INSERT INTO files(path, file_hash, language) VALUES(?, ?, ?)",
                (relative_path, file_hash, result.language),
            )
            self._connection.executemany(
                """INSERT INTO definitions(path, symbol, qualified_name, kind, line_start, line_end, signature)
                   VALUES(?, ?, ?, ?, ?, ?, ?)""",
                [(relative_path, item.name, item.qualified_name or item.name, item.kind, item.line_start, item.line_end, item.signature) for item in result.symbols],
            )
            self._connection.executemany(
                "INSERT INTO references_index(path, symbol, kind, line, column_number) VALUES(?, ?, ?, ?, ?)",
                [(relative_path, item.name, item.kind, item.line, item.column) for item in result.references],
            )
            self._connection.executemany(
                "INSERT INTO imports_index(path, module, symbol, line) VALUES(?, ?, ?, ?)",
                [(relative_path, item.module, item.name, item.line) for item in result.imports],
            )

    def search_symbols(self, query: str, *, limit: int = 50) -> list[dict[str, object]]:
        if not query.strip():
            return []
        rows = self._connection.execute(
            """SELECT path, symbol, qualified_name, kind, line_start, line_end, signature
               FROM definitions WHERE symbol LIKE ? OR qualified_name LIKE ?
               ORDER BY path, line_start LIMIT ?""",
            (f"%{query}%", f"%{query}%", max(1, min(limit, 200))),
        ).fetchall()
        return [dict(row) for row in rows]

    def search_references(self, symbol: str, *, limit: int = 100) -> list[dict[str, object]]:
        rows = self._connection.execute(
            """SELECT path, symbol, kind, line, column_number FROM references_index
               WHERE symbol=? ORDER BY path, line LIMIT ?""",
            (symbol, max(1, min(limit, 500))),
        ).fetchall()
        return [dict(row) for row in rows]

    def indexed_paths(self) -> set[str]:
        return {str(row["path"]) for row in self._connection.execute("SELECT path FROM files")}

