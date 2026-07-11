"""offline-knowledge-service: ingestion → chunking → embedding → Qdrant upsert。

订阅 NATS `agenthub.knowledge.ingest.requested` 事件，把文档内容分块、向量化、
写入 Qdrant collection，完成后发布 `agenthub.knowledge.ingest.completed`。

也暴露 HTTP POST /ingest 供同步调用（debug / API 集成）。
"""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from prometheus_client import Counter, Histogram, make_asgi_app

from shared.events import EventEnvelope, EventType, Producer, Routing
from shared.nats_client import NatsClient

from . import embedding_client, ingestor, qdrant_repo
from .config import settings
from .models import IngestRequest, IngestResult
from .api_v2 import router as api_v2_router

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ── Prometheus 指标 ──────────────────────────────────────────────────
INGEST_COUNTER = Counter(
    "offline_knowledge_ingest_total", "Total ingest operations", ["status"]
)
INGEST_CHUNKS = Counter(
    "offline_knowledge_chunks_total", "Total chunks created across all ingests"
)
INGEST_POINTS = Counter(
    "offline_knowledge_points_total", "Total Qdrant points upserted"
)
INGEST_LATENCY = Histogram(
    "offline_knowledge_ingest_latency_ms", "Ingest latency in ms"
)

# ── 全局句柄（lifespan 中初始化）────────────────────────────────────
nats_client: NatsClient | None = None
repo: qdrant_repo.QdrantRepo | None = None
embedding_dim: int = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    global nats_client, repo, embedding_dim

    # 1. Qdrant 仓储。
    repo = qdrant_repo.QdrantRepo()

    # 2. 探测 embedding 维度（决定 collection 向量大小）。
    try:
        embedding_dim = await embedding_client.probe_dimension()
    except Exception as e:
        logger.error("embedding dimension probe failed: %s — falling back to 384", e)
        embedding_dim = 384

    # 3. 幂等建 4 个 collection。
    await repo.ensure_all_collections(embedding_dim)

    # 4. 连接 NATS 并订阅。
    nats_client = NatsClient(settings.nats_url)
    await nats_client.connect()

    async def on_ingest_requested(envelope: EventEnvelope):
        await _handle_ingest_event(envelope)

    await nats_client.subscribe(
        "offline-knowledge-ingest",
        "agenthub.knowledge.ingest.requested",
        on_ingest_requested,
    )

    logger.info(
        "offline-knowledge-service started (embedding_dim=%d, collections=%s)",
        embedding_dim,
        settings.collections,
    )
    yield

    if nats_client:
        await nats_client.close()
    if repo:
        await repo.close()


app = FastAPI(title="offline-knowledge-service", version="0.3.0", lifespan=lifespan)
app.include_router(api_v2_router)
app.mount("/metrics", make_asgi_app())


# ── NATS 事件处理 ────────────────────────────────────────────────────
async def _handle_ingest_event(envelope: EventEnvelope) -> None:
    """knowledge.ingest.requested → ingest → publish knowledge.ingest.completed。"""
    p = envelope.payload
    tenant_id = p.get("tenant_id") or envelope.tenant_id
    try:
        req = IngestRequest(
            request_id=p.get("request_id"),
            tenant_id=tenant_id,
            source_id=p.get("source_id", ""),
            collection=p.get("collection", "docs"),
            content_type=p.get("content_type", "text/plain"),
            content=p.get("content", ""),
            metadata=p.get("metadata") or {},
            chunking=p.get("chunking") or {},
        )
    except Exception as e:
        logger.error("invalid ingest payload: %s", e, exc_info=True)
        return

    result = await _do_ingest(req)
    await _publish_completed(envelope, result)


async def _do_ingest(req: IngestRequest) -> IngestResult:
    """执行入库并更新指标。"""
    if repo is None:
        raise RuntimeError("service not initialized (qdrant repo missing)")
    result = await ingestor.ingest(req, repo)
    status = "degraded" if result.degraded else "ok"
    INGEST_COUNTER.labels(status=status).inc()
    INGEST_CHUNKS.inc(result.chunks_created)
    INGEST_POINTS.inc(result.points_upserted)
    INGEST_LATENCY.observe(result.elapsed_ms)
    return result


async def _publish_completed(orig: EventEnvelope, result: IngestResult) -> None:
    """发布 knowledge.ingest.completed 事件。"""
    if nats_client is None:
        return
    envelope = EventEnvelope(
        event_id=str(uuid.uuid4()),
        event_type=EventType.KNOWLEDGE_INGEST_COMPLETED,
        trace_id=orig.trace_id,
        tenant_id=orig.tenant_id,
        session_id=orig.session_id,
        producer=Producer(
            service="offline-knowledge-service",
            instance=os.getenv("HOSTNAME", "local"),
        ),
        routing=Routing(
            channel="knowledge",
            partition_key=orig.tenant_id,
        ),
        payload=result.model_dump(),
    )
    await nats_client.publish("agenthub.knowledge.ingest.completed", envelope)


# ── HTTP 端点 ────────────────────────────────────────────────────────
@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    nats_ok = nats_client.connected if nats_client else False
    qdrant_ok = await repo.health() if repo else False
    status = "ok" if (nats_ok and qdrant_ok) else "degraded"
    return {
        "status": status,
        "service": "offline-knowledge-service",
        "nats_connected": nats_ok,
        "qdrant_connected": qdrant_ok,
        "embedding_dim": embedding_dim,
    }


@app.get("/profile")
async def profile() -> dict:
    return {
        "service": "offline-knowledge-service",
        "responsibilities": [
            "knowledge ingestion (NATS event + HTTP sync)",
            "recursive text chunking",
            "embedding generation (via model-adapter /v1/embeddings)",
            "vector upsert to Qdrant (docs/code/memory/artifacts)",
        ],
        "subscriptions": [
            {"subject": "agenthub.knowledge.ingest.requested", "durable": "offline-knowledge-ingest"},
        ],
        "publishes": [
            {"subject": "agenthub.knowledge.ingest.completed"},
        ],
        "collections": settings.collections,
        "embedding_dim": embedding_dim,
        "embedding_model": settings.embedding_model,
    }


@app.post("/ingest", response_model=IngestResult)
async def ingest_sync(req: IngestRequest) -> IngestResult:
    """同步入库（debug / API 集成用）。NATS 事件走 _handle_ingest_event。"""
    try:
        return await _do_ingest(req)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error("sync ingest failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
