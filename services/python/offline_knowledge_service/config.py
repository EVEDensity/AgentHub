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

    # ── 多模态 (P1-5) ────────────────────────────────────────────────
    # 图片 embedding 模型名（model-adapter 需支持此模型）。
    multimodal_image_model: str = "clip-vit-base-patch32"
    # 是否启用多模态（图片 embedding + 图片搜索）。
    multimodal_enabled: bool = True
    # 图片 collection 后缀（实际 collection 名为 {name}_images）。
    image_collection_suffix: str = "_images"
    # 最大图片尺寸（像素，超出会等比缩放）。
    max_image_dim: int = 1024

    @property
    def collections(self) -> list[str]:
        return [c.strip() for c in self.collections_csv.split(",") if c.strip()]

    @property
    def image_collections(self) -> list[str]:
        """Return the list of image collection names."""
        return [c + self.image_collection_suffix for c in self.collections]


settings = Settings()
