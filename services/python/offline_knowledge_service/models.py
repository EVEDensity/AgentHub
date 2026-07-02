"""离线知识服务数据模型。

与 platform/contracts/event-catalog.json 中 knowledge.* 事件 payload 对齐。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChunkingConfig(BaseModel):
    """分块参数。strategy 当前仅支持 recursive。"""

    strategy: str = "recursive"
    chunk_size: int = 512
    overlap: int = 64


class IngestRequest(BaseModel):
    """入库请求。可由 NATS 事件 knowledge.ingest.requested 的 payload 构造，
    也可由 HTTP POST /ingest 同步触发。"""

    request_id: str | None = None  # None 时由 ingestor 自动生成 UUID
    tenant_id: str
    source_id: str  # 文档/分片唯一 ID
    collection: str  # docs / code / memory / artifacts
    content_type: str = "text/plain"
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)


class Chunk(BaseModel):
    """分块结果。"""

    text: str
    index: int  # 在原文中的序号（0-based）
    start_offset: int  # 原文字符偏移
    end_offset: int


class IngestResult(BaseModel):
    """入库结果。对应 knowledge.ingest.completed 事件 payload。"""

    request_id: str
    tenant_id: str
    source_id: str
    collection: str
    chunks_created: int
    points_upserted: int
    embedding_dim: int
    elapsed_ms: int
    degraded: bool = False
    degradation_reason: str | None = None
