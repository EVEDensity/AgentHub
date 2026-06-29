"""文档管线服务配置。"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """document-pipeline-service 配置。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── 下游服务 ──────────────────────────────────────────────────────
    nats_url: str = "nats://127.0.0.1:4222"
    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = "minio"
    minio_secret_key: str = "minio123"
    minio_bucket: str = "agenthub"
    minio_secure: bool = False

    # ── 抽取限制 ──────────────────────────────────────────────────────
    # 单文件最大字符数（防止超大文档打爆下游）。0 = 不限。
    max_extract_chars: int = 500_000
    # 预览产物大小上限（MB）。
    preview_max_mb: int = 20

    # ── HTTP ──────────────────────────────────────────────────────────
    http_timeout_seconds: float = 60.0


settings = Settings()
