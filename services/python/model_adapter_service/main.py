"""Model Adapter Service — unified OpenAI-compatible gateway to multiple LLM providers.

This service is the single entry point for all model invocations from the Go
tier. It exposes:
  - POST /v1/chat/completions  (OpenAI-compatible, non-streaming)
  - POST /v1/chat/stream       (OpenAI-compatible, SSE streaming)
  - POST /v1/embeddings        (text embeddings)
  - GET  /v1/models            (available models)
  - GET  /healthz, /profile, /metrics

Provider adapters are pluggable via the PROVIDER_ADAPTERS env var. In the
initial landing, a "mock" provider returns deterministic responses so the
full Go → Python → Go chain can be exercised end-to-end without external API
keys. Real provider adapters (claude, codex, openai, etc.) are added in P2.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from prometheus_client import Counter, Histogram, make_asgi_app
from pydantic import BaseModel, Field

app = FastAPI(title="model-adapter-service", version="0.1.0")
app.mount("/metrics", make_asgi_app())

# Prometheus metrics
REQUEST_COUNT = Counter(
    "model_adapter_requests_total",
    "Total model adapter requests",
    ["provider", "model", "status"],
)
REQUEST_LATENCY = Histogram(
    "model_adapter_request_duration_seconds",
    "Model adapter request duration",
    ["provider", "model"],
)


# ---------------------------------------------------------------------------
# Request / response models (OpenAI-compatible)
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "mock-gpt"
    messages: list[ChatMessage]
    temperature: float = 0.7
    max_tokens: int | None = None
    stream: bool = False
    system_prompt: str | None = None
    agent_role: str | None = None
    stage: str | None = None


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[dict[str, Any]]
    usage: dict[str, int]


class EmbeddingRequest(BaseModel):
    model: str = "mock-embedding"
    input: str | list[str]


class EmbeddingResponse(BaseModel):
    object: str = "list"
    data: list[dict[str, Any]]
    model: str
    usage: dict[str, int]


# ---------------------------------------------------------------------------
# Provider adapters
# ---------------------------------------------------------------------------

class MockProvider:
    """Deterministic mock provider for end-to-end testing without API keys."""

    name = "mock"

    def chat(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        last_msg = req.messages[-1].content if req.messages else ""
        role_label = req.agent_role or "assistant"
        stage_label = req.stage or "unknown"

        response_text = (
            f"[{role_label}@{stage_label}] Processed: {last_msg[:200]}"
            if len(last_msg) > 200
            else f"[{role_label}@{stage_label}] Processed: {last_msg}"
        )

        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            created=int(time.time()),
            model=req.model,
            choices=[
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": response_text},
                    "finish_reason": "stop",
                }
            ],
            usage={
                "prompt_tokens": len(last_msg) // 4,
                "completion_tokens": len(response_text) // 4,
                "total_tokens": (len(last_msg) + len(response_text)) // 4,
            },
        )

    def chat_stream(self, req: ChatCompletionRequest):
        """Yield SSE chunks mimicking OpenAI streaming format."""
        result = self.chat(req)
        content = result.choices[0]["message"]["content"]
        chunk_size = 10
        for i in range(0, len(content), chunk_size):
            chunk = content[i : i + chunk_size]
            yield f'data: {{"id":"{result.id}","object":"chat.completion.chunk","created":{result.created},"model":"{result.model}","choices":[{{"index":0,"delta":{{"content":"{chunk}"}},"finish_reason":null}}]}}\n\n'
        yield f'data: {{"id":"{result.id}","object":"chat.completion.chunk","created":{result.created},"model":"{result.model}","choices":[{{"index":0,"delta":{{}},"finish_reason":"stop"}}]}}\n\n'
        yield "data: [DONE]\n\n"

    def embed(self, text: str) -> list[float]:
        """Deterministic 384-dim embedding for testing."""
        import hashlib

        seed = hashlib.sha256(text.encode()).digest()
        return [((b / 255.0) - 0.5) for b in (seed * 24)[:384]]


class OpenAIProvider:
    """OpenAI-compatible API provider. Works with any OpenAI-compatible endpoint."""

    name = "openai"

    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.client = httpx.Client(timeout=60.0)

    def chat(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        if not self.api_key:
            raise HTTPException(status_code=503, detail="OpenAI API key not configured")

        payload = {
            "model": req.model,
            "messages": [{"role": m.role, "content": m.content} for m in req.messages],
            "temperature": req.temperature,
        }
        if req.max_tokens:
            payload["max_tokens"] = req.max_tokens
        if req.system_prompt:
            payload["messages"] = [
                {"role": "system", "content": req.system_prompt},
                *payload["messages"],
            ]

        resp = self.client.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()
        return ChatCompletionResponse(**data)


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

PROVIDERS: dict[str, Any] = {"mock": MockProvider(), "openai": OpenAIProvider()}


def get_provider(model: str) -> Any:
    """Select provider by model prefix (e.g. 'mock-gpt' → mock, 'gpt-4' → openai)."""
    if model.startswith("mock"):
        return PROVIDERS["mock"]
    if model.startswith("gpt") or model.startswith("o1") or model.startswith("o3"):
        return PROVIDERS["openai"]
    # Default to mock for safety
    return PROVIDERS["mock"]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "model-adapter-service"}


@app.get("/profile")
async def profile() -> dict:
    return {
        "service": "model-adapter-service",
        "responsibilities": [
            "provider adapters (mock, openai)",
            "OpenAI-compatible chat completions",
            "SSE streaming",
            "text embeddings",
            "offline model benchmarking",
            "batch embedding and rerank inference",
        ],
        "providers": list(PROVIDERS.keys()),
    }


@app.get("/v1/models")
async def list_models() -> dict:
    return {
        "object": "list",
        "data": [
            {"id": "mock-gpt", "object": "model", "owned_by": "agenthub"},
            {"id": "mock-embedding", "object": "model", "owned_by": "agenthub"},
            {"id": "gpt-4o", "object": "model", "owned_by": "openai"},
            {"id": "gpt-4o-mini", "object": "model", "owned_by": "openai"},
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    provider = get_provider(req.model)

    with REQUEST_LATENCY.labels(provider=provider.name, model=req.model).time():
        try:
            if req.stream:
                return StreamingResponse(
                    provider.chat_stream(req),
                    media_type="text/event-stream",
                )
            result = provider.chat(req)
            REQUEST_COUNT.labels(provider=provider.name, model=req.model, status="ok").inc()
            return result
        except HTTPException:
            raise
        except Exception as e:
            REQUEST_COUNT.labels(provider=provider.name, model=req.model, status="error").inc()
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/embeddings")
async def embeddings(req: EmbeddingRequest) -> EmbeddingResponse:
    texts = [req.input] if isinstance(req.input, str) else req.input
    provider = get_provider(req.model)

    data = []
    total_tokens = 0
    for i, text in enumerate(texts):
        emb = provider.embed(text)
        data.append({"object": "embedding", "index": i, "embedding": emb})
        total_tokens += len(text) // 4

    return EmbeddingResponse(
        data=data,
        model=req.model,
        usage={"prompt_tokens": total_tokens, "total_tokens": total_tokens},
    )
