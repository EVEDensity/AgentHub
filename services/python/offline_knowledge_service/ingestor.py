"""入库编排器：chunk → embed → upsert，带降级链。

降级策略（与 Rust retrieval-core 读侧降级呼应）：
  1. content 为空 / chunking 0 块 → degraded，reason="empty_content"，不入库。
  2. embedding 调用失败 → degraded，reason="embedding_unavailable"，不入库
     （无向量无法被 dense 检索，写入无意义）。
  3. qdrant upsert 失败 → degraded，reason="qdrant_unavailable"，
     chunks_created 仍统计（embedding 已成功，可后续补写）。
  4. 超过 max_chunks_per_ingest → 截断 + degraded，reason="chunks_truncated"。
"""

from __future__ import annotations

import logging
import time
import uuid

from . import embedding_client, qdrant_repo
from .chunker import split_text
from .config import settings
from .models import IngestRequest, IngestResult

logger = logging.getLogger(__name__)


async def ingest(req: IngestRequest, repo: qdrant_repo.QdrantRepo) -> IngestResult:
    """执行一次入库。

    Args:
        req: 入库请求。
        repo: Qdrant 仓储（由调用方持有，复用连接）。

    Returns:
        IngestResult，含 chunks_created / points_upserted / degraded 等统计。
    """
    request_id = req.request_id or str(uuid.uuid4())
    start = time.monotonic()

    degraded = False
    reason: str | None = None

    # ── 1. 分块 ──────────────────────────────────────────────────────
    chunks = split_text(
        req.content,
        chunk_size=req.chunking.chunk_size,
        overlap=req.chunking.overlap,
    )

    if not chunks:
        return _result(
            request_id, req, chunks_created=0, points_upserted=0,
            dim=embedding_client.cached_dimension() or 0, start=start,
            degraded=True, reason="empty_content",
        )

    # 超限截断。
    if len(chunks) > settings.max_chunks_per_ingest:
        logger.warning(
            "source %s produced %d chunks, truncating to %d",
            req.source_id, len(chunks), settings.max_chunks_per_ingest,
        )
        chunks = chunks[: settings.max_chunks_per_ingest]
        degraded = True
        reason = "chunks_truncated"

    # ── 2. Embedding ─────────────────────────────────────────────────
    try:
        vectors = await embedding_client.embed([c.text for c in chunks])
    except Exception as e:
        logger.error("embedding failed for source %s: %s", req.source_id, e)
        return _result(
            request_id, req, chunks_created=len(chunks), points_upserted=0,
            dim=embedding_client.cached_dimension() or 0, start=start,
            degraded=True, reason="embedding_unavailable",
        )

    dim = len(vectors[0]) if vectors else (embedding_client.cached_dimension() or 0)

    # ── 3. Qdrant upsert ─────────────────────────────────────────────
    try:
        points = await repo.upsert_chunks(
            collection=req.collection,
            tenant_id=req.tenant_id,
            source_id=req.source_id,
            chunks=chunks,
            vectors=vectors,
            metadata=req.metadata,
        )
    except Exception as e:
        logger.error("qdrant upsert failed for source %s: %s", req.source_id, e)
        return _result(
            request_id, req, chunks_created=len(chunks), points_upserted=0,
            dim=dim, start=start,
            degraded=True, reason="qdrant_unavailable",
        )

    return _result(
        request_id, req, chunks_created=len(chunks), points_upserted=points,
        dim=dim, start=start,
        degraded=degraded, reason=reason,
    )


def _result(
    request_id: str,
    req: IngestRequest,
    *,
    chunks_created: int,
    points_upserted: int,
    dim: int,
    start: float,
    degraded: bool,
    reason: str | None,
) -> IngestResult:
    elapsed_ms = int((time.monotonic() - start) * 1000)
    return IngestResult(
        request_id=request_id,
        tenant_id=req.tenant_id,
        source_id=req.source_id,
        collection=req.collection,
        chunks_created=chunks_created,
        points_upserted=points_upserted,
        embedding_dim=dim,
        elapsed_ms=elapsed_ms,
        degraded=degraded,
        degradation_reason=reason,
    )
