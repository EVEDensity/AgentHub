"""Canonical provider-neutral model contract.

The constructors retain the historical ``id``/``content`` spellings while
accepting the production contract's ``call_id``/``text`` aliases.  This lets
adapters migrate independently without creating a second business protocol.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from collections.abc import AsyncIterator
from typing import Any, Literal, Mapping, Protocol

@dataclass(frozen=True)
class Message:
    role: str
    content: str
    source_id: str = ""


@dataclass(frozen=True)
class ModelUsage:
    """Provider-neutral usage accumulated by the Harness."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0

    def __post_init__(self) -> None:
        values = (self.prompt_tokens, self.completion_tokens)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            raise ValueError("Model usage values must be non-negative")
        if (
            isinstance(self.cost, bool)
            or not isinstance(self.cost, (int, float))
            or not math.isfinite(self.cost)
            or self.cost < 0
        ):
            raise ValueError("Model usage values must be non-negative")

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, other: "ModelUsage") -> "ModelUsage":
        return ModelUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cost=self.cost + other.cost,
        )

    @classmethod
    def from_value(cls, value: object) -> "ModelUsage":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            return cls()
        return cls(
            prompt_tokens=_non_negative_int(value.get("prompt_tokens")),
            completion_tokens=_non_negative_int(value.get("completion_tokens")),
            cost=_non_negative_number(value.get("cost")),
        )


@dataclass(frozen=True, init=False)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any]
    arguments_complete: bool

    def __init__(
        self,
        id: str | None = None,
        name: str = "",
        arguments: Mapping[str, Any] | None = None,
        *,
        call_id: str | None = None,
        arguments_complete: bool = True,
    ) -> None:
        resolved_id = call_id if call_id is not None else id
        if not resolved_id:
            raise ValueError("tool call id/call_id must be non-empty")
        object.__setattr__(self, "id", str(resolved_id))
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "arguments", dict(arguments or {}))
        object.__setattr__(self, "arguments_complete", bool(arguments_complete))

    @property
    def call_id(self) -> str:
        return self.id

@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    success: bool
    content: str

@dataclass(frozen=True)
class ModelRequest:
    messages: tuple[Message, ...]
    model: str = ""
    tools: tuple[Mapping[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    stream: bool = True
    tool_choice: str = "auto"
    timeout_seconds: float = 60.0

@dataclass(frozen=True)
class ModelStreamEvent:
    kind: Literal[
        "text_delta",
        "tool_call_delta",
        "tool_call",
        "tool_result",
        "completed",
        "error",
    ]
    text: str = ""
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    usage: Mapping[str, int | float] = field(default_factory=dict)
    error: Any = None

@dataclass(frozen=True, init=False)
class ModelResponse:
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: ModelUsage = field(default_factory=ModelUsage)

    def __init__(
        self,
        content: str = "",
        tool_calls: tuple[ToolCall, ...] = (),
        usage: ModelUsage | Mapping[str, Any] | None = None,
        *,
        text: str | None = None,
    ) -> None:
        object.__setattr__(self, "content", content if text is None else text)
        object.__setattr__(self, "tool_calls", tuple(tool_calls))
        object.__setattr__(self, "usage", ModelUsage.from_value(usage))

    @property
    def text(self) -> str:
        return self.content


class ModelPort(Protocol):
    """Provider-neutral model boundary for new Harness integrations."""

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]: ...

    async def complete(self, request: ModelRequest) -> ModelResponse: ...


def _non_negative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _non_negative_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    numeric = float(value)
    return numeric if math.isfinite(numeric) and numeric >= 0 else 0.0


__all__ = [
    "Message",
    "ModelPort",
    "ModelRequest",
    "ModelResponse",
    "ModelStreamEvent",
    "ModelUsage",
    "ToolCall",
    "ToolResult",
]
