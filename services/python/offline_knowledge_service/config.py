"""离线知识服务配置。

所有值从环境变量读取（docker-compose 注入），提供合理默认值便于本地运行。
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """offline-knowledge-service 配置。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── 下游服务地址 ──────────────────────────────────────────────────
    nats_url: str = "nats://127.0.0.1:4222"
    qdrant_url: str = "http://127.0.0.1:6333"
    model_adapter_url: str = "http://127.0.0.1:8091"
    embedding_model: str = "bge-large-zh-v1.5"

    # ── HTTP 客户端 ───────────────────────────────────────────────────
    http_timeout_seconds: float = 30.0

    # ── Embedding ────────────────────────────────────────────────────
    # 单次 /v1/embeddings 请求批量大小（model-adapter 支持列表输入）。
    embedding_batch_size: int = 32

    # ── 分块 ─────────────────────────────────────────────────────────
    chunk_size: int = 512  # 字符数
    chunk_overlap: int = 64

    # ── Qdrant collection ───────────────────────────────────────────
    # 逗号分隔的 collection 列表，启动时幂等建表。
    collections_csv: str = "docs,code,memory,artifacts"

    # ── 限流 ─────────────────────────────────────────────────────────
    # 单次 ingestion 最大 chunk 数，防止超大文档打爆下游。
    max_chunks_per_ingest: int = 500

    @property
    def collections(self) -> list[str]:
        return [c.strip() for c in self.collections_csv.split(",") if c.strip()]


settings = Settings()
