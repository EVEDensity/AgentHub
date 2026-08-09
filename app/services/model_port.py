from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol

from app.services.harness_service import (
    FunctionCall,
    FunctionResult,
    FunctionTool,
    HarnessRequest,
    ModelPort,
    ModelResponse,
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
    ) -> None:
        if not model.strip():
            raise ValueError("model must be non-empty")
        self._adapter = adapter
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._system_prompt = system_prompt
        self._tools = list(tools) if tools is not None else None

    async def complete(
        self,
        request: HarnessRequest,
        tool_results: tuple[FunctionResult, ...],
    ) -> ModelResponse:
        prompt = _render_prompt(request.code, tool_results)
        raw = await self._adapter.execute_prompt(
            prompt,
            self._model,
            self._api_key,
            self._base_url,
            system_prompt=self._system_prompt,
            tools=self._tools,
        )
        return normalize_model_response(raw)


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
