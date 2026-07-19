from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from app.services.memory.models import (
    CognitiveMemoryType,
    MemoryDocument,
    MemoryHeader,
    MemoryMeta,
    MemoryScope,
    MemoryType,
)


_LAZY_EXPORTS = {
    "MemoryStorage": ("app.services.memory.storage", "MemoryStorage"),
    "MemoryScanner": ("app.services.memory.scanner", "MemoryScanner"),
    "MemoryExtractor": ("app.services.memory.extractor", "MemoryExtractor"),
    "SessionMemoryManager": ("app.services.memory.session_memory", "SessionMemoryManager"),
    "SessionMemoryStore": ("app.services.memory.session_store", "SessionMemoryStore"),
    "SessionMemoryInfo": ("app.services.memory.session_store", "SessionMemoryInfo"),
    "MemoryConsolidator": ("app.services.memory.consolidator", "MemoryConsolidator"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


if TYPE_CHECKING:
    from app.services.memory.consolidator import MemoryConsolidator
    from app.services.memory.extractor import MemoryExtractor
    from app.services.memory.scanner import MemoryScanner
    from app.services.memory.session_memory import SessionMemoryManager
    from app.services.memory.session_store import SessionMemoryInfo, SessionMemoryStore
    from app.services.memory.storage import MemoryStorage


__all__ = [
    "CognitiveMemoryType",
    "MemoryConsolidator",
    "MemoryDocument",
    "MemoryExtractor",
    "MemoryHeader",
    "MemoryMeta",
    "MemoryScanner",
    "MemoryScope",
    "MemoryStorage",
    "MemoryType",
    "SessionMemoryInfo",
    "SessionMemoryManager",
    "SessionMemoryStore",
]
