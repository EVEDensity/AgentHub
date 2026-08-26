"""Local OpenAI-compatible mock LLM upstream.

Used ONLY for the new-api gateway rollout: it acts as a stand-in upstream
channel so AgentHub → new-api → upstream can be exercised end-to-end
offline (no provider keys needed) during development and CI.

Endpoints:
  GET  /v1/models             → returns the configured model id
  POST /v1/chat/completions   → deterministic echo response (sync + SSE)

Set MOCK_MODEL to the model id this service advertises (default mock-llm).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="mock-llm", version="1.0.0")

MODEL = os.getenv("MOCK_MODEL", "mock-llm").strip()
LATENCY_MS = int(os.getenv("MOCK_LATENCY_MS", "0"))


@app.get("/v1/models")
async def list_models() -> dict:
    return {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "mock"}]}


def _echo_body(payload: dict) -> dict:
    messages = payload.get("messages", [])
    prompt = "".join(str(message.get("content", "")) for message in messages)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": payload.get("model", MODEL),
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": f"[mock:{MODEL}] {prompt}"},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": len(prompt) // 2, "completion_tokens": 8, "total_tokens": len(prompt) // 2 + 8},
    }


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(request: Request):
    if LATENCY_MS:
        await asyncio.sleep(LATENCY_MS / 1000.0)
    payload = await request.json()
    if payload.get("stream"):
        async def stream():
            body = _echo_body(payload)
            chunk = {
                "id": body["id"],
                "object": "chat.completion.chunk",
                "created": body["created"],
                "model": body["model"],
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": body["choices"][0]["message"]["content"]}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            done = {"id": body["id"], "object": "chat.completion.chunk", "created": body["created"],
                    "model": body["model"], "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(stream(), media_type="text/event-stream")
    return JSONResponse(_echo_body(payload))