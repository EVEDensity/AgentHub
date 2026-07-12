"""文档处理编排器：download → extract → publish ingest event。

处理流程：
  1. 解析 file_ref：minio:// → 下载；本地路径 → 直接用。
  2. 调用 extractors.extract_file 抽取文本。
  3. 发布 knowledge.ingest.requested 事件（供 offline-knowledge-service 入库）。
  4. 返回 ProcessResult。

降级链：
  - MinIO 下载失败 → degraded，reason="download_failed"
  - 抽取无文本 → degraded，reason="empty_extraction"，不发布 ingest
  - NATS 发布失败 → degraded，reason="publish_failed"
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from pathlib import Path

from shared.events import EventEnvelope, EventType, Producer, Routing
from shared.nats_client import NatsClient

from .config import settings
from .extractors import detect_file_type, extract_file
from .minio_client import MinioClient, is_minio_ref
from .models import ExtractedContent, ProcessRequest, ProcessResult

logger = logging.getLogger(__name__)


async def process_document(
    req: ProcessRequest,
    nats_client: NatsClient | None = None,
    minio_client: MinioClient | None = None,
) -> ProcessResult:
    """处理一个文档：下载 → 抽取 → 发布 ingest 事件。

    Args:
        req: 处理请求。
        nats_client: NATS 客户端（None 时不发布 ingest 事件）。
        minio_client: MinIO 客户端（None 时仅支持本地路径）。

    Returns:
        ProcessResult。
    """
    request_id = req.request_id or str(uuid.uuid4())
    start = time.monotonic()

    # ── 1. 获取文件到本地路径 ─────────────────────────────────────────
    tmp_path: Path | None = None
    try:
        if is_minio_ref(req.file_ref):
            if minio_client is None:
                return _result(request_id, req, file_type="unknown", extracted_chars=0,
                               ingest_published=False, start=start,
                               degraded=True, reason="minio_unavailable")
            try:
                tmp_path = minio_client.download(req.file_ref)
            except Exception as e:
                logger.error("minio download failed for %s: %s", req.file_ref, e)
                return _result(request_id, req, file_type="unknown", extracted_chars=0,
                               ingest_published=False, start=start,
                               degraded=True, reason="download_failed")
            local_path = tmp_path
        else:
            local_path = Path(req.file_ref)
            if not local_path.exists():
                return _result(request_id, req, file_type="unknown", extracted_chars=0,
                               ingest_published=False, start=start,
                               degraded=True, reason="file_not_found")

        # ── 2. 抽取文本 ───────────────────────────────────────────────
        file_type = req.file_type or detect_file_type(local_path.name)
        try:
            content = await extract_file(local_path, file_type, settings.max_extract_chars)
        except Exception as e:
            logger.error("extraction failed for %s: %s", req.file_ref, e)
            return _result(request_id, req, file_type=file_type, extracted_chars=0,
                           ingest_published=False, start=start,
                           degraded=True, reason="extraction_failed")

        if not content.text.strip():
            return _result(request_id, req, file_type=content.file_type,
                           extracted_chars=0, ingest_published=False, start=start,
                           degraded=True, reason="empty_extraction")

        # ── 3. 发布 knowledge.ingest.requested ────────────────────────
        ingest_published = False
        if nats_client is not None and nats_client.connected:
            try:
                await _publish_ingest_request(nats_client, request_id, req, content)
                ingest_published = True
            except Exception as e:
                logger.error("failed to publish ingest request: %s", e)
                return _result(request_id, req, file_type=content.file_type,
                               extracted_chars=content.char_count, ingest_published=False,
                               start=start, degraded=True, reason="publish_failed")
        else:
            # NATS 不可用——文本已抽取但不入库。
            logger.warning("NATS unavailable; extracted text not ingested")

        return _result(
            request_id, req, file_type=content.file_type,
            extracted_chars=content.char_count, ingest_published=ingest_published,
            start=start,
        )
    finally:
        # 清理临时文件
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


async def _publish_ingest_request(
    nats_client: NatsClient,
    request_id: str,
    req: ProcessRequest,
    content: ExtractedContent,
) -> None:
    """发布 knowledge.ingest.requested 事件，把抽取的文本发给 offline-knowledge-service。"""
    # 合并 metadata：文档级 metadata + 抽取元数据（页数等）。
    meta = {**req.metadata}
    if content.pages:
        meta["pages"] = content.pages
    meta["original_file_type"] = content.file_type

    payload = {
        "request_id": request_id,
        "tenant_id": req.tenant_id,
        "source_id": req.source_id,
        "collection": req.collection,
        "content_type": "text/plain",
        "content": content.text,
        "metadata": meta,
        "chunking": req.chunking.model_dump(),
    }

    envelope = EventEnvelope(
        event_id=str(uuid.uuid4()),
        event_type=EventType.KNOWLEDGE_INGEST_REQUESTED,
        trace_id=request_id,
        tenant_id=req.tenant_id,
        session_id=req.source_id,  # document 没有会话上下文，用 source_id 代替
        producer=Producer(
            service="document-pipeline-service",
            instance=os.getenv("HOSTNAME", "local"),
        ),
        routing=Routing(
            channel="knowledge",
            partition_key=req.tenant_id,
        ),
        payload=payload,
    )
    await nats_client.publish("agenthub.knowledge.ingest.requested", envelope)
    logger.info(
        "published ingest request for %s (%d chars, type=%s)",
        req.source_id, content.char_count, content.file_type,
    )


def _result(
    request_id: str,
    req: ProcessRequest,
    *,
    file_type: str,
    extracted_chars: int,
    ingest_published: bool,
    start: float,
    degraded: bool = False,
    reason: str | None = None,
) -> ProcessResult:
    elapsed_ms = int((time.monotonic() - start) * 1000)
    return ProcessResult(
        request_id=request_id,
        tenant_id=req.tenant_id,
        source_id=req.source_id,
        file_type=file_type,
        extracted_chars=extracted_chars,
        ingest_published=ingest_published,
        degraded=degraded,
        degradation_reason=reason,
        elapsed_ms=elapsed_ms,
    )
