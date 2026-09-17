"""Lightweight, optional source-code indexing for developer tooling.

The index is an acceleration layer only. Callers must fall back to file
search/glob whenever it is unavailable or stale.
"""

from app.services.code_index.incremental_indexer import IndexReport, IncrementalIndexer
from app.services.code_index.index_store import CodeIndexStore
from app.services.code_index.service import CodeIndexService
from app.services.code_index.symbol_extractor import (
    ExtractionResult,
    ImportReference,
    Reference,
    Symbol,
    extract_symbols,
)

__all__ = [
    "CodeIndexService",
    "CodeIndexStore",
    "ExtractionResult",
    "ImportReference",
    "IncrementalIndexer",
    "IndexReport",
    "Reference",
    "Symbol",
    "extract_symbols",
]
