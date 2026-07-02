"""document-pipeline-service: PDF/DOCX/PPTX/图片 → 文本抽取 → 知识入库。

订阅 NATS `agenthub.knowledge.document.requested` 事件，从 MinIO 下载文档，
抽取纯文本，发布 `agenthub.knowledge.ingest.requested`（由 offline-knowledge-service
入库），完成后发布 `agenthub.knowledge.document.completed`。

也暴露 HTTP 端点供同步调用：
  - POST /extract: 上传文件 → 返回抽取文本（不入库，纯抽取测试用）
  - POST /process: 传 file_ref → 下载+抽取+发布 ingest → 返回 ProcessResult
"""

from __future__ import annotations

import logging
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from prometheus_client import Counter, Histogram, make_asgi_app

from shared.events import EventEnvelope, EventType, Producer, Routing
from shared.nats_client import NatsClient

from . import processor
from .config import settings
from .extractors import detect_file_type, extract_file
from .minio_client import MinioClient
from .models import ExtractedContent, ProcessRequest, ProcessResult

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ── Prometheus 指标 ──────────────────────────────────────────────────
PROCESS_COUNTER = Counter(
    "document_pipeline_process_total", "Total documents processed", ["status", "file_type"]
)
PROCESS_LATENCY = Histogram(
    "document_pipeline_process_latency_ms", "Document processing latency in ms"
)
EXTRACT_COUNTER = Counter(
    "document_pipeline_extract_total", "Total extract operations", ["file_type"]
)

# ── 全局句柄 ──────────────────────────────────────────────────────────
nats_client: NatsClient | None = None
minio_client: MinioClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global nats_client, minio_client

    # 1. MinIO 客户端（惰性连接，首次 download 时才实际建连）。
    minio_client = MinioClient()
    try:
        minio_client.ensure_bucket()
        logger.info("minio bucket ready: %s", settings.minio_bucket)
    except Exception as e:
        logger.warning("minio unavailable: %s (local file refs still work)", e)

    # 2. NATS 连接 + 订阅。
    nats_client = NatsClient(settings.nats_url)
    await nats_client.connect()

    async def on_document_requested(envelope: EventEnvelope):
        await _handle_document_event(envelope)

    await nats_client.subscribe(
        "document-pipeline-process",
        "agenthub.knowledge.document.requested",
        on_document_requested,
    )

    logger.info("document-pipeline-service started")
    yield

    if nats_client:
        await nats_client.close()


app = FastAPI(title="document-pipeline-service", version="0.2.0", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


# ── NATS 事件处理 ────────────────────────────────────────────────────
async def _handle_document_event(envelope: EventEnvelope) -> None:
    """knowledge.document.requested → process → publish knowledge.document.completed。"""
    p = envelope.payload
    try:
        req = ProcessRequest(
            request_id=p.get("request_id"),
            tenant_id=p.get("tenant_id", envelope.tenant_id),
            source_id=p.get("source_id", ""),
            file_ref=p.get("file_ref", ""),
            file_type=p.get("file_type"),
            collection=p.get("collection", "docs"),
            metadata=p.get("metadata") or {},
        )
    except Exception as e:
        logger.error("invalid document request payload: %s", e, exc_info=True)
        return

    result = await processor.process_document(req, nats_client, minio_client)
    _record_metrics(result)
    await _publish_completed(envelope, result)


async def _publish_completed(orig: EventEnvelope, result: ProcessResult) -> None:
    """发布 knowledge.document.completed 事件。"""
    if nats_client is None:
        return
    envelope = EventEnvelope(
        event_id=str(uuid.uuid4()),
        event_type=EventType.KNOWLEDGE_DOCUMENT_COMPLETED,
        trace_id=orig.trace_id,
        tenant_id=orig.tenant_id,
        session_id=orig.session_id,
        producer=Producer(
            service="document-pipeline-service",
            instance=os.getenv("HOSTNAME", "local"),
        ),
        routing=Routing(
            channel="knowledge",
            partition_key=orig.tenant_id,
        ),
        payload=result.model_dump(),
    )
    await nats_client.publish("agenthub.knowledge.document.completed", envelope)


def _record_metrics(result: ProcessResult) -> None:
    status = "degraded" if result.degraded else "ok"
    PROCESS_COUNTER.labels(status=status, file_type=result.file_type).inc()
    PROCESS_LATENCY.observe(result.elapsed_ms)


# ── HTTP 端点 ────────────────────────────────────────────────────────
@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    nats_ok = nats_client.connected if nats_client else False
    minio_ok = minio_client.health() if minio_client else False
    status = "ok" if nats_ok else "degraded"
    return {
        "status": status,
        "service": "document-pipeline-service",
        "nats_connected": nats_ok,
        "minio_connected": minio_ok,
    }


@app.get("/profile")
async def profile() -> dict:
    return {
        "service": "document-pipeline-service",
        "responsibilities": [
            "PDF/DOCX/PPTX/image text extraction",
            "MinIO artifact download",
            "publish knowledge.ingest.requested for offline-knowledge-service",
        ],
        "subscriptions": [
            {"subject": "agenthub.knowledge.document.requested", "durable": "document-pipeline-process"},
        ],
        "publishes": [
            {"subject": "agenthub.knowledge.ingest.requested"},
            {"subject": "agenthub.knowledge.document.completed"},
        ],
        "supported_types": ["pdf", "docx", "pptx", "image", "text"],
    }


@app.post("/extract", response_model=ExtractedContent)
async def extract_upload(file: UploadFile = File(...)) -> ExtractedContent:
    """上传文件 → 抽取文本（不入库，纯抽取测试用）。

    支持任意大小文件（受 starlette 限制），抽取结果受 max_extract_chars 限制。
    """
    # 保存上传文件到临时路径
    suffix = Path(file.filename or "").suffix
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(await file.read())
        tmp.close()
        tmp_path = Path(tmp.name)

        file_type = detect_file_type(file.filename or tmp_path.name)
        EXTRACT_COUNTER.labels(file_type=file_type).inc()

        content = await extract_file(tmp_path, file_type, settings.max_extract_chars)
        return content
    except Exception as e:
        logger.error("extract failed for %s: %s", file.filename, e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            Path(tmp.name).unlink(missing_ok=True)
        except Exception:
            pass


@app.post("/process", response_model=ProcessResult)
async def process_sync(req: ProcessRequest) -> ProcessResult:
    """同步处理：下载+抽取+发布 ingest 事件。"""
    try:
        result = await processor.process_document(req, nats_client, minio_client)
        _record_metrics(result)
        return result
    except Exception as e:
        logger.error("sync process failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
