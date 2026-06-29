"""文档管线数据模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChunkingConfig(BaseModel):
    """分块配置（透传给 offline-knowledge-service）。"""

    strategy: str = "recursive"
    chunk_size: int = 512
    overlap: int = 64


class ExtractedContent(BaseModel):
    """抽取结果。"""

    text: str
    file_type: str  # pdf / docx / pptx / image / text
    pages: int = 0  # PDF/PPTX 页/幻灯片数
    char_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProcessRequest(BaseModel):
    """文档处理请求（NATS 事件 payload 或 HTTP body）。"""

    request_id: str | None = None
    tenant_id: str
    source_id: str
    file_ref: str  # minio://bucket/key 或本地路径（测试用）
    file_type: str | None = None  # None 时按扩展名推断
    collection: str = "docs"
    metadata: dict[str, Any] = Field(default_factory=dict)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)


class ProcessResult(BaseModel):
    """文档处理结果。"""

    request_id: str
    tenant_id: str
    source_id: str
    file_type: str
    extracted_chars: int
    ingest_published: bool
    degraded: bool = False
    degradation_reason: str | None = None
    elapsed_ms: int = 0
