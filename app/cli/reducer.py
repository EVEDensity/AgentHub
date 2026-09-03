"""Pure reducer for CLI streaming view state.

Renderers consume this state; they never mutate Mission/WorkUnit truth.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from app.cli.events import CliEvent


@dataclass(frozen=True)
class ToolView:
    name: str
    status: str
    output: str = ""


@dataclass(frozen=True)
class SessionViewState:
    status: str = ""
    assistant_text: str = ""
    tools: tuple[ToolView, ...] = ()
    pending_decision: dict[str, Any] | None = None
    verification_status: str = ""
    event_count: int = 0


def reduce_event(state: SessionViewState, event: CliEvent) -> SessionViewState:
    """Apply one normalized event idempotently at the renderer boundary."""
    kind = event.event_type
    payload = event.payload
    status = event.status or state.status
    text = state.assistant_text
    if kind == "assistant.delta":
        text += event.text_delta or ""
    decision = state.pending_decision
    if kind == "decision.pending":
        decision = dict(payload.get("decision", payload))
    elif kind in {"decision.resolved", "decision.expired"}:
        decision = None
    tools = list(state.tools)
    if kind.startswith("tool."):
        name = str(payload.get("toolName") or payload.get("tool_name") or "unknown")
        index = next((i for i, item in enumerate(tools) if item.name == name and item.status != "completed"), None)
        if index is None:
            tools.append(ToolView(name=name, status=kind.removeprefix("tool."), output=str(payload.get("text") or "")))
        else:
            current = tools[index]
            tools[index] = replace(current, status=kind.removeprefix("tool."), output=(str(payload.get("text") or "") or current.output))
    verification = state.verification_status
    if kind in {"verification.started", "verification.completed"}:
        verification = kind.removeprefix("verification.")
    return replace(
        state,
        status=status,
        assistant_text=text,
        tools=tuple(tools),
        pending_decision=decision,
        verification_status=verification,
        event_count=state.event_count + 1,
    )


__all__ = ["SessionViewState", "ToolView", "reduce_event"]
