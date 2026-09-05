"""Optional real-provider streaming smoke test.

Usage: python scripts/cli_provider_smoke.py
Environment: AGENTHUB_CLI_MODEL_API_KEY, AGENTHUB_CLI_PROVIDER,
AGENTHUB_CLI_MODEL, AGENTHUB_CLI_MODEL_BASE_URL.
The script never prints credentials and returns 0 for PASS or SKIP.
"""
from __future__ import annotations

import json
import os
import sys
import time

import httpx


def main() -> int:
    key = os.environ.get("AGENTHUB_CLI_MODEL_API_KEY", "").strip()
    if not key:
        print("SKIP: AGENTHUB_CLI_MODEL_API_KEY is not set")
        return 0
    provider = os.environ.get("AGENTHUB_CLI_PROVIDER", "openai").strip()
    model = os.environ.get("AGENTHUB_CLI_MODEL", "").strip() or "v4-flash"
    output_path = os.environ.get("AGENTHUB_CLI_PROVIDER_SMOKE_OUTPUT", "").strip()
    base = os.environ.get("AGENTHUB_CLI_MODEL_BASE_URL", "").strip().rstrip("/")
    if not base:
        base = "https://api.deepseek.com"
    url = base + "/chat/completions" if base.endswith("/v1") else base + "/v1/chat/completions"
    body = {"model": model, "stream": True, "messages": [{"role": "user", "content": "Reply with the word READY."}]}
    tool_smoke = os.environ.get("AGENTHUB_CLI_PROVIDER_TOOL_SMOKE", "").lower() in {"1", "true", "yes"}
    if tool_smoke:
        body["messages"] = [{"role": "user", "content": "Use the file_read tool to inspect README.md."}]
        body["tools"] = [{"type": "function", "function": {"name": "file_read", "description": "Read a workspace file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]
        body["tool_choice"] = "required"
    chunks = 0
    tool_calls = 0
    tool_call_ids: set[str] = set()
    tool_argument_fragments: dict[str, list[str]] = {}
    tool_call_indexes: dict[int, str] = {}
    started = time.perf_counter()
    first_token_seconds: float | None = None
    error_kind: str | None = None
    try:
        with httpx.stream("POST", url, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=60) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    continue
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = payload.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    if delta.get("content"):
                        chunks += 1
                        if first_token_seconds is None:
                            first_token_seconds = time.perf_counter() - started
                    for tool_call in delta.get("tool_calls") or []:
                        tool_calls += 1
                        index = tool_call.get("index")
                        call_id = str(tool_call.get("id") or "")
                        if not call_id and isinstance(index, int):
                            call_id = tool_call_indexes.get(index, "")
                        if call_id:
                            tool_call_ids.add(call_id)
                            if isinstance(index, int):
                                tool_call_indexes[index] = call_id
                        function = tool_call.get("function") or {}
                        if call_id and function.get("arguments"):
                            tool_argument_fragments.setdefault(call_id, []).append(str(function["arguments"]))
    except httpx.HTTPStatusError as exc:
        response = exc.response
        # Responses created by ``httpx.stream`` are not buffered yet. Read
        # before inspecting JSON so diagnostics never mask the original HTTP
        # status with ResponseNotRead.
        try:
            response.read()
        except (AttributeError, httpx.ResponseNotRead, httpx.StreamError):
            pass
        detail = _redacted_error_detail(response)
        error_kind = f"http_{response.status_code}"
        _emit_summary({"status": "FAIL", "provider": provider, "model": model, "errorType": error_kind, "statusCode": response.status_code, "detail": detail}, output_path)
        return 1
    except (httpx.HTTPError, OSError) as exc:
        error_kind = type(exc).__name__
        _emit_summary({"status": "FAIL", "provider": provider, "model": model, "errorType": error_kind}, output_path)
        return 1
    if tool_smoke and tool_calls == 0:
        _emit_summary({"status": "FAIL", "provider": provider, "model": model, "errorType": "missing_tool_call", "textChunks": chunks}, output_path)
        return 1
    if tool_smoke and not tool_call_ids:
        _emit_summary({"status": "FAIL", "provider": provider, "model": model, "errorType": "missing_call_id", "toolCallChunks": tool_calls}, output_path)
        return 1
    if not tool_smoke and chunks == 0:
        _emit_summary({"status": "FAIL", "provider": provider, "model": model, "errorType": "missing_text_chunks"}, output_path)
        return 1
    _emit_summary({
        "schemaVersion": 1,
        "status": "PASS",
        "provider": provider,
        "model": model,
        "textChunks": chunks,
        "toolCallChunks": tool_calls,
        "toolCallIds": len(tool_call_ids),
        "toolArgumentsComplete": (bool(tool_argument_fragments) and all(_balanced_json_fragment("".join(parts)) for parts in tool_argument_fragments.values())) if tool_smoke else True,
        "firstTokenSeconds": first_token_seconds,
    }, output_path)
    return 0


def _balanced_json_fragment(value: str) -> bool:
    if not value.strip():
        return False
    try:
        json.loads(value)
    except json.JSONDecodeError:
        return False
    return True


def _emit_summary(summary: dict[str, object], output_path: str) -> None:
    rendered = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    print(rendered)
    if output_path:
        from pathlib import Path
        Path(output_path).write_text(rendered + "\n", encoding="utf-8")


def _redacted_error_detail(response: httpx.Response) -> str:
    """Return a bounded provider error without credentials or request data."""
    try:
        payload = response.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            value = error.get("message") or error.get("type") or error.get("code")
        else:
            value = error or payload
    except (ValueError, httpx.ResponseNotRead):
        try:
            value = response.text
        except httpx.ResponseNotRead:
            value = "response body unavailable"
    text = str(value).replace("Bearer ", "Bearer <redacted>")
    return " ".join(text.split())[:300]


def validate_event_chain(event_types: list[str]) -> tuple[bool, list[str]]:
    """Validate the minimum Mission Control event progression for Phase A."""
    required = [
        "assistant.delta", "tool.started", "tool.output", "checkpoint.created",
        "verification.started", "verification.completed", "mission.completed",
    ]
    missing = [kind for kind in required if kind not in event_types]
    positions = [event_types.index(kind) for kind in required if kind in event_types]
    if positions != sorted(positions):
        return False, missing + ["event_order"]
    return not missing, missing


if __name__ == "__main__":
    raise SystemExit(main())
