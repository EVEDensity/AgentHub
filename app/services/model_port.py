from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any, Protocol

from app.services.harness_service import (
    FunctionCall,
    FunctionResult,
    FunctionTool,
    HarnessRequest,
    ModelPort,
    ModelResponse,
    ModelUsage,
)


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


class ModelAdapterPort(ModelPort):
    """Adapt the existing stateless prompt adapters to Harness ModelPort."""

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
        self._adapter = adapter
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._system_prompt = system_prompt
        self._tools = list(tools) if tools is not None else None
        self._prompt_token_cost = prompt_token_cost
        self._completion_token_cost = completion_token_cost

    async def complete(
        self,
        request: HarnessRequest,
        tool_results: tuple[FunctionResult, ...],
        *,
        tools_enabled: bool = True,
    ) -> ModelResponse:
        prompt = _render_prompt(request.code, tool_results)
        raw = await self._adapter.execute_prompt(
            prompt,
            self._model,
            self._api_key,
            self._base_url,
            system_prompt=self._system_prompt,
            tools=self._tools if tools_enabled else None,
        )
        response = normalize_model_response(raw)
        return ModelResponse(
            content=response.content,
            tool_calls=response.tool_calls,
            usage=self._usage(),
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


def _render_prompt(prompt: str, tool_results: tuple[FunctionResult, ...]) -> str:
    if not tool_results:
        return prompt
    rendered = [
        {
            "callId": result.call_id,
            "name": result.name,
            "success": result.success,
            "content": result.content,
        }
        for result in tool_results
    ]
    return f"{prompt}\n\nTool results:\n{json.dumps(rendered, ensure_ascii=False, sort_keys=True)}"


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def normalize_model_response(raw: object) -> ModelResponse:
    """Normalize plain text, internal JSON, or OpenAI-shaped responses."""
    if isinstance(raw, Mapping):
        return _normalize_mapping(raw)
    if not isinstance(raw, str):
        raise TypeError("model adapter returned a non-text response")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ModelResponse(content=raw)
    if isinstance(payload, Mapping):
        return _normalize_mapping(payload)
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


def _normalize_mapping(payload: Mapping[str, Any]) -> ModelResponse:
    if not any(key in payload for key in ("choices", "content", "tool_calls")):
        return ModelResponse(content=json.dumps(payload, ensure_ascii=False, sort_keys=True))
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        message = choices[0].get("message")
        if isinstance(message, Mapping):
            return _normalize_mapping(message)

    content = payload.get("content", "")
    if content is None:
        content = ""
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, sort_keys=True)

    calls = payload.get("tool_calls", [])
    if not isinstance(calls, list):
        calls = []
    normalized: list[FunctionCall] = []
    for index, call in enumerate(calls):
        parsed = _normalize_call(call, index)
        if parsed is not None:
            normalized.append(parsed)
    return ModelResponse(content=content, tool_calls=tuple(normalized))


def _normalize_call(raw: object, index: int) -> FunctionCall | None:
    if not isinstance(raw, Mapping):
        return None
    function = raw.get("function")
    source = function if isinstance(function, Mapping) else raw
    name = source.get("name")
    if not isinstance(name, str):
        return None
    call_id = raw.get("id", f"call-{index + 1}")
    if not isinstance(call_id, str):
        call_id = f"call-{index + 1}"
    arguments = source.get("arguments", {})
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {"__raw_arguments__": arguments}
    if not isinstance(arguments, Mapping):
        arguments = {"__raw_arguments__": arguments}
    return FunctionCall(id=call_id, name=name, arguments=arguments)


__all__ = [
    "ModelAdapterPort",
    "PromptAdapterPort",
    "build_function_tool_schemas",
    "normalize_model_response",
]
