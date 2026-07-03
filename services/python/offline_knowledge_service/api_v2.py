"""api_v2 — Knowledge base CRUD + retrieval-test endpoints.

Provides collection management, document listing, chunk retrieval, deletion,
and a retrieval-test endpoint that embeds a query and searches Qdrant directly.
Mounted by main.py alongside the existing /ingest endpoint.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from . import embedding_client, qdrant_repo
from .config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="", tags=["knowledge-v2"])

# ── helpers ────────────────────────────────────────────────────────────

def _repo() -> qdrant_repo.QdrantRepo:
    """Get or create a QdrantRepo for read operations."""
    # main.py initializes a global `repo`; fall back to creating one locally.
    import main as _main
    if _main.repo is not None:
        return _main.repo
    return qdrant_repo.QdrantRepo()


def _embedding_dim() -> int:
    import main as _main
    if _main.embedding_dim > 0:
        return _main.embedding_dim
    return 384


async def _list_collections() -> list[dict[str, Any]]:
    """List Qdrant collections with point counts."""
    r = _repo()
    result = []
    try:
        collections = await r.client.get_collections()
        for c in collections:
            name = c.name if hasattr(c, "name") else str(c)
            try:
                info = await r.client.get_collection(name)
                points_count = getattr(info, "points_count", 0) or 0
            except Exception:
                points_count = 0
            result.append({"name": name, "points_count": points_count})
    except Exception as e:
        logger.error("failed to list collections: %s", e, exc_info=True)
    return result


async def _list_documents(collection: str, tenant_id: str = "") -> list[dict[str, Any]]:
    """List distinct documents (source_id groups) in a collection."""
    r = _repo()
    docs: dict[str, dict[str, Any]] = {}
    try:
        # Scroll through all points, grouping by source_id.
        offset = None
        while True:
            points, next_offset = await r.client.scroll(
                collection_name=collection,
                scroll_filter=None,
                limit=500,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for p in points:
                payload = p.payload or {}
                sid = payload.get("source_id", "")
                if not sid:
                    continue
                if sid not in docs:
                    docs[sid] = {
                        "source_id": sid,
                        "collection": collection,
                        "tenant_id": payload.get("tenant_id", tenant_id),
                        "chunk_count": 0,
                        "file_type": payload.get("file_type", ""),
                        "created_at": payload.get("timestamp", ""),
                    }
                docs[sid]["chunk_count"] += 1
            if next_offset is None:
                break
            offset = next_offset
    except Exception as e:
        logger.error("failed to list documents in %s: %s", collection, e, exc_info=True)

    return sorted(docs.values(), key=lambda d: d.get("created_at", ""), reverse=True)


async def _get_chunks(collection: str, source_id: str) -> list[dict[str, Any]]:
    """Get all chunks for a document."""
    r = _repo()
    chunks = []
    try:
        offset = None
        while True:
            points, next_offset = await r.client.scroll(
                collection_name=collection,
                scroll_filter={"must": [{"key": "source_id", "match": {"value": source_id}}]},
                limit=500,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for p in points:
                payload = p.payload or {}
                chunks.append({
                    "id": p.id,
                    "index": payload.get("chunk_index", 0),
                    "total": payload.get("chunk_total", 0),
                    "content": payload.get("content", ""),
                    "start_offset": payload.get("start_offset", 0),
                    "end_offset": payload.get("end_offset", 0),
                })
            if next_offset is None:
                break
            offset = next_offset
    except Exception as e:
        logger.error("failed to get chunks for %s/%s: %s", collection, source_id, e, exc_info=True)

    return sorted(chunks, key=lambda c: c["index"])


async def _delete_document(collection: str, source_id: str) -> int:
    """Delete all points for a document. Returns count of deleted points."""
    r = _repo()
    try:
        result = await r.client.delete(
            collection_name=collection,
            points_selector={"filter": {"must": [{"key": "source_id", "match": {"value": source_id}}]}},
        )
        deleted = getattr(result, "status", None)
        if hasattr(deleted, "int_") :
            deleted = deleted.int_
        elif isinstance(deleted, (int, float)):
            deleted = int(deleted)
        else:
            deleted = 0
        logger.info("deleted %s points for source_id=%s from %s", deleted, source_id, collection)
        return int(deleted)
    except Exception as e:
        logger.error("failed to delete %s from %s: %s", source_id, collection, e, exc_info=True)
        raise


async def _retrieval_test(
    query: str, collection: str, k: int = 5
) -> list[dict[str, Any]]:
    """Embed a query and search Qdrant, returning top-k results with scores."""
    dim = _embedding_dim()
    try:
        vec = await embedding_client.get_embedding(query)
    except Exception as e:
        logger.error("embedding failed for retrieval test: %s", e, exc_info=True)
        raise HTTPException(status_code=503, detail=f"Embedding service unavailable: {e}")

    r = _repo()
    try:
        hits = await r.client.search(
            collection_name=collection,
            query_vector=vec,
            limit=k,
            with_payload=True,
            with_vectors=False,
        )
    except Exception as e:
        logger.error("qdrant search failed: %s", e, exc_info=True)
        raise HTTPException(status_code=503, detail=f"Qdrant search failed: {e}")

    results = []
    for h in hits:
        p = h.payload or {}
        results.append({
            "id": h.id,
            "score": round(h.score, 6),
            "content": p.get("content", ""),
            "source_id": p.get("source_id", ""),
            "collection": collection,
            "chunk_index": p.get("chunk_index", 0),
        })
    return results


# ── Routes ─────────────────────────────────────────────────────────────

@router.get("/collections")
async def list_collections():
    """List Qdrant collections with point counts."""
    cols = await _list_collections()
    return JSONResponse({"collections": cols})


@router.get("/collections/{collection}/documents")
async def list_documents(
    collection: str,
    tenant_id: str = Query("", description="Tenant filter"),
):
    """List documents in a collection, grouped by source_id."""
    docs = await _list_documents(collection, tenant_id)
    return JSONResponse({"documents": docs, "collection": collection})


@router.get("/collections/{collection}/documents/{source_id}/chunks")
async def get_chunks(collection: str, source_id: str):
    """Get all chunks for a specific document."""
    chunks = await _get_chunks(collection, source_id)
    return JSONResponse({"chunks": chunks, "source_id": source_id, "collection": collection})


@router.delete("/collections/{collection}/documents/{source_id}")
async def delete_document(collection: str, source_id: str):
    """Delete a document and all its chunks."""
    try:
        deleted = await _delete_document(collection, source_id)
        return JSONResponse({"deleted": deleted, "source_id": source_id, "collection": collection})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/retrieval-test")
async def retrieval_test(
    body: dict[str, Any],
):
    """Run a retrieval test: embed query and search Qdrant.

    Body: { query: str, collection: str = "docs", k: int = 5 }
    """
    query = body.get("query", "")
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    collection = body.get("collection", "docs")
    k = min(max(int(body.get("k", 5)), 1), 50)
    results = await _retrieval_test(query, collection, k)
    return JSONResponse({"query": query, "collection": collection, "k": k, "results": results})


# ── Multimodal / Image Search Routes (P1-5) ───────────────────────────

@router.post("/image-search")
async def image_search(
    body: dict[str, Any],
):
    """Search for images by text description or image similarity.

    Body:
      {
        query: str,            // Text description of the desired image
        collection: str = "docs",
        k: int = 5,
        mode: "text" | "image" = "text"
      }

    Returns:
      { query, collection, k, results: [{id, score, source_id, caption, ...}] }
    """
    query = body.get("query", "")
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    collection = body.get("collection", "docs")
    k = min(max(int(body.get("k", 5)), 1), 50)
    mode = body.get("mode", "text")

    # Determine target collection (image vectors stored in {collection}_images)
    image_collection = f"{collection}_images"

    from . import multimodal_embedding_client as mec
    from .config import settings

    if not settings.multimodal_enabled:
        raise HTTPException(status_code=503, detail="Multimodal search is disabled")

    try:
        if mode == "text":
            # Text-to-image search: embed the text query and search image vectors
            vec = await mec.embed_text(query) if hasattr(mec, "embed_text") else None
            if vec is None:
                # Fall back to text embedding
                from . import embedding_client
                vec = await embedding_client.get_embedding(query)
        else:
            raise HTTPException(status_code=400, detail="Image-to-image search requires base64 image input")
    except Exception as e:
        logger.error("Image embedding failed: %s", e, exc_info=True)
        raise HTTPException(status_code=503, detail=f"Image embedding service unavailable: {e}")

    r = _repo()
    try:
        hits = await r.client.search(
            collection_name=image_collection,
            query_vector=vec,
            limit=k,
            with_payload=True,
            with_vectors=False,
        )
    except Exception as e:
        logger.error("Image search in Qdrant failed: %s", e, exc_info=True)
        # Graceful fallback: return empty results with a note
        return JSONResponse({
            "query": query,
            "collection": image_collection,
            "k": k,
            "mode": mode,
            "results": [],
            "warning": f"Image collection '{image_collection}' may not exist yet. Ingest images first.",
        })

    results = []
    for h in hits:
        p = h.payload or {}
        results.append({
            "id": h.id,
            "score": round(h.score, 6),
            "source_id": p.get("source_id", ""),
            "collection": image_collection,
            "caption": p.get("caption", ""),
            "file_type": p.get("file_type", ""),
            "width": p.get("width"),
            "height": p.get("height"),
            "url": p.get("url", ""),
        })
    return JSONResponse({
        "query": query,
        "collection": image_collection,
        "k": k,
        "mode": mode,
        "results": results,
    })


@router.post("/image-ingest")
async def image_ingest(
    body: dict[str, Any],
):
    """Ingest a single image into the knowledge base for later image search.

    Body:
      {
        image_data: str,      // base64-encoded image (data:image/...;base64,...)
        source_id: str,       // unique ID for this image
        collection: str = "docs",
        caption: str = "",    // optional caption/alt-text
        tenant_id: str = "default"
      }

    Returns:
      { status: "ok", source_id, points_upserted: 1 }
    """
    image_b64 = body.get("image_data", "")
    if not image_b64:
        raise HTTPException(status_code=400, detail="image_data (base64) is required")
    source_id = body.get("source_id", "")
    if not source_id:
        raise HTTPException(status_code=400, detail="source_id is required")
    collection = body.get("collection", "docs")
    caption = body.get("caption", "")
    tenant_id = body.get("tenant_id", "default")

    from . import multimodal_embedding_client as mec
    from .config import settings

    if not settings.multimodal_enabled:
        raise HTTPException(status_code=503, detail="Multimodal is disabled")

    # Strip data URI prefix if present
    if "base64," in image_b64:
        image_b64 = image_b64.split("base64,", 1)[1]

    try:
        vec = await mec.embed_image(image_b64)
    except Exception as e:
        logger.error("Image embedding for ingest failed: %s", e, exc_info=True)
        raise HTTPException(status_code=503, detail=f"Image embedding failed: {e}")

    # Determine image dimensions from base64 (best effort)
    import base64
    from io import BytesIO
    try:
        from PIL import Image as PILImage
        data = base64.b64decode(image_b64)
        img = PILImage.open(BytesIO(data))
        width, height = img.size
    except Exception:
        width, height = None, None

    # Store in image collection
    image_collection = f"{collection}_images"
    r = _repo()

    # Ensure the image collection exists (lazy creation)
    try:
        dim = mec.cached_image_dimension() or len(vec)
        await r.ensure_collection(image_collection, dim)
    except Exception as e:
        logger.warning("Could not ensure image collection %s: %s", image_collection, e)

    from datetime import datetime, timezone
    from qdrant_client.models import PointStruct

    ts = datetime.now(timezone.utc).isoformat()
    point_id = f"img-{source_id}"

    import uuid
    point = PointStruct(
        id=str(uuid.uuid5(uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890"), point_id)),
        vector=vec,
        payload={
            "source_id": source_id,
            "collection": image_collection,
            "tenant_id": tenant_id,
            "caption": caption,
            "file_type": "image",
            "width": width,
            "height": height,
            "timestamp": ts,
            "url": f"/api/knowledge/images/{source_id}",
        },
    )

    try:
        await r.client.upsert(collection_name=image_collection, points=[point], wait=True)
    except Exception as e:
        logger.error("Image ingest upsert failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Qdrant upsert failed: {e}")

    return JSONResponse({
        "status": "ok",
        "source_id": source_id,
        "collection": image_collection,
        "points_upserted": 1,
    })
