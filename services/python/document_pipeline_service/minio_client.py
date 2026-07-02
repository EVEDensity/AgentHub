"""MinIO 对象存储客户端。

负责从 MinIO 下载文件到本地临时路径供抽取器使用，以及上传预览产物。
file_ref 格式：minio://<bucket>/<key>
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from .config import settings

logger = logging.getLogger(__name__)


class MinioClient:
    """MinIO 客户端封装。"""

    def __init__(self) -> None:
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from minio import Minio

            self._client = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
            )
        return self._client

    def ensure_bucket(self, bucket: str | None = None) -> None:
        """幂等建 bucket。"""
        bucket = bucket or settings.minio_bucket
        if not self.client.bucket_exists(bucket):
            self.client.make_bucket(bucket)
            logger.info("created minio bucket %s", bucket)

    def download(self, file_ref: str) -> Path:
        """从 MinIO 下载文件到临时路径。

        Args:
            file_ref: minio://<bucket>/<key>

        Returns:
            本地临时文件路径（调用方负责清理）。
        """
        parsed = urlparse(file_ref)
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")

        # 用原文件扩展名命名临时文件，方便抽取器按扩展名推断类型。
        suffix = Path(key).suffix
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.close()
        tmp_path = Path(tmp.name)

        self.client.fget_object(bucket, key, str(tmp_path))
        logger.info("downloaded %s → %s (%d bytes)", file_ref, tmp_path, tmp_path.stat().st_size)
        return tmp_path

    def upload(self, local_path: Path, bucket: str | None = None, key: str | None = None) -> str:
        """上传文件到 MinIO，返回 file_ref。"""
        bucket = bucket or settings.minio_bucket
        if key is None:
            key = local_path.name
        self.client.fput_object(bucket, key, str(local_path))
        return f"minio://{bucket}/{key}"

    def health(self) -> bool:
        """MinIO 健康检查。"""
        try:
            self.client.list_buckets()
            return True
        except Exception:
            return False


def parse_file_ref(file_ref: str) -> tuple[str, str]:
    """解析 file_ref，返回 (bucket, key)。仅处理 minio:// 协议。"""
    parsed = urlparse(file_ref)
    return parsed.netloc, parsed.path.lstrip("/")


def is_minio_ref(file_ref: str) -> bool:
    """判断 file_ref 是否为 MinIO 引用。"""
    return file_ref.startswith("minio://")
