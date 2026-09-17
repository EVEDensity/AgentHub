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

# L0/L1 only (ADR-0107): session summaries, durable conversation files and
# raw file storage. The heavy L2/L3/semantic/procedural layers were removed
# with the web-chat memory decommission.
_LAZY_EXPORTS = {
    "MemoryStorage": ("app.services.memory.storage", "MemoryStorage"),
    "SessionMemoryManager": ("app.services.memory.session_memory", "SessionMemoryManager"),
    "SessionMemoryStore": ("app.services.memory.session_store", "SessionMemoryStore"),
    "SessionMemoryInfo": ("app.services.memory.session_store", "SessionMemoryInfo"),
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
    from app.services.memory.session_memory import SessionMemoryManager
    from app.services.memory.session_store import SessionMemoryInfo, SessionMemoryStore
    from app.services.memory.storage import MemoryStorage


__all__ = [
    "CognitiveMemoryType",
    "MemoryDocument",
    "MemoryHeader",
    "MemoryMeta",
    "MemoryScope",
    "MemoryStorage",
    "MemoryType",
    "SessionMemoryInfo",
    "SessionMemoryManager",
    "SessionMemoryStore",
]