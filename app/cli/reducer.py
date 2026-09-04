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


def state_to_dict(state: SessionViewState) -> dict[str, Any]:
    return {"status": state.status, "assistantText": state.assistant_text, "tools": [{"name": t.name, "status": t.status, "output": t.output} for t in state.tools], "pendingDecision": state.pending_decision, "verificationStatus": state.verification_status, "eventCount": state.event_count}


def state_summary(state: SessionViewState) -> str:
    parts = [state.status] if state.status else []
    if state.tools:
        parts.append(f"tool:{state.tools[-1].name} {state.tools[-1].status}")
    if state.pending_decision is not None:
        parts.append("decision pending")
    if state.verification_status:
        parts.append(f"verification:{state.verification_status}")
    return " · ".join(parts)


def reduce_event(state: SessionViewState, event: CliEvent) -> SessionViewState:
    """Apply one normalized event idempotently at the renderer boundary."""
    kind = event.event_type
    payload = event.payload
    status = event.status or state.status
    if not event.status:
        status = {"mission.created": "CREATED", "work_unit.claimed": "CLAIMED", "work_unit.running": "RUNNING", "mission.completed": "SUCCEEDED"}.get(kind, status)
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


__all__ = ["SessionViewState", "ToolView", "reduce_event", "state_to_dict", "state_summary"]
