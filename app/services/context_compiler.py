"""Deterministic context compilation for CLI and Mission prompts."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

@dataclass(frozen=True)
class ContextSource:
    kind: str
    source_id: str
    text: str
    priority: int
    included: bool = True
    reason: str = ""

@dataclass(frozen=True)
class ContextManifest:
    sources: tuple[ContextSource, ...] = ()
    token_budget: int = 0
    estimated_chars: int = 0

    def render(self) -> str:
        return "\n\n".join(s.text for s in sorted(self.sources, key=lambda x: -x.priority) if s.included and s.text)

    def to_dict(self) -> dict[str, Any]:
        return {"sources": [s.__dict__ for s in self.sources], "tokenBudget": self.token_budget, "estimatedChars": self.estimated_chars}

class ContextCompiler:
    """Single read boundary; callers provide optional Mission/facts layers."""
    def __init__(self, directory: Path, *, char_budget: int = 12000) -> None:
        self.directory = Path(directory)
        self.char_budget = max(1000, int(char_budget))

    def compile(self, *, current: str = "", conversation: str = "", mission: str = "", compact: str = "", facts: str = "", memory: str = "") -> ContextManifest:
        candidates = [
            ContextSource("current", "current", current, 100, reason="active request"),
            ContextSource("conversation", "conversation", conversation, 90, reason="recent session"),
            ContextSource("mission", "mission", mission, 80, reason="resume chain"),
            ContextSource("compact", "compact", compact, 70, reason="compressed history"),
            ContextSource("project_facts", "project-facts", facts, 60, reason="keyword match"),
            ContextSource("memory", "memory", memory, 40, reason="long-term fallback"),
        ]
        remaining = self.char_budget
        selected: list[ContextSource] = []
        for source in candidates:
            if not source.text or remaining <= 0:
                continue
            text = source.text[:remaining]
            selected.append(ContextSource(source.kind, source.source_id, text, source.priority, reason=source.reason if len(text) == len(source.text) else "budget truncated"))
            remaining -= len(text)
        return ContextManifest(tuple(selected), token_budget=self.char_budget // 4, estimated_chars=self.char_budget - remaining)

__all__ = ["ContextCompiler", "ContextManifest", "ContextSource"]
