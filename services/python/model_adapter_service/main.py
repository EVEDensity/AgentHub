"""Model Adapter Service — unified gateway to multiple LLM/embedding providers.

This service is the single entry point for all model invocations from the Go
tier. It exposes:
  - POST /v1/chat/completions  (OpenAI-compatible, non-streaming / SSE)
  - POST /v1/embeddings        (text embeddings — OpenAI + BGE)
  - POST /v1/rerank            (cross-encoder re-ranking)
  - GET  /v1/models            (available models)
  - GET  /healthz, /profile, /metrics

Providers: mock (test), openai (GPT-4o, embeddings), anthropic (Claude),
           bge (BAAI/BGE embeddings), codex (OpenAI Codex CLI).

P2+ provider adapters (claude, codex, bge) are registered here so the Go tier
can reach them through a single HTTP surface area.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from prometheus_client import Counter, Histogram, make_asgi_app
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="model-adapter-service", version="0.2.0")
app.mount("/metrics", make_asgi_app())

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
# Request / response models
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


class RerankRequest(BaseModel):
    model: str = "bge-reranker-v2-m3"
    query: str
    documents: list[str]
    top_k: int | None = None
    return_documents: bool = False


class RerankResponse(BaseModel):
    object: str = "rerank"
    model: str
    results: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _httpx_client() -> httpx.Client:
    return httpx.Client(timeout=120.0)


# ---------------------------------------------------------------------------
# MockProvider
# ---------------------------------------------------------------------------

class MockProvider:
    """Deterministic mock — no API keys needed, used for end-to-end testing."""

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
            choices=[{
                "index": 0,
                "message": {"role": "assistant", "content": response_text},
                "finish_reason": "stop",
            }],
            usage={
                "prompt_tokens": len(last_msg) // 4,
                "completion_tokens": len(response_text) // 4,
                "total_tokens": (len(last_msg) + len(response_text)) // 4,
            },
        )

    def chat_stream(self, req: ChatCompletionRequest):
        result = self.chat(req)
        content = result.choices[0]["message"]["content"]
        for i in range(0, len(content), 10):
            chunk = content[i:i + 10]
            yield f'data: {{"id":"{result.id}","object":"chat.completion.chunk","created":{result.created},"model":"{result.model}","choices":[{{"index":0,"delta":{{"content":"{chunk}"}},"finish_reason":null}}]}}\n\n'
        yield f'data: {{"id":"{result.id}","object":"chat.completion.chunk","created":{result.created},"model":"{result.model}","choices":[{{"index":0,"delta":{{}},"finish_reason":"stop"}}]}}\n\n'
        yield "data: [DONE]\n\n"

    def embed(self, text: str) -> list[float]:
        import hashlib
        seed = hashlib.sha256(text.encode()).digest()
        return [((b / 255.0) - 0.5) for b in (seed * 24)[:384]]

    def rerank(self, query: str, documents: list[str], top_k: int | None = None) -> list[dict[str, Any]]:
        # Mock: return documents in original order with descending mock scores.
        k = top_k or len(documents)
        return [
            {"index": i, "score": 1.0 - (i * 0.05), "document": d}
            for i, d in enumerate(documents[:k])
        ]


# ---------------------------------------------------------------------------
# OpenAIProvider
# ---------------------------------------------------------------------------

class OpenAIProvider:
    """OpenAI / OpenAI-compatible API provider (chat + embeddings)."""

    name = "openai"

    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def chat(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        if not self.api_key:
            raise HTTPException(status_code=503, detail="OpenAI API key not configured")

        messages: list[dict[str, str]] = []
        if req.system_prompt:
            messages.append({"role": "system", "content": req.system_prompt})
        messages.extend({"role": m.role, "content": m.content} for m in req.messages)

        payload: dict[str, Any] = {"model": req.model, "messages": messages, "temperature": req.temperature}
        if req.max_tokens:
            payload["max_tokens"] = req.max_tokens

        with _httpx_client() as client:
            resp = client.post(f"{self.base_url}/chat/completions", json=payload, headers=self._headers())
            resp.raise_for_status()
            return ChatCompletionResponse(**resp.json())

    def chat_stream(self, req: ChatCompletionRequest):
        """SSE streaming passthrough from the upstream OpenAI-compatible API."""
        if not self.api_key:
            raise HTTPException(status_code=503, detail="OpenAI API key not configured")

        messages: list[dict[str, str]] = []
        if req.system_prompt:
            messages.append({"role": "system", "content": req.system_prompt})
        messages.extend({"role": m.role, "content": m.content} for m in req.messages)

        payload: dict[str, Any] = {"model": req.model, "messages": messages, "temperature": req.temperature, "stream": True}
        if req.max_tokens:
            payload["max_tokens"] = req.max_tokens

        with _httpx_client() as client:
            with client.stream("POST", f"{self.base_url}/chat/completions", json=payload, headers=self._headers()) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if line:
                        yield line + "\n"

    def embed(self, text: str) -> list[float]:
        if not self.api_key:
            raise HTTPException(status_code=503, detail="OpenAI API key not configured")

        model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        with _httpx_client() as client:
            resp = client.post(
                f"{self.base_url}/embeddings",
                json={"model": model, "input": text},
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return data["data"][0]["embedding"]

    def rerank(self, _query: str, _documents: list[str], _top_k: int | None = None) -> list[dict[str, Any]]:
        raise HTTPException(status_code=501, detail="rerank not supported by OpenAI provider")


# ---------------------------------------------------------------------------
# AnthropicClaudeProvider
# ---------------------------------------------------------------------------

class AnthropicClaudeProvider:
    """Anthropic Claude provider via the Messages API.

    Maps the OpenAI-compatible chat request format to Anthropic's native
    messages API. Streaming is supported via SSE passthrough.
    """

    name = "anthropic"

    def __init__(self) -> None:
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        self.default_model = os.getenv("ANTHROPIC_DEFAULT_MODEL", "claude-sonnet-4-6")

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def _map_messages(self, req: ChatCompletionRequest) -> tuple[str | None, list[dict[str, Any]]]:
        """Convert OpenAI-format messages to Anthropic format.

        Returns (system, messages) — Anthropic accepts a top-level system param.
        """
        system = req.system_prompt
        anthropic_msgs: list[dict[str, Any]] = []
        for m in req.messages:
            role = m.role
            if role == "system":
                system = m.content
                continue
            if role == "assistant":
                role = "assistant"
            elif role == "function" or role == "tool":
                role = "user"
            else:
                role = "user"
            anthropic_msgs.append({"role": role, "content": m.content})
        return system, anthropic_msgs

    def chat(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        if not self.api_key:
            raise HTTPException(status_code=503, detail="Anthropic API key not configured (set ANTHROPIC_API_KEY)")

        model = req.model if req.model and not req.model.startswith("claude-") else self.default_model
        if req.model and req.model.startswith("claude-"):
            model = req.model

        system, messages = self._map_messages(req)
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": req.max_tokens or 4096,
        }
        if system:
            payload["system"] = system

        with _httpx_client() as client:
            resp = client.post(f"{self.base_url}/v1/messages", json=payload, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()

        content_blocks = data.get("content", [])
        text = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")

        return ChatCompletionResponse(
            id=f"chatcmpl-{data.get('id', uuid.uuid4().hex[:12])}",
            created=int(time.time()),
            model=data.get("model", model),
            choices=[{
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": data.get("stop_reason", "stop"),
            }],
            usage={
                "prompt_tokens": data.get("usage", {}).get("input_tokens", 0),
                "completion_tokens": data.get("usage", {}).get("output_tokens", 0),
                "total_tokens": data.get("usage", {}).get("input_tokens", 0) + data.get("usage", {}).get("output_tokens", 0),
            },
        )

    def chat_stream(self, req: ChatCompletionRequest):
        """SSE passthrough for Anthropic streaming (SSE) → OpenAI-compatible SSE."""
        if not self.api_key:
            raise HTTPException(status_code=503, detail="Anthropic API key not configured")

        model = req.model if req.model and not req.model.startswith("claude-") else self.default_model
        if req.model and req.model.startswith("claude-"):
            model = req.model

        system, messages = self._map_messages(req)
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": req.max_tokens or 4096,
            "stream": True,
        }
        if system:
            payload["system"] = system

        chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())

        with _httpx_client() as client:
            with client.stream("POST", f"{self.base_url}/v1/messages", json=payload, headers=self._headers()) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    # Anthropic SSE: "data: {...}" or "event: ..."
                    if line.startswith("data: "):
                        try:
                            event = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue
                        event_type = event.get("type", "")
                        if event_type == "content_block_delta":
                            delta = event.get("delta", {})
                            text = delta.get("text", "")
                            yield f'data: {{"id":"{chat_id}","object":"chat.completion.chunk","created":{created},"model":"{model}","choices":[{{"index":0,"delta":{{"content":"{text}"}},"finish_reason":null}}]}}\n\n'
                        elif event_type == "message_stop":
                            yield f'data: {{"id":"{chat_id}","object":"chat.completion.chunk","created":{created},"model":"{model}","choices":[{{"index":0,"delta":{{}},"finish_reason":"stop"}}]}}\n\n'
                yield "data: [DONE]\n\n"

    def embed(self, _text: str) -> list[float]:
        raise HTTPException(status_code=501, detail="embeddings not supported by Anthropic provider — use BGE or OpenAI")

    def rerank(self, _query: str, _documents: list[str], _top_k: int | None = None) -> list[dict[str, Any]]:
        raise HTTPException(status_code=501, detail="rerank not supported by Anthropic provider")


# ---------------------------------------------------------------------------
# BGEEmbeddingProvider
# ---------------------------------------------------------------------------

class BGEEmbeddingProvider:
    """BGE / BAAI embedding models via HuggingFace TEI or compatible endpoint.

    Supports BGE-M3 (1024-dim multilingual), BGE-large-en (1024-dim), and
    any HuggingFace Text Embeddings Inference (TEI) compatible server.

    Configure with:
      BGE_API_URL=http://localhost:8080  (default)
      BGE_API_KEY=...                    (optional, for HF inference endpoints)
      BGE_DEFAULT_MODEL=BAAI/bge-m3      (model name sent in request)
    """

    name = "bge"

    def __init__(self) -> None:
        self.api_url = os.getenv("BGE_API_URL", "http://127.0.0.1:8080")
        self.api_key = os.getenv("BGE_API_KEY", "")
        self.default_model = os.getenv("BGE_DEFAULT_MODEL", "BAAI/bge-m3")

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def chat(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        raise HTTPException(status_code=501, detail="chat not supported by BGE provider — use OpenAI or Anthropic")

    def chat_stream(self, req: ChatCompletionRequest):
        raise HTTPException(status_code=501, detail="chat not supported by BGE provider")

    def embed(self, text: str) -> list[float]:
        """Call BGE embedding endpoint (TEI-compatible: POST /embed)."""
        with _httpx_client() as client:
            resp = client.post(
                f"{self.api_url}/embed",
                json={"inputs": text, "model": self.default_model},
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            # TEI returns [[...]] (batch), HF returns [...] (single).
            emb = data[0] if isinstance(data, list) and isinstance(data[0], list) else data
            if isinstance(emb, list):
                return emb  # type: ignore[return-value]
            raise HTTPException(status_code=502, detail=f"unexpected embedding response shape: {type(data)}")

    def rerank(self, query: str, documents: list[str], top_k: int | None = None) -> list[dict[str, Any]]:
        """Call BGE rerank endpoint (TEI-compatible: POST /rerank).

        Uses a cross-encoder model (e.g., BAAI/bge-reranker-v2-m3) to score
        each document against the query and returns ranked results.
        """
        k = top_k or len(documents)
        with _httpx_client() as client:
            resp = client.post(
                f"{self.api_url}/rerank",
                json={"query": query, "texts": documents, "model": os.getenv("BGE_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")},
                headers=self._headers(),
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            # Expected response: [{"index": 0, "score": 0.95}, ...] or {"results": [...]}
            results = data if isinstance(data, list) else data.get("results", [])
            sorted_results = sorted(results, key=lambda x: x.get("score", 0), reverse=True)[:k]
            out = []
            for r in sorted_results:
                entry = {"index": r["index"], "score": r["score"]}
                if r["index"] < len(documents):
                    entry["document"] = documents[r["index"]]
                out.append(entry)
            return out


# ---------------------------------------------------------------------------
# OpenAICompatibleProvider  (generic — catches any OpenAI-compatible endpoint)
# ---------------------------------------------------------------------------

class OpenAICompatibleProvider:
    """Generic OpenAI-compatible API provider — points at any URL that speaks
    the OpenAI chat completions protocol (e.g. vLLM, Ollama, LiteLLM proxy,
    local-model-server). Configured via OPENAI_COMPATIBLE_* env vars."""

    name = "openai-compatible"

    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_COMPATIBLE_API_KEY", "not-needed")
        self.base_url = os.getenv("OPENAI_COMPATIBLE_BASE_URL", "http://127.0.0.1:8000/v1")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def chat(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        messages: list[dict[str, str]] = []
        if req.system_prompt:
            messages.append({"role": "system", "content": req.system_prompt})
        messages.extend({"role": m.role, "content": m.content} for m in req.messages)
        payload: dict[str, Any] = {"model": req.model, "messages": messages, "temperature": req.temperature}
        if req.max_tokens:
            payload["max_tokens"] = req.max_tokens
        with _httpx_client() as client:
            resp = client.post(f"{self.base_url}/chat/completions", json=payload, headers=self._headers())
            resp.raise_for_status()
            return ChatCompletionResponse(**resp.json())

    def chat_stream(self, req: ChatCompletionRequest):
        messages: list[dict[str, str]] = []
        if req.system_prompt:
            messages.append({"role": "system", "content": req.system_prompt})
        messages.extend({"role": m.role, "content": m.content} for m in req.messages)
        payload: dict[str, Any] = {"model": req.model, "messages": messages, "temperature": req.temperature, "stream": True}
        if req.max_tokens:
            payload["max_tokens"] = req.max_tokens
        with _httpx_client() as client:
            with client.stream("POST", f"{self.base_url}/chat/completions", json=payload, headers=self._headers()) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if line:
                        yield line + "\n"

    def embed(self, text: str) -> list[float]:
        with _httpx_client() as client:
            resp = client.post(
                f"{self.base_url}/embeddings",
                json={"model": os.getenv("OPENAI_COMPATIBLE_EMBED_MODEL", "text-embedding-3-small"), "input": text},
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]

    def rerank(self, _query: str, _documents: list[str], _top_k: int | None = None) -> list[dict[str, Any]]:
        raise HTTPException(status_code=501, detail="rerank not supported by this provider")


# ---------------------------------------------------------------------------
# VLLMProvider  (vLLM local inference — same OpenAI-compatible protocol,
# configured via VLLM_* env vars with fallback to OPENAI_COMPATIBLE_*)
# ---------------------------------------------------------------------------

class VLLMProvider(OpenAICompatibleProvider):
    """vLLM local inference provider.

    vLLM (https://docs.vllm.ai) exposes an OpenAI-compatible HTTP API, so we
    reuse OpenAICompatibleProvider's logic and only override env-var lookup:
        VLLM_BASE_URL   (fallback OPENAI_COMPATIBLE_BASE_URL, default http://127.0.0.1:8000/v1)
        VLLM_API_KEY    (fallback OPENAI_COMPATIBLE_API_KEY, default "not-needed")
        VLLM_EMBED_MODEL (fallback OPENAI_COMPATIBLE_EMBED_MODEL)
    """

    name = "vllm"

    def __init__(self) -> None:
        # Skip parent __init__ so we can read VLLM_* first
        self.api_key = (
            os.getenv("VLLM_API_KEY")
            or os.getenv("OPENAI_COMPATIBLE_API_KEY")
            or "not-needed"
        )
        self.base_url = (
            os.getenv("VLLM_BASE_URL")
            or os.getenv("OPENAI_COMPATIBLE_BASE_URL")
            or "http://127.0.0.1:8000/v1"
        )

    @staticmethod
    def _strip_prefix(model: str) -> str:
        """Strip the 'vllm-' or 'vllm/' routing prefix so vLLM receives the
        real HF model id (e.g. 'vllm-Qwen/Qwen2.5-7B-Instruct' → 'Qwen/Qwen2.5-7B-Instruct')."""
        if model.startswith("vllm/"):
            return model[len("vllm/"):]
        if model.startswith("vllm-"):
            return model[len("vllm-"):]
        return model

    def chat(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        # Strip vllm- prefix before forwarding to vLLM server
        original_model = req.model
        req.model = self._strip_prefix(original_model)
        try:
            return super().chat(req)
        finally:
            req.model = original_model

    def chat_stream(self, req: ChatCompletionRequest):
        original_model = req.model
        req.model = self._strip_prefix(original_model)
        try:
            yield from super().chat_stream(req)
        finally:
            req.model = original_model

    def embed(self, text: str) -> list[float]:
        # Override to honor VLLM_EMBED_MODEL instead of OPENAI_COMPATIBLE_EMBED_MODEL
        embed_model = (
            os.getenv("VLLM_EMBED_MODEL")
            or os.getenv("OPENAI_COMPATIBLE_EMBED_MODEL")
            or "text-embedding-3-small"
        )
        with _httpx_client() as client:
            resp = client.post(
                f"{self.base_url}/embeddings",
                json={"model": embed_model, "input": text},
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]


# ---------------------------------------------------------------------------
# NewAPIProvider  (new-api / one-api unified gateway — optional supplier layer;
# configured via NEWAPI_BASE_URL / NEWAPI_API_KEY, OpenAI-compatible protocol)
# ---------------------------------------------------------------------------

class NewAPIProvider(OpenAICompatibleProvider):
    """new-api unified LLM gateway provider.

    When ``NEWAPI_BASE_URL`` is set, chat models route through the gateway's
    OpenAI-compatible entry; new-api owns channel selection, retry/failover,
    quotas and billing. Local embedding/rerank (bge) and mock stay local.
    """

    name = "newapi"

    def __init__(self) -> None:
        self.api_key = os.getenv("NEWAPI_API_KEY") or "not-needed"
        self.base_url = os.getenv("NEWAPI_BASE_URL") or "http://127.0.0.1:3000/v1"


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

_providers: dict[str, Any] = {}


def _init_providers() -> dict[str, Any]:
    """Lazy-init providers so env vars are read at request time, not import time."""
    if _providers:
        return _providers
    _providers["mock"] = MockProvider()
    _providers["openai"] = OpenAIProvider()
    _providers["anthropic"] = AnthropicClaudeProvider()
    _providers["bge"] = BGEEmbeddingProvider()
    _providers["openai-compatible"] = OpenAICompatibleProvider()
    _providers["vllm"] = VLLMProvider()
    _providers["newapi"] = NewAPIProvider()
    return _providers


def get_provider(model: str) -> Any:
    """Route a model name to a provider instance.

    Rules (checked in order):
      - "mock-*"          → mock
      - "claude-*"        → anthropic (Claude)
      - "gpt-*", "o1-*", "o3-*" → openai
      - "bge-*", "BAAI/*" → bge
      - "text-embedding-*" → openai (OpenAI embeddings)
      - "vllm-*", "vllm/*" → vllm (if VLLM_BASE_URL or OPENAI_COMPATIBLE_BASE_URL set) else mock
      - "codex"           → openai-compatible (if OPENAI_COMPATIBLE_BASE_URL set) else mock
      - default           → vllm if VLLM_BASE_URL set, else openai-compatible if
                            OPENAI_COMPATIBLE_BASE_URL set, else mock
    """
    providers = _init_providers()

    if model.startswith("mock"):
        return providers["mock"]
    if model.startswith("claude"):
        return providers["anthropic"]
    if model.startswith(("gpt-", "o1-", "o3-", "o4-")):
        return providers["openai"]
    if model.startswith(("bge-", "BAAI/")) or model.startswith("bge"):
        return providers["bge"]
    if model.startswith("text-embedding-"):
        return providers["openai"]
    # ── vLLM 显式路由（vllm-<model> 或 vllm/<model>）─────────────────
    if model.startswith("vllm-") or model.startswith("vllm/"):
        if os.getenv("VLLM_BASE_URL") or os.getenv("OPENAI_COMPATIBLE_BASE_URL"):
            return providers["vllm"]
        return providers["mock"]
    if model == "codex" or model.startswith("codex-"):
        if os.getenv("OPENAI_COMPATIBLE_BASE_URL"):
            return providers["openai-compatible"]
        return providers["mock"]
    # ── new-api unified gateway (optional supplier layer) ───────────
    if os.getenv("NEWAPI_BASE_URL"):
        return providers["newapi"]
    # Generic fallback: prefer vllm if VLLM_BASE_URL set, else openai-compatible
    if os.getenv("VLLM_BASE_URL"):
        return providers["vllm"]
    if os.getenv("OPENAI_COMPATIBLE_BASE_URL"):
        return providers["openai-compatible"]
    return providers["mock"]


# ---------------------------------------------------------------------------
# Rerank logic
# ---------------------------------------------------------------------------

def _get_rerank_provider(model: str) -> Any:
    """Route rerank requests to the best available provider."""
    if model.startswith("bge") or model.startswith("BAAI/"):
        return _init_providers()["bge"]
    # Fallback: try BGE provider; if unavailable, mock.
    if os.getenv("BGE_API_URL"):
        return _init_providers()["bge"]
    return _init_providers()["mock"]


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
        "version": "0.2.0",
        "responsibilities": [
            "provider adapters (mock, openai, anthropic, bge, openai-compatible)",
            "OpenAI-compatible chat completions (non-streaming + SSE)",
            "text embeddings (OpenAI + BGE)",
            "cross-encoder rerank (BGE)",
        ],
        "providers": list(_init_providers().keys()),
    }


@app.get("/v1/models")
async def list_models() -> dict:
    models = [
        {"id": "mock-gpt", "object": "model", "owned_by": "agenthub"},
        {"id": "mock-embedding", "object": "model", "owned_by": "agenthub"},
        {"id": "gpt-4o", "object": "model", "owned_by": "openai"},
        {"id": "gpt-4o-mini", "object": "model", "owned_by": "openai"},
        {"id": "o4-mini", "object": "model", "owned_by": "openai"},
        {"id": "text-embedding-3-small", "object": "model", "owned_by": "openai"},
        {"id": "text-embedding-3-large", "object": "model", "owned_by": "openai"},
        {"id": "claude-sonnet-4-6", "object": "model", "owned_by": "anthropic"},
        {"id": "claude-opus-4-8", "object": "model", "owned_by": "anthropic"},
        {"id": "claude-haiku-4-5", "object": "model", "owned_by": "anthropic"},
        {"id": "BAAI/bge-m3", "object": "model", "owned_by": "bge"},
        {"id": "BAAI/bge-large-en-v1.5", "object": "model", "owned_by": "bge"},
        {"id": "BAAI/bge-reranker-v2-m3", "object": "model", "owned_by": "bge"},
        # ── vLLM 本地推理模型（仅在 VLLM_BASE_URL 配置时可用）──────────
        # 命名约定：vllm-<hf-model-id>，调用时 model 字段传 vllm-Qwen/Qwen2.5-7B-Instruct
        # 实际可用模型由 vLLM 服务加载，此处仅作注册声明
        {"id": "vllm-Qwen/Qwen2.5-7B-Instruct", "object": "model", "owned_by": "vllm"},
        {"id": "vllm-Qwen/Qwen2.5-14B-Instruct", "object": "model", "owned_by": "vllm"},
        {"id": "vllm-meta-llama/Meta-Llama-3-8B-Instruct", "object": "model", "owned_by": "vllm"},
        {"id": "vllm-meta-llama/Meta-Llama-3-70B-Instruct", "object": "model", "owned_by": "vllm"},
        {"id": "vllm-microsoft/Phi-3-medium-4k-instruct", "object": "model", "owned_by": "vllm"},
    ]
    return {"object": "list", "data": models}


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


@app.post("/v1/rerank")
async def rerank(req: RerankRequest) -> RerankResponse:
    provider = _get_rerank_provider(req.model)
    try:
        results = provider.rerank(req.query, req.documents, req.top_k)
        return RerankResponse(object="rerank", model=req.model, results=results)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
