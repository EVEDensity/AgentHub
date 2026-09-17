"""Memory context provider for Planner + ReflectiveHarness.

Wraps AgentHub's existing session memory + receipts infrastructure into
a single async interface that the planning and reflection layers can call
before any tool runs.  Three tiers are retrieved in order of cost:

1. **Session transcript** (L0) — raw chat history, truncated to a few
   thousand characters.  Cheap and always available.

2. **Project facts / summaries** (L1) — key-value facts the session has
   already established.  Medium cost.

3. **Evidence receipts** (L2) — receipts for prior Artifacts/Evidence the
   mission has already produced.  More expensive (FTS-backed), cached per
   planner invocation.

Each tier is bounded to a character budget so we never blow past the
planner's own context window.  When a tier is empty (fresh session, no
receipts yet) it is simply omitted — no error.

The :class:`MemoryContextProvider` is a *protocol* with two implementations:

* :class:`NullMemoryContextProvider` — the safe default when Mission
  Control is unreachable or no memory subsystem is wired up.  Returns
  empty context but never raises.

* :class:`BoundMemoryContextProvider` — takes concrete session memory
  + receipts instances and produces real context strings.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("agenthub.memory.context")


# ── Protocol ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MemoryContextBundle:
    """Tiered memory context ready to be embedded in a prompt."""

    session_transcript: str = ""
    project_facts: str = ""
    evidence_receipts: str = ""

    @property
    def is_empty(self) -> bool:
        return not (
            self.session_transcript or self.project_facts or self.evidence_receipts
        )

    def format_for_prompt(self) -> str:
        """Render the non-empty tiers into a prompt-ready block."""
        sections: list[str] = []
        if self.session_transcript:
            sections.append(f"[Recent Session Transcript]\n{self.session_transcript}")
        if self.project_facts:
            sections.append(f"[Project Facts]\n{self.project_facts}")
        if self.evidence_receipts:
            sections.append(f"[Prior Evidence Receipts]\n{self.evidence_receipts}")
        return "\n\n".join(sections)


@runtime_checkable
class MemoryContextProvider(Protocol):
    """Any component that can assemble a :class:`MemoryContextBundle`.

    The protocol is deliberately synchronous — planners and harnesses are
    already in async territory; the provider itself does not need to be.
    """

    def get_context(self, *, mission_id: str | None = None) -> MemoryContextBundle: ...


# ── Null safe default ─────────────────────────────────────────────────


class NullMemoryContextProvider:
    """No-op provider used when memory wiring is missing.

    Always returns an empty bundle without raising.  This keeps the planner
    usable in unit tests, offline dev shells, and any environment where the
    memory subsystem has not been provisioned.
    """

    def get_context(self, *, mission_id: str | None = None) -> MemoryContextBundle:
        return MemoryContextBundle()


# ── Bound implementation ──────────────────────────────────────────────


@dataclass
class BoundMemoryContextProvider:
    """Concrete provider backed by real session memory + receipts.

    All dependencies are optional — the provider gracefully skips tiers
    whose backing store is missing.  This means you can wire up just
    session memory in dev and leave receipts to production without
    touching callers.
    """

    session_memory: Any | None = None  # SessionMemoryManager
    receipt_formatter: Any | None = None  # receipts.format_receipts_as_context
    max_transcript_chars: int = 2_000
    max_facts_chars: int = 1_500
    max_receipts_chars: int = 1_500

    def get_context(self, *, mission_id: str | None = None) -> MemoryContextBundle:
        transcript = self._transcript()
        facts = self._facts()
        receipts = self._receipts(mission_id)
        return MemoryContextBundle(
            session_transcript=transcript,
            project_facts=facts,
            evidence_receipts=receipts,
        )

    def _transcript(self) -> str:
        if self.session_memory is None:
            return ""
        try:
            manager = self.session_memory
            if hasattr(manager, "_build_transcript"):
                result = manager._build_transcript([], self.max_transcript_chars)
            else:
                return ""
        except Exception as exc:  # noqa: BLE001 - memory failure is silent
            logger.debug("memory: transcript unavailable (%s)", exc)
            return ""
        text = str(result or "")
        if len(text) > self.max_transcript_chars:
            text = text[: self.max_transcript_chars] + "…"
        return text

    def _facts(self) -> str:
        # Project facts live in SessionMemoryManager but there is no
        # dedicated accessor yet.  This hook is left for L1 summary
        # service to populate once it lands.
        return ""

    def _receipts(self, mission_id: str | None) -> str:
        if self.receipt_formatter is None or mission_id is None:
            return ""
        try:
            formatter = self.receipt_formatter
            if callable(formatter):
                result = formatter(mission_id=mission_id, limit=5)
            else:
                return ""
        except Exception as exc:  # noqa: BLE001 - receipts failure is silent
            logger.debug("memory: receipts unavailable (%s)", exc)
            return ""
        text = str(result or "")
        if len(text) > self.max_receipts_chars:
            text = text[: self.max_receipts_chars] + "…"
        return text


__all__ = [
    "BoundMemoryContextProvider",
    "MemoryContextBundle",
    "MemoryContextProvider",
    "NullMemoryContextProvider",
]