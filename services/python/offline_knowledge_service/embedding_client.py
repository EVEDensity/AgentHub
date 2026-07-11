"""Embedding 客户端：调用 model-adapter-service 的 /v1/embeddings。

OpenAI 兼容协议：POST /v1/embeddings，请求 {model, input: str|list[str]}，
响应 {data: [{object, index, embedding: list[float]}], model, usage}。

关键设计：向量维度由 model-adapter 实际返回决定（MockProvider=384，真 BGE=1024），
本客户端在启动时用探针文本探测维度，供 Qdrant collection 建表使用——这样无需
硬编码维度，mock 与真实 provider 切换零改动。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import settings

logger = logging.getLogger(__name__)

# 探针文本：足够长以触发正常 embedding 路径，语义无关紧要。
_PROBE_TEXT = "agenthub embedding dimension probe"

# 探测到的维度缓存（启动时填，None 表示未探测）。
_probed_dim: int | None = None


class EmbeddingError(RuntimeError):
    """Embedding 调用失败。"""


async def embed(texts: list[str]) -> list[list[float]]:
    """批量 embedding。

    按 embedding_batch_size 分批调用 /v1/embeddings，保持输入顺序。

    Args:
        texts: 文本列表（非空）。

    Returns:
        与 texts 等长的向量列表。每个向量维度由 model-adapter 决定。

    Raises:
        EmbeddingError: 调用失败或响应格式异常。
    """
    if not texts:
        return []

    out: list[list[float]] = []
    batch = settings.embedding_batch_size
    base = settings.model_adapter_url.rstrip("/")
    url = f"{base}/v1/embeddings"
    timeout = httpx.Timeout(settings.http_timeout_seconds)

    async with httpx.AsyncClient(timeout=timeout) as client:
        for i in range(0, len(texts), batch):
            chunk = texts[i : i + batch]
            payload = {"model": settings.embedding_model, "input": chunk}
            try:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
            except httpx.HTTPError as e:
                raise EmbeddingError(f"embedding request failed: {e}") from e

            data = _parse_response(resp.json(), expect=len(chunk))
            out.extend(data)

    return out


async def probe_dimension() -> int:
    """用探针文本探测 embedding 维度，缓存结果。

    启动时调一次，供 Qdrant collection 建表使用。若探测失败抛 EmbeddingError
    （调用方应捕获并降级为默认维度或不建表）。

    Returns:
        向量维度（如 384 / 1024）。
    """
    global _probed_dim
    if _probed_dim is not None:
        return _probed_dim

    vectors = await embed([_PROBE_TEXT])
    if not vectors or not vectors[0]:
        raise EmbeddingError("probe returned empty embedding")
    _probed_dim = len(vectors[0])
    logger.info(
        "embedding dimension probed: %d (model=%s)",
        _probed_dim,
        settings.embedding_model,
    )
    return _probed_dim


def cached_dimension() -> int | None:
    """返回已探测的维度（未探测返回 None）。"""
    return _probed_dim


async def get_embedding(text: str) -> list[float]:
    """单文本 embedding 快捷方法。用于检索测试等场景。"""
    vectors = await embed([text])
    if not vectors:
        raise EmbeddingError("embedding returned empty result")
    return vectors[0]


def _parse_response(body: Any, expect: int) -> list[list[float]]:
    """解析 OpenAI 兼容响应，返回按 index 排序的向量列表。"""
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list) or len(data) != expect:
        raise EmbeddingError(
            f"unexpected embedding response: expected {expect} items, got "
            f"{len(data) if isinstance(data, list) else 'non-list'}"
        )
    # 按 index 排序（model-adapter 保证顺序，但防御性排序）。
    data.sort(key=lambda d: d.get("index", 0))
    vectors: list[list[float]] = []
    for item in data:
        emb = item.get("embedding")
        if not isinstance(emb, list) or not emb:
            raise EmbeddingError("embedding field missing or empty")
        vectors.append([float(x) for x in emb])
    return vectors
