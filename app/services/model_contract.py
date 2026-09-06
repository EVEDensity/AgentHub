"""Canonical provider-neutral model contract."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any]

@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    success: bool
    content: str

@dataclass(frozen=True)
class ModelRequest:
    messages: tuple[Mapping[str, Any], ...]
    model: str = ""
    tools: tuple[Mapping[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class ModelStreamEvent:
    kind: Literal["text_delta", "tool_call", "tool_result", "completed", "error"]
    text: str = ""
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    error: str | None = None

@dataclass(frozen=True)
class ModelResponse:
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Mapping[str, Any] = field(default_factory=dict)

__all__ = ["ModelRequest", "ModelStreamEvent", "ModelResponse", "ToolCall", "ToolResult"]
