"""Deterministic context compilation for CLI and Mission prompts."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.context_store import ContextStore
from app.services.model_contract import Message


@dataclass(frozen=True)
class ContextBudget:
    total_chars: int = 12000
    reserved_output_chars: int = 4000

    @property
    def input_chars(self) -> int:
        return max(1000, self.total_chars - self.reserved_output_chars)


@dataclass(frozen=True)
class ContextSource:
    kind: str
    source_id: str
    text: str
    priority: int
    included: bool = True
    reason: str = ""
    # Durable context identity fields.  They are optional for legacy callers
    # but are emitted whenever a ContextStore-backed record supplies them.
    mission_id: str = ""
    event_id: str = ""
    role: str = ""
    created_at: str = ""

    @property
    def source(self) -> str:
        """Production-contract alias for the historical ``kind`` field."""
        return self.kind

    @property
    def content(self) -> str:
        """Production-contract alias for the historical ``text`` field."""
        return self.text


@dataclass(frozen=True)
class ContextManifest:
    sources: tuple[ContextSource, ...] = ()
    messages: tuple[Message, ...] = ()
    covered_missions: tuple[str, ...] = ()
    omitted: tuple[str, ...] = ()
    token_budget: int = 0
    estimated_chars: int = 0

    def render(self) -> str:
        return "\n\n".join(message.content for message in self.messages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sources": [
                {
                    **s.__dict__,
                    "source": s.source,
                    "content": s.content,
                }
                for s in self.sources
            ],
            "messages": [message.__dict__ for message in self.messages],
            "coveredMissions": list(self.covered_missions),
            "omitted": list(self.omitted),
            "tokenBudget": self.token_budget,
            "estimatedChars": self.estimated_chars,
        }


class ContextCompiler:
    """Compile every model-facing context layer through one deterministic boundary."""

    def __init__(
        self,
        directory: Path,
        *,
        char_budget: int = 12000,
        budget: ContextBudget | None = None,
        store: ContextStore | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.budget = budget or ContextBudget(total_chars=char_budget)
        self.char_budget = self.budget.input_chars
        self.store = store or ContextStore(self.directory)

    def compile(
        self,
        *,
        current: str = "",
        conversation: str | None = None,
        mission: str = "",
        compact: str = "",
        policy: str = "",
        project: str = "",
        facts: str = "",
        manifest: str = "",
        memory: str = "",
    ) -> ContextManifest:
        records = self.store.records(limit=40) if conversation is None else []
        if conversation is None:
            conversation = "\n".join(
                f"{record.role}: {record.content}" for record in records
            )
        candidates = [
            ContextSource(
                "policy", "system-policy", policy, 110, reason="runtime policy"
            ),
            ContextSource("current", "current", current, 100, reason="active request"),
            ContextSource(
                "conversation",
                "conversation",
                conversation,
                90,
                reason="recent session",
            ),
            ContextSource("mission", "mission", mission, 80, reason="resume chain"),
            ContextSource(
                "compact", "compact", compact, 70, reason="compressed history"
            ),
            ContextSource(
                "project",
                "project-instructions",
                project,
                65,
                reason="workspace instructions",
            ),
            ContextSource(
                "project_manifest",
                "project-manifest",
                manifest,
                62,
                reason="workspace identity",
            ),
            ContextSource(
                "project_facts",
                "project-facts",
                facts,
                60,
                reason="keyword match",
            ),
            ContextSource("memory", "memory", memory, 40, reason="long-term fallback"),
        ]
        remaining = self.char_budget
        selected: list[ContextSource] = []
        omitted: list[str] = []
        for source in candidates:
            if not source.text:
                continue
            if remaining <= 0:
                omitted.append(source.source_id)
                continue
            text = source.text[:remaining]
            selected.append(
                ContextSource(
                    source.kind,
                    source.source_id,
                    text,
                    source.priority,
                    reason=(
                        source.reason
                        if len(text) == len(source.text)
                        else "budget truncated"
                    ),
                )
            )
            if len(text) != len(source.text):
                omitted.append(source.source_id)
            remaining -= len(text)
        messages = tuple(
            Message(
                role="user" if source.kind == "current" else "system",
                content=source.text,
                source_id=source.source_id,
            )
            for source in sorted(selected, key=lambda item: -item.priority)
        )
        covered_missions = tuple(
            dict.fromkeys(
                record.mission_id for record in records if record.mission_id
            )
        )
        return ContextManifest(
            sources=tuple(selected),
            messages=messages,
            covered_missions=covered_missions,
            omitted=tuple(omitted),
            token_budget=self.budget.total_chars // 4,
            estimated_chars=self.char_budget - remaining,
        )


__all__ = ["ContextBudget", "ContextCompiler", "ContextManifest", "ContextSource"]
