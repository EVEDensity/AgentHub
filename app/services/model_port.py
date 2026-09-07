from __future__ import annotations

import json
import inspect
import logging
import math
from dataclasses import replace
from collections.abc import Mapping
from typing import Any, Protocol

from app.services.harness_service import FunctionTool, HarnessRequest
from app.services.model_contract import (
    Message,
    ModelPort,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
    ModelUsage,
    ToolCall,
    ToolResult,
)

logger = logging.getLogger(__name__)

# Context budget for the rendered tool-result transcript. Older entries are
# summarized once the rendered prompt exceeds this many characters
# (Codex-style /compact for the function-calling loop).
DEFAULT_CONTEXT_CHAR_BUDGET = 24_000
# Compression knobs: the newest results always stay verbatim; older ones are
# replaced by a one-line summary carrying the first N content characters.
_SUMMARY_HEAD_CHARS = 200
_RECENT_RESULTS_KEPT = 2
_SUMMARY_SUFFIX = "…[已压缩 {omitted} 字符]"


class PromptAdapterPort(Protocol):
    async def execute_prompt(
        self,
        prompt: str,
        model: str,
        api_key: str = "",
        base_url: str = "",
        *,
        system_prompt: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> str: ...

    async def stream_prompt(
        self, prompt: str, model: str, api_key: str = "", base_url: str = "",
        *, system_prompt: str = "", tools: list[dict[str, Any]] | None = None,
    ) -> Any: ...


class ModelAdapterPort(ModelPort):
    """The sole compatibility adapter from canonical DTOs to prompt APIs."""

    def __init__(
        self,
        adapter: PromptAdapterPort,
        *,
        model: str,
        api_key: str = "",
        base_url: str = "",
        system_prompt: str = "",
        tools: list[dict[str, Any]] | None = None,
        prompt_token_cost: float = 0.0,
        completion_token_cost: float = 0.0,
        context_char_budget: int = DEFAULT_CONTEXT_CHAR_BUDGET,
    ) -> None:
        if not model.strip():
            raise ValueError("model must be non-empty")
        if (
            not math.isfinite(prompt_token_cost)
            or prompt_token_cost < 0
            or not math.isfinite(completion_token_cost)
            or completion_token_cost < 0
        ):
            raise ValueError("Model token costs must be non-negative")
        if context_char_budget < 1:
            raise ValueError("context_char_budget must be positive")
        self._adapter = adapter
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._system_prompt = system_prompt
        self._tools = list(tools) if tools is not None else None
        self._prompt_token_cost = prompt_token_cost
        self._completion_token_cost = completion_token_cost
        self._context_char_budget = context_char_budget

    async def complete(
        self,
        request: ModelRequest | HarnessRequest,
        *legacy_args: object,
        **legacy_kwargs: object,
    ) -> ModelResponse:
        request = self._canonical_request(request, legacy_args, legacy_kwargs, stream=False)
        system_prompt, prompt = _render_model_request(
            request,
            configured_system_prompt=self._system_prompt,
            context_char_budget=self._context_char_budget,
        )
        raw = await self._adapter.execute_prompt(
            prompt,
            request.model or self._model,
            self._api_key,
            self._base_url,
            system_prompt=system_prompt,
            tools=list(request.tools) if request.tools else None,
        )
        response = normalize_model_response(raw, strict=True)
        return ModelResponse(
            content=response.content,
            tool_calls=response.tool_calls,
            usage=self._usage(),
        )

    def stream(
        self,
        request: ModelRequest | HarnessRequest,
        *legacy_args: object,
        **legacy_kwargs: object,
    ) -> Any:
        """Yield canonical stream events, preserving structured tool calls."""
        legacy_callback = (
            request.on_text_delta if isinstance(request, HarnessRequest) else None
        )
        canonical = self._canonical_request(
            request, legacy_args, legacy_kwargs, stream=True
        )
        events = self._stream_events(canonical)
        if isinstance(request, HarnessRequest):
            return self._legacy_stream_response(events, legacy_callback)
        return events

    async def _stream_events(self, request: ModelRequest) -> Any:
        if request.tools:
            response = await self.complete(request)
            if response.content:
                yield ModelStreamEvent(kind="text_delta", text=response.content)
            for call in response.tool_calls:
                yield ModelStreamEvent(kind="tool_call", tool_call=call)
            yield ModelStreamEvent(
                kind="completed",
                usage=_usage_mapping(response.usage),
            )
            return
        system_prompt, prompt = _render_model_request(
            request,
            configured_system_prompt=self._system_prompt,
            context_char_budget=self._context_char_budget,
        )
        stream_method = getattr(self._adapter, "stream_prompt", None)
        if not callable(stream_method):
            response = await self.complete(request)
            if response.content:
                yield ModelStreamEvent(kind="text_delta", text=response.content)
            yield ModelStreamEvent(kind="completed", usage=_usage_mapping(response.usage))
            return
        kwargs: dict[str, Any] = {"system_prompt": system_prompt, "tools": None}
        async for chunk in stream_method(
            prompt, request.model or self._model, self._api_key, self._base_url, **kwargs
        ):
            if not chunk:
                continue
            text = str(chunk)
            yield ModelStreamEvent(kind="text_delta", text=text)
        yield ModelStreamEvent(kind="completed", usage=_usage_mapping(self._usage()))

    async def _legacy_stream_response(
        self,
        events: Any,
        callback: Any,
    ) -> ModelResponse:
        chunks: list[str] = []
        calls: list[ToolCall] = []
        usage = ModelUsage()
        async for event in events:
            if event.kind == "text_delta" and event.text:
                chunks.append(event.text)
                if callback is not None:
                    result = callback(event.text)
                    if inspect.isawaitable(result):
                        await result
            elif event.kind == "tool_call" and event.tool_call is not None:
                calls.append(event.tool_call)
            elif event.kind == "completed":
                usage = ModelUsage.from_value(event.usage)
        return ModelResponse(text="".join(chunks), tool_calls=tuple(calls), usage=usage)

    def _canonical_request(
        self,
        request: ModelRequest | HarnessRequest,
        legacy_args: tuple[object, ...],
        legacy_kwargs: Mapping[str, object],
        *,
        stream: bool,
    ) -> ModelRequest:
        if isinstance(request, ModelRequest):
            if legacy_args or legacy_kwargs:
                raise TypeError("canonical ModelRequest does not accept legacy arguments")
            return request
        if len(legacy_args) > 1:
            raise TypeError("legacy model call accepts at most one tool-results argument")
        raw_results = legacy_args[0] if legacy_args else ()
        if not isinstance(raw_results, tuple) or not all(
            isinstance(item, ToolResult) for item in raw_results
        ):
            raise TypeError("legacy tool_results must be a tuple of ToolResult")
        unexpected = set(legacy_kwargs) - {"tools_enabled"}
        if unexpected:
            raise TypeError(f"unexpected legacy model arguments: {sorted(unexpected)}")
        tools_enabled = bool(legacy_kwargs.get("tools_enabled", True))
        messages: list[Message] = [
            Message(role="user", content=request.code, source_id="legacy-harness")
        ]
        for result in raw_results:
            messages.append(
                Message(
                    role="tool",
                    source_id=result.call_id,
                    content=json.dumps(
                        {
                            "callId": result.call_id,
                            "name": result.name,
                            "success": result.success,
                            "content": result.content,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                )
            )
        return ModelRequest(
            messages=tuple(messages),
            model=self._model,
            tools=tuple(self._tools or ()) if tools_enabled else (),
            metadata={"language": request.language},
            stream=stream,
            tool_choice="auto" if tools_enabled else "none",
            timeout_seconds=request.timeout,
        )

    def _usage(self) -> ModelUsage:
        raw_usage = getattr(self._adapter, "last_usage", {})
        if not isinstance(raw_usage, Mapping):
            return ModelUsage()
        prompt_tokens = _non_negative_int(raw_usage.get("prompt_tokens"))
        completion_tokens = _non_negative_int(raw_usage.get("completion_tokens"))
        return ModelUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=(
                prompt_tokens * self._prompt_token_cost
                + completion_tokens * self._completion_token_cost
            ),
        )


def _render_model_request(
    request: ModelRequest,
    *,
    configured_system_prompt: str = "",
    context_char_budget: int | None = None,
) -> tuple[str, str]:
    """Render canonical messages only at the legacy prompt-adapter edge."""
    system_parts = [configured_system_prompt] if configured_system_prompt else []
    conversation: list[str] = []
    tool_entries: list[dict[str, Any]] = []
    for message in request.messages:
        if message.role == "system":
            system_parts.append(message.content)
        elif message.role == "tool":
            try:
                payload = json.loads(message.content)
            except (TypeError, ValueError):
                payload = {"callId": message.source_id, "content": message.content}
            if not isinstance(payload, dict):
                payload = {"callId": message.source_id, "content": payload}
            tool_entries.append(payload)
        else:
            conversation.append(message.content)
    if context_char_budget is not None and tool_entries:
        tool_entries = _compress_rendered_results(tool_entries, context_char_budget)
    if tool_entries:
        conversation.append(
            "Tool results:\n"
            + json.dumps(tool_entries, ensure_ascii=False, sort_keys=True)
        )
    return "\n\n".join(system_parts), "\n\n".join(conversation)


class ContextBoundModelPort(ModelAdapterPort):
    """Bind compiler-produced messages before crossing the provider adapter."""

    def __init__(self, inner: ModelPort, messages: tuple[Message, ...]) -> None:
        self._inner = inner
        self._messages = tuple(messages)
        self._context_char_budget = getattr(
            inner, "_context_char_budget", DEFAULT_CONTEXT_CHAR_BUDGET
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return await self._inner.complete(self._bind(request))

    def stream(self, request: ModelRequest) -> Any:
        return self._inner.stream(self._bind(request))

    def _bind(self, request: ModelRequest) -> ModelRequest:
        return replace(request, messages=self._messages + request.messages)


def _rendered_chars(entries: list[dict[str, Any]]) -> int:
    return len(
        json.dumps(entries, ensure_ascii=False, sort_keys=True)
    )


def _compress_rendered_results(
    entries: list[dict[str, Any]],
    budget: int,
) -> list[dict[str, Any]]:
    """Summarize the oldest tool results once the transcript exceeds budget.

    The newest ``_RECENT_RESULTS_KEPT`` entries always stay verbatim so the
    model keeps the freshest context; older entries are replaced by a
    one-line summary (head characters + omitted count) until the rendered
    transcript fits the budget. Compressing stops as soon as the budget is
    met, and entries too short to shrink are left untouched.
    """
    total = _rendered_chars(entries)
    if total <= budget:
        return entries
    compressible = max(len(entries) - _RECENT_RESULTS_KEPT, 0)
    compressed = list(entries)
    saved_total = 0
    for index in range(compressible):
        if total - saved_total <= budget:
            break
        original = compressed[index]
        content = original["content"]
        head = content[:_SUMMARY_HEAD_CHARS]
        omitted = len(content) - len(head)
        if omitted <= 0:
            continue
        summarized = dict(original)
        summarized["content"] = (
            f"{head}{_SUMMARY_SUFFIX.format(omitted=omitted)}"
        )
        saved = len(json.dumps(original, ensure_ascii=False, sort_keys=True)) - len(
            json.dumps(summarized, ensure_ascii=False, sort_keys=True)
        )
        if saved <= 0:
            continue
        compressed[index] = summarized
        saved_total += saved
    if saved_total > 0:
        logger.debug(
            "compressed %d chars of %d tool-result entries for the model prompt",
            saved_total,
            len(entries),
        )
    return compressed


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _supports_keyword(callable_obj: Any, name: str) -> bool:
    """Return whether a callable accepts a keyword or arbitrary kwargs."""
    try:
        parameters = inspect.signature(callable_obj).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == name or parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def normalize_model_response(raw: object, *, strict: bool = False) -> ModelResponse:
    """Normalize plain text, internal JSON, or OpenAI-shaped responses."""
    if isinstance(raw, Mapping):
        return _normalize_mapping(raw, strict=strict)
    if not isinstance(raw, str):
        raise TypeError("model adapter returned a non-text response")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ModelResponse(content=raw)
    if isinstance(payload, Mapping):
        return _normalize_mapping(payload, strict=strict)
    return ModelResponse(content=raw)


def build_function_tool_schemas(tools: list[FunctionTool]) -> list[dict[str, Any]]:
    """Render the resolved per-run tool set for OpenAI-compatible providers."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": dict(tool.parameters),
            },
        }
        for tool in tools
    ]


def _normalize_mapping(
    payload: Mapping[str, Any], *, strict: bool = False
) -> ModelResponse:
    if not any(key in payload for key in ("choices", "content", "tool_calls")):
        return ModelResponse(content=json.dumps(payload, ensure_ascii=False, sort_keys=True))
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        message = choices[0].get("message")
        if isinstance(message, Mapping):
            return _normalize_mapping(message, strict=strict)

    content = payload.get("content", "")
    if content is None:
        content = ""
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, sort_keys=True)

    calls = payload.get("tool_calls", [])
    if not isinstance(calls, list):
        calls = []
    normalized: list[ToolCall] = []
    for index, call in enumerate(calls):
        parsed = _normalize_call(call, index, strict=strict)
        if parsed is not None:
            normalized.append(parsed)
    return ModelResponse(content=content, tool_calls=tuple(normalized))


def _normalize_call(
    raw: object, index: int, *, strict: bool = False
) -> ToolCall | None:
    if not isinstance(raw, Mapping):
        return None
    function = raw.get("function")
    source = function if isinstance(function, Mapping) else raw
    name = source.get("name")
    if not isinstance(name, str):
        return None
    call_id = raw.get("id")
    if strict and (not isinstance(call_id, str) or not call_id.strip()):
        raise ValueError("provider tool call is missing call_id")
    if not isinstance(call_id, str) or not call_id:
        call_id = f"call-{index + 1}"
    arguments = source.get("arguments", {})
    arguments_complete = True
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {"__raw_arguments__": arguments}
            arguments_complete = False
    if not isinstance(arguments, Mapping):
        arguments = {"__raw_arguments__": arguments}
    return ToolCall(
        call_id=call_id,
        name=name,
        arguments=arguments,
        arguments_complete=arguments_complete,
    )


def _usage_mapping(usage: ModelUsage) -> dict[str, int | float]:
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "cost": usage.cost,
    }


__all__ = [
    "DEFAULT_CONTEXT_CHAR_BUDGET",
    "ContextBoundModelPort",
    "ModelAdapterPort",
    "PromptAdapterPort",
    "build_function_tool_schemas",
    "normalize_model_response",
]
