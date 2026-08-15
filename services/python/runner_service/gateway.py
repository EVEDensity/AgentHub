from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from app.services.harness_service import FunctionTool, ModelPort
from app.services.model_port import ModelAdapterPort, build_function_tool_schemas


class ModelGatewayError(RuntimeError):
    """Raised when the configured AI Gateway cannot return a valid response."""


class StrictOpenAICompatiblePromptAdapter:
    """Bounded, non-streaming OpenAI-compatible AI Gateway adapter."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        endpoint: str,
        access_token: str,
        timeout_seconds: float,
        max_response_bytes: int,
        max_output_tokens: int | None = None,
    ) -> None:
        parsed = httpx.URL(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            raise ValueError("AI Gateway endpoint must be an absolute HTTP(S) URL")
        if not access_token:
            raise ValueError("AI Gateway token must be non-empty")
        if timeout_seconds <= 0:
            raise ValueError("AI Gateway timeout must be positive")
        if max_response_bytes < 1:
            raise ValueError("AI Gateway response limit must be positive")
        self._http_client = http_client
        self._endpoint = endpoint.rstrip("/") + "/chat/completions"
        self._access_token = access_token
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._max_output_tokens = max_output_tokens
        self.last_usage: dict[str, int] = {}

    async def execute_prompt(
        self,
        prompt: str,
        model: str,
        api_key: str = "",
        base_url: str = "",
        *,
        system_prompt: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        if api_key or base_url:
            raise ModelGatewayError("per-request AI Gateway overrides are forbidden")
        if not model.strip() or model.casefold().startswith("mock"):
            raise ModelGatewayError("configured model is not permitted")

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        request_payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            request_payload["tools"] = tools
            request_payload["tool_choice"] = "auto"
        if self._max_output_tokens is not None:
            request_payload["max_tokens"] = self._max_output_tokens

        try:
            async with self._http_client.stream(
                "POST",
                self._endpoint,
                headers={
                    "authorization": f"Bearer {self._access_token}",
                    "content-type": "application/json",
                    "accept": "application/json",
                },
                json=request_payload,
                timeout=self._timeout_seconds,
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise ModelGatewayError(
                        f"AI Gateway returned HTTP {response.status_code}"
                    )
                content_type = response.headers.get("content-type", "")
                if content_type.split(";", 1)[0].strip().casefold() != "application/json":
                    raise ModelGatewayError("AI Gateway returned a non-JSON response")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self._max_response_bytes:
                        raise ModelGatewayError("AI Gateway response exceeded the limit")
        except ModelGatewayError:
            raise
        except httpx.HTTPError as exc:
            raise ModelGatewayError("AI Gateway request failed") from exc

        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelGatewayError("AI Gateway returned invalid JSON") from exc
        self.last_usage = _validate_openai_response(payload)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class OpenAICompatibleModelFactory:
    """Create one model adapter per WorkUnit attempt and resolved tool set."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        endpoint: str,
        access_token: str,
        model: str,
        timeout_seconds: float,
        max_response_bytes: int,
        max_output_tokens: int | None = None,
        system_prompt: str = "",
        prompt_token_cost: float = 0.0,
        completion_token_cost: float = 0.0,
    ) -> None:
        if not model.strip() or model.casefold().startswith("mock"):
            raise ValueError("AI Gateway model must be non-mock")
        self._http_client = http_client
        self._endpoint = endpoint
        self._access_token = access_token
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._max_output_tokens = max_output_tokens
        self._system_prompt = system_prompt
        self._prompt_token_cost = prompt_token_cost
        self._completion_token_cost = completion_token_cost

    def build(self, tools: Sequence[FunctionTool]) -> ModelPort:
        adapter = StrictOpenAICompatiblePromptAdapter(
            self._http_client,
            endpoint=self._endpoint,
            access_token=self._access_token,
            timeout_seconds=self._timeout_seconds,
            max_response_bytes=self._max_response_bytes,
            max_output_tokens=self._max_output_tokens,
        )
        return ModelAdapterPort(
            adapter,
            model=self._model,
            system_prompt=self._system_prompt,
            tools=build_function_tool_schemas(list(tools)),
            prompt_token_cost=self._prompt_token_cost,
            completion_token_cost=self._completion_token_cost,
        )


def _validate_openai_response(payload: object) -> dict[str, int]:
    if not isinstance(payload, Mapping):
        raise ModelGatewayError("AI Gateway returned a non-object response")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelGatewayError("AI Gateway response has no choice")
    first = choices[0]
    if not isinstance(first, Mapping) or not isinstance(first.get("message"), Mapping):
        raise ModelGatewayError("AI Gateway response has no message")
    message = first["message"]
    content = message.get("content")
    tool_calls = message.get("tool_calls", [])
    if content is not None and not isinstance(content, str):
        raise ModelGatewayError("AI Gateway message content is invalid")
    if not isinstance(tool_calls, list):
        raise ModelGatewayError("AI Gateway tool calls are invalid")
    if content in {None, ""} and not tool_calls:
        raise ModelGatewayError("AI Gateway message is empty")
    for tool_call in tool_calls:
        _validate_tool_call(tool_call)

    raw_usage = payload.get("usage", {})
    if raw_usage is None:
        raw_usage = {}
    if not isinstance(raw_usage, Mapping):
        raise ModelGatewayError("AI Gateway usage is invalid")
    usage: dict[str, int] = {}
    for name in ("prompt_tokens", "completion_tokens"):
        value = raw_usage.get(name, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ModelGatewayError("AI Gateway usage is invalid")
        usage[name] = value
    return usage


def _validate_tool_call(tool_call: object) -> None:
    if not isinstance(tool_call, Mapping):
        raise ModelGatewayError("AI Gateway tool call is invalid")
    call_id = tool_call.get("id")
    function = tool_call.get("function")
    if not isinstance(call_id, str) or not call_id.strip():
        raise ModelGatewayError("AI Gateway tool call id is invalid")
    if not isinstance(function, Mapping):
        raise ModelGatewayError("AI Gateway tool call function is invalid")
    name = function.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ModelGatewayError("AI Gateway tool call name is invalid")
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise ModelGatewayError(
                "AI Gateway tool call arguments are invalid"
            ) from exc
    if not isinstance(arguments, Mapping):
        raise ModelGatewayError("AI Gateway tool call arguments are invalid")


__all__ = [
    "ModelGatewayError",
    "OpenAICompatibleModelFactory",
    "StrictOpenAICompatiblePromptAdapter",
]
