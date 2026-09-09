"""Deterministic context compilation for CLI and Mission prompts."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Any

from app.services.context_store import ContextStore
from app.services.model_contract import Message
from app.services.context_compaction import compact_text
from app.services.token_budget import (
    count_tokens,
    tokenizer_backend,
    truncate_to_tokens,
)


@dataclass(frozen=True)
class ContextBudget:
    total_chars: int = 12000
    reserved_output_chars: int = 4000
    total_tokens: int | None = None
    compression_threshold: float = 0.70

    @property
    def input_chars(self) -> int:
        return max(1000, self.total_chars - self.reserved_output_chars)

    def __post_init__(self) -> None:
        if self.total_chars < 1:
            raise ValueError("total_chars must be positive")
        if self.reserved_output_chars < 0:
            raise ValueError("reserved_output_chars must be non-negative")
        if self.total_tokens is not None and self.total_tokens < 1:
            raise ValueError("total_tokens must be positive when supplied")
        if not 0.0 < self.compression_threshold <= 1.0:
            raise ValueError("compression_threshold must be in (0, 1]")


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
    token_count: int = 0

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
    estimated_tokens: int = 0
    compression_triggered: bool = False
    tokenizer_backend: str = "legacy-char-budget"

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
            "estimatedTokens": self.estimated_tokens,
            "compressionTriggered": self.compression_triggered,
            "tokenizerBackend": self.tokenizer_backend,
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
        provider: str = "",
        model: str = "",
        token_budget: int | None = None,
        auto_compress: bool = True,
    ) -> None:
        self.directory = Path(directory)
        self.budget = budget or ContextBudget(total_chars=char_budget)
        self.char_budget = self.budget.input_chars
        self.store = store or ContextStore(self.directory)
        self.provider = provider
        self.model = model
        self.token_budget = token_budget or self.budget.total_tokens
        self.auto_compress = auto_compress

    @property
    def token_mode(self) -> bool:
        """Return whether this compiler should enforce a real token budget.

        Existing callers that only provide the historical character budget keep
        the legacy deterministic behavior. Production callers pass a provider,
        model, or explicit token budget and use the tokenizer-backed path.
        """
        return bool(self.token_budget or self.provider or self.model)

    @staticmethod
    def is_context_length_error(error: BaseException | str) -> bool:
        """Recognize provider context-window failures without provider coupling."""
        text = str(error).lower()
        return any(marker in text for marker in (
            "context_length_exceeded",
            "context length exceeded",
            "maximum context length",
            "too many tokens",
            "prompt is too long",
        ))

    def _effective_token_budget(self) -> int:
        if self.token_budget:
            return int(self.token_budget)
        # Convert the legacy input character ceiling to a conservative token
        # ceiling using the selected provider tokenizer.
        return max(1, count_tokens("x" * self.char_budget, self.provider, self.model))

    @staticmethod
    def _tool_sources(tool_results: Sequence[Mapping[str, Any]]) -> list[ContextSource]:
        """Project tool output into raw, summary and lazy-reference layers."""
        sources: list[ContextSource] = []
        for index, result in enumerate(tool_results):
            name = str(result.get("tool_name") or result.get("name") or "tool")
            call_id = str(result.get("call_id") or result.get("callId") or index)
            raw_value = result.get("result", result.get("content", result.get("error", "")))
            raw = raw_value if isinstance(raw_value, str) else json.dumps(raw_value, ensure_ascii=False, sort_keys=True)
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            success = bool(result.get("success", "error" not in result))
            summary = str(result.get("summary") or result.get("error") or compact_text(raw, max_chars=320))
            reference = f"[tool-result-ref] name={name} call_id={call_id} sha256={digest} chars={len(raw)}"
            sources.extend((
                ContextSource("tool_summary", f"tool-summary:{call_id}", f"{name}: {summary}", 58, reason="structured tool summary"),
                ContextSource("tool_reference", f"tool-reference:{call_id}", reference, 56, reason="lazy tool result reference"),
                ContextSource("tool_raw", f"tool-raw:{call_id}", raw, 52, reason="raw tool result"),
            ))
        return sources

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
        tool_results: Sequence[Mapping[str, Any]] | None = None,
        force_compress: bool = False,
        context_error: BaseException | str | None = None,
    ) -> ContextManifest:
        force_compress = force_compress or (
            context_error is not None and self.is_context_length_error(context_error)
        )
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
        if tool_results:
            candidates.extend(self._tool_sources(tool_results))

        if not self.token_mode:
            return self._compile_legacy(candidates, records)

        token_limit = self._effective_token_budget()
        compression_triggered = force_compress
        total_tokens = sum(count_tokens(item.text, self.provider, self.model) for item in candidates if item.text)
        if force_compress or (
            self.auto_compress
            and total_tokens > int(token_limit * self.budget.compression_threshold)
        ):
            compressed: list[ContextSource] = []
            for source in candidates:
                if not source.text or source.kind in {"policy", "current"}:
                    compressed.append(source)
                    continue
                compacted = compact_text(source.text, max_chars=max(160, min(len(source.text), 1200)))
                if compacted != source.text:
                    compression_triggered = True
                    source = ContextSource(source.kind, source.source_id, compacted, source.priority, reason="auto-compressed")
                compressed.append(source)
            candidates = compressed

        remaining_tokens = token_limit
        selected: list[ContextSource] = []
        omitted: list[str] = []
        for source in sorted(candidates, key=lambda item: -item.priority):
            if not source.text:
                continue
            if remaining_tokens <= 0:
                omitted.append(source.source_id)
                continue
            text, truncated = truncate_to_tokens(source.text, remaining_tokens, self.provider, self.model)
            used = count_tokens(text, self.provider, self.model)
            if not text:
                omitted.append(source.source_id)
                continue
            selected.append(ContextSource(
                source.kind, source.source_id, text, source.priority,
                reason=("budget truncated" if truncated else source.reason),
                token_count=used,
            ))
            if truncated:
                omitted.append(source.source_id)
            remaining_tokens -= used
        # Tokenizers account for separators and message framing differently
        # from the sum of individual source counts. Recheck the rendered
        # manifest and trim the lowest-priority source until the actual model
        # input is within the hard budget.
        while selected:
            rendered = "\n\n".join(item.text for item in selected)
            actual_tokens = count_tokens(rendered, self.provider, self.model)
            if actual_tokens <= token_limit:
                break
            source = selected[-1]
            allowed = max(1, source.token_count - (actual_tokens - token_limit) - 2)
            text, truncated = truncate_to_tokens(source.text, allowed, self.provider, self.model)
            if not truncated or text == source.text:
                omitted.append(source.source_id)
                selected.pop()
                continue
            selected[-1] = ContextSource(
                source.kind,
                source.source_id,
                text,
                source.priority,
                reason="budget truncated",
                token_count=count_tokens(text, self.provider, self.model),
            )
            omitted.append(source.source_id)
        messages = tuple(
            Message(role="user" if source.kind == "current" else "system", content=source.text, source_id=source.source_id)
            for source in selected
        )
        return ContextManifest(
            sources=tuple(selected),
            messages=messages,
            covered_missions=tuple(dict.fromkeys(record.mission_id for record in records if record.mission_id)),
            omitted=tuple(omitted),
            token_budget=token_limit,
            estimated_chars=sum(len(source.text) for source in selected),
            estimated_tokens=sum(source.token_count for source in selected),
            compression_triggered=compression_triggered,
            tokenizer_backend=tokenizer_backend(self.provider, self.model),
        )

    def _compile_legacy(self, candidates: list[ContextSource], records: list[Any]) -> ContextManifest:
        """Preserve the historical character-budget contract for old callers."""
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
            estimated_tokens=count_tokens("\n\n".join(item.text for item in selected), self.provider, self.model),
            tokenizer_backend="legacy-char-budget",
        )


__all__ = ["ContextBudget", "ContextCompiler", "ContextManifest", "ContextSource"]
