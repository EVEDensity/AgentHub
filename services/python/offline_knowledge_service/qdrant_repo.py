"""Qdrant 写入侧：collection 幂等建表 + point upsert。

payload 字段约定与 Rust retrieval-core 读侧（qdrant.rs）严格对齐：
  - content: str        文本片段（读侧必需，缺失则空串）
  - source_id: str      文档/分片 ID（读侧必需，缺失则回退 point id）
  - timestamp: str      RFC3339，用于 freshness 打分（读侧必需）
  - tenant_id: str      多租户过滤（读侧预留 _tenant_id 参数）
  - chunk_index: int    分块序号
  - chunk_total: int    总块数
  - 其余 metadata 字段扁平化进 payload

point id 用 UUID5(source_id + chunk_index) 确定性生成，保证同一文档重复入库
是幂等 upsert 而非追加。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, PointStruct, VectorParams

from .config import settings
from .models import Chunk

logger = logging.getLogger(__name__)

# UUID5 命名空间（固定），保证跨进程/跨实例生成的 point id 一致。
_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def _point_id(source_id: str, chunk_index: int) -> str:
    """确定性 point id：UUID5(namespace, source_id + ":" + chunk_index)。
    同一文档重新入库时，相同 chunk_index 会命中同一 point id → 幂等 upsert。
    """
    key = f"{source_id}:{chunk_index}"
    return str(uuid.uuid5(_NAMESPACE, key))


def _now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat()


class QdrantRepo:
    """Qdrant 写入仓储。"""

    def __init__(self, url: str | None = None) -> None:
        self._url = url or settings.qdrant_url
        self._client: AsyncQdrantClient | None = None

    @property
    def client(self) -> AsyncQdrantClient:
        if self._client is None:
            self._client = AsyncQdrantClient(url=self._url)
        return self._client

    async def ensure_collection(self, name: str, dim: int) -> None:
        """幂等建表。若 collection 不存在则创建；若存在但维度不匹配则重建。

        重建会清空旧数据——用于 embedding 模型切换导致维度变化的场景。
        """
        c = self.client
        try:
            info = await c.get_collection(collection_name=name)
            existing_dim = _extract_dim(info)
            if existing_dim == dim:
                logger.debug("collection %s exists with dim=%d", name, dim)
                return
            logger.warning(
                "collection %s dim mismatch (existing=%d, want=%d); recreating",
                name,
                existing_dim,
                dim,
            )
            await c.delete_collection(collection_name=name)
        except UnexpectedResponse as e:
            # 404 → collection 不存在，正常流程，继续创建。
            if e.status_code != 404:
                raise
        except Exception:
            # 其他异常（如网络）保守不删，尝试直接创建，若已存在 Qdrant 会报错。
            logger.warning("could not inspect collection %s, will try create", name, exc_info=True)

        await c.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        logger.info("created qdrant collection %s (dim=%d, distance=cosine)", name, dim)

    async def ensure_all_collections(self, dim: int) -> None:
        """对 settings.collections 中所有 collection 幂等建表。"""
        for name in settings.collections:
            try:
                await self.ensure_collection(name, dim)
            except Exception:
                logger.error("failed to ensure collection %s", name, exc_info=True)

    async def upsert_chunks(
        self,
        collection: str,
        tenant_id: str,
        source_id: str,
        chunks: list[Chunk],
        vectors: list[list[float]],
        metadata: dict[str, Any],
        timestamp: str | None = None,
    ) -> int:
        """把 chunk + vector 写入 Qdrant。

        Args:
            collection: 目标 collection 名。
            tenant_id: 租户 ID（写入 payload 供过滤）。
            source_id: 文档 ID（生成 point id + 写入 payload）。
            chunks: 分块列表（与 vectors 等长）。
            vectors: 向量列表（与 chunks 等长）。
            metadata: 文档级元数据（扁平化进每个 point payload）。
            timestamp: RFC3339 时间戳；None 用当前时间。

        Returns:
            成功 upsert 的 point 数。
        """
        if len(chunks) != len(vectors):
            raise ValueError(
                f"chunks ({len(chunks)}) and vectors ({len(vectors)}) length mismatch"
            )
        if not chunks:
            return 0

        ts = timestamp or _now_rfc3339()
        total = len(chunks)
        points: list[PointStruct] = []
        for chunk, vec in zip(chunks, vectors):
            payload: dict[str, Any] = {
                "content": chunk.text,
                "source_id": source_id,
                "timestamp": ts,
                "tenant_id": tenant_id,
                "chunk_index": chunk.index,
                "chunk_total": total,
                "start_offset": chunk.start_offset,
                "end_offset": chunk.end_offset,
            }
            # 扁平化文档级 metadata（不覆盖上述保留字段）。
            for k, v in metadata.items():
                if k not in payload:
                    payload[k] = v
            points.append(
                PointStruct(
                    id=_point_id(source_id, chunk.index),
                    vector=vec,
                    payload=payload,
                )
            )

        await self.client.upsert(collection_name=collection, points=points, wait=True)
        logger.info(
            "upserted %d points into %s (source=%s tenant=%s)",
            len(points),
            collection,
            source_id,
            tenant_id,
        )
        return len(points)

    async def health(self) -> bool:
        """Qdrant 健康检查。"""
        try:
            await self.client.get_collections()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


def _extract_dim(info: Any) -> int:
    """从 CollectionInfo 提取向量维度。qdrant-client 版本间结构略有差异，防御性解析。"""
    params = getattr(info, "config", None)
    params = getattr(params, "params", None) if params is not None else None
    vectors = getattr(params, "vectors", None) if params is not None else None
    # vectors 可能是 VectorParams 或 dict {default: VectorParams}
    if vectors is None:
        return -1
    # 单向量配置：VectorParams 对象
    size = getattr(vectors, "size", None)
    if size is not None:
        return int(size)
    # dict 形式
    if isinstance(vectors, dict):
        for v in vectors.values():
            s = getattr(v, "size", None)
            if s is not None:
                return int(s)
    return -1
