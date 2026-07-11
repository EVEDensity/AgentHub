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
from .retrieval_eval import evaluate_retrieval

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


@router.post("/evaluate-retrieval")
async def evaluate_retrieval_endpoint(
    body: dict[str, Any],
):
    """Run a retrieval evaluation with golden chunk IDs.

    Body:
      {
        query: str,
        collection: str = "docs",
        golden_chunk_ids: [str, ...],   // list of ground-truth chunk IDs
        k: int = 10                      // top-K to retrieve
      }

    Returns:
      {
        query, collection, k,
        metrics: { ndcg_at_5, ndcg_at_10, mrr, recall_at_5, recall_at_10,
                   precision_at_5, precision_at_10, ap },
        results: [{id, score, content, source_id, chunk_index, relevant}]  // relevant: bool
      }
    """
    query = body.get("query", "")
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    collection = body.get("collection", "docs")
    golden_chunk_ids = body.get("golden_chunk_ids", [])
    if not isinstance(golden_chunk_ids, list) or not golden_chunk_ids:
        raise HTTPException(status_code=400, detail="golden_chunk_ids must be a non-empty list")
    k = min(max(int(body.get("k", 10)), 1), 50)

    results = await _retrieval_test(query, collection, k)

    # Compute evaluation metrics
    golden_set = set(golden_chunk_ids)
    metrics = evaluate_retrieval(query, results, golden_chunk_ids)

    # Annotate each result with a relevance flag
    annotated_results = []
    for r in results:
        rid = str(r.get("id", ""))
        annotated_results.append({
            **r,
            "relevant": rid in golden_set,
        })

    return JSONResponse({
        "query": query,
        "collection": collection,
        "k": k,
        "metrics": {
            "ndcg_at_5": metrics["ndcg_at_5"],
            "ndcg_at_10": metrics["ndcg_at_10"],
            "mrr": metrics["mrr"],
            "recall_at_5": metrics["recall_at_5"],
            "recall_at_10": metrics["recall_at_10"],
            "precision_at_5": metrics["precision_at_5"],
            "precision_at_10": metrics["precision_at_10"],
            "ap": metrics["ap"],
        },
        "results": annotated_results,
    })


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


# ── RAG Search Routes (P1-1) ────────────────────────────────────────────

# Source type → Qdrant collection mapping
_SOURCE_TO_COLLECTION: dict[str, str] = {
    "project_docs": "docs",
    "api_docs": "docs",
    "uploaded_docs": "docs",
    "code_repos": "code",
    "sessions": "memory",
    "artifacts": "artifacts",
}

_SOURCE_TYPE_LABELS: dict[str, str] = {
    "project_docs": "project_docs",
    "api_docs": "api_docs",
    "uploaded_docs": "uploaded_docs",
    "code_repos": "code_repos",
    "sessions": "sessions",
    "artifacts": "artifacts",
}


def _generate_rewrites(query: str, limit: int = 3) -> list[str]:
    """Generate simple query rewrite variants without LLM.

    Uses suffix patterns to help the retrieval system find more
    relevant documents across different knowledge sources.
    """
    rewrites = [query]
    # Basic variants
    suffixes = [
        " 架构设计",
        " 实现原理",
        " 最佳实践",
        " 技术方案",
        " 使用方法",
    ]
    for suffix in suffixes[: limit - 1]:
        variant = query + suffix
        if variant != query:
            rewrites.append(variant)
    return rewrites[:limit]


def _extract_highlights(text: str, query_terms: list[str]) -> list[str]:
    """Extract highlighting fragments from text based on query terms."""
    highlights: list[str] = []
    text_lower = text.lower()
    for term in query_terms:
        term_lower = term.lower()
        if term_lower in text_lower and len(term) > 1:
            # Find the surrounding context (up to 50 chars)
            idx = text_lower.find(term_lower)
            start = max(0, idx - 20)
            end = min(len(text), idx + len(term) + 30)
            highlights.append(text[start:end].strip())
    return highlights[:5]  # max 5 highlight fragments


def _rrf_fusion(
    results_by_collection: dict[str, list[dict[str, Any]]],
    k: int = 60,
) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion: merge multiple result sets into one ranked list.

    RRF score = sum(1 / (k + rank_i)) for each result across collections.
    """
    scores: dict[str, dict[str, Any]] = {}
    for collection, results in results_by_collection.items():
        for rank, item in enumerate(results):
            key = f"{collection}:{item.get('id', '')}"
            if key not in scores:
                scores[key] = {
                    **item,
                    "collection": collection,
                    "rrf_rank_sum": 0.0,
                    "rrf_contributions": 0,
                }
            rrf_score = 1.0 / (k + rank + 1)
            scores[key]["rrf_rank_sum"] += rrf_score
            scores[key]["rrf_contributions"] += 1
            # Keep the highest individual score
            if item.get("score", 0) > scores[key].get("score", 0):
                scores[key]["score"] = item["score"]

    # Sort by RRF score descending
    merged = sorted(
        scores.values(),
        key=lambda x: x["rrf_rank_sum"],
        reverse=True,
    )
    return merged


@router.get("/rag-search")
async def rag_search(
    q: str = Query(..., description="Search query"),
    source: list[str] = Query(default=["project_docs"], description="Source types to search"),
    top_k: int = Query(default=10, ge=1, le=50, description="Max results"),
    include_images: bool = Query(default=True, description="Include image results"),
    time_range: str = Query(default="30d", description="Time range filter (7d/30d/90d/all)"),
    sort: str = Query(default="relevance", description="Sort mode (relevance/newest/oldest)"),
):
    """RAG hybrid search across knowledge sources with query rewriting and RRF fusion.

    Frontend: RAGDocViewer.tsx calls this endpoint.
    Returns: RAGSearchResponse { query, rewrites, results, images, fusion, latency_ms }
    """
    import time as _time
    t_start = _time.monotonic()

    if not q.strip():
        raise HTTPException(status_code=400, detail="q is required")

    # ── 1. Generate query rewrites ──────────────────────────────────
    rewrites = _generate_rewrites(q.strip())

    # ── 2. Map source types to collections ──────────────────────────
    target_collections = set()
    for s in source:
        coll = _SOURCE_TO_COLLECTION.get(s)
        if coll:
            target_collections.add(coll)
    if not target_collections:
        target_collections = {"docs"}

    # ── 3. Embed the primary query ─────────────────────────────────
    dim = _embedding_dim()
    try:
        query_vec = await embedding_client.get_embedding(q.strip())
    except Exception as e:
        logger.error("RAG search embedding failed: %s", e, exc_info=True)
        raise HTTPException(status_code=503, detail=f"Embedding service unavailable: {e}")

    # ── 4. Search across target collections ────────────────────────
    r = _repo()
    results_by_collection: dict[str, list[dict[str, Any]]] = {}
    search_k = max(top_k * 2, 20)  # Oversample for RRF

    for collection in target_collections:
        try:
            hits = await r.client.search(
                collection_name=collection,
                query_vector=query_vec,
                limit=search_k,
                with_payload=True,
                with_vectors=False,
            )
        except Exception as e:
            logger.warning("RAG search in collection %s failed: %s", collection, e)
            continue

        # Also try rewrites for better coverage
        for i, rewrite in enumerate(rewrites[1:], 1):  # skip first (original)
            try:
                rw_vec = await embedding_client.get_embedding(rewrite)
                rw_hits = await r.client.search(
                    collection_name=collection,
                    query_vector=rw_vec,
                    limit=max(5, top_k // 2),
                    with_payload=True,
                    with_vectors=False,
                )
                hits.extend(rw_hits)
            except Exception:
                pass  # rewrite search is best-effort

        # Deduplicate by ID within collection
        seen_ids: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for h in hits:
            hid = str(h.id)
            if hid not in seen_ids:
                seen_ids.add(hid)
                p = h.payload or {}
                # Determine source type from collection
                src_type = "project_docs"
                for st, sc in _SOURCE_TO_COLLECTION.items():
                    if sc == collection:
                        src_type = st
                        break

                query_terms = [w for w in q.strip().split(" ") if len(w) > 1]
                deduped.append({
                    "source_id": p.get("source_id", ""),
                    "chunk_id": str(p.get("chunk_index", h.id)),
                    "text": p.get("content", ""),
                    "score": round(h.score, 6),
                    "source_type": src_type,
                    "metadata": {
                        "file_path": p.get("file_path", ""),
                        "section": p.get("section", ""),
                        "file_type": p.get("file_type", ""),
                        "line": str(p.get("start_offset", "")) if p.get("start_offset") is not None else "",
                    },
                    "highlights": _extract_highlights(p.get("content", ""), query_terms),
                })
        if deduped:
            results_by_collection[collection] = deduped

    # ── 5. RRF fusion ─────────────────────────────────────────────
    fused = _rrf_fusion(results_by_collection, k=60)

    # Sort by relevance or time
    if sort == "newest":
        fused.sort(key=lambda x: x.get("metadata", {}).get("file_path", ""), reverse=True)
    elif sort == "oldest":
        fused.sort(key=lambda x: x.get("metadata", {}).get("file_path", ""))

    # Apply top_k limit
    results = fused[:top_k]

    # ── 6. Image search (if requested) ────────────────────────────
    images: list[dict[str, Any]] = []
    if include_images:
        from .config import settings as s
        for collection in target_collections:
            image_collection = f"{collection}_images"
            try:
                img_hits = await r.client.search(
                    collection_name=image_collection,
                    query_vector=query_vec,
                    limit=max(5, top_k // 3),
                    with_payload=True,
                    with_vectors=False,
                )
                for h in img_hits:
                    p = h.payload or {}
                    src_type = "project_docs"
                    for st, sc in _SOURCE_TO_COLLECTION.items():
                        if sc == collection:
                            src_type = st
                            break
                    images.append({
                        "id": str(h.id),
                        "url": p.get("url", ""),
                        "caption": p.get("caption", ""),
                        "score": round(h.score, 6),
                        "source_id": p.get("source_id", ""),
                        "source_type": src_type,
                        "width": p.get("width"),
                        "height": p.get("height"),
                    })
            except Exception:
                pass  # Image collection may not exist

    # ── 7. Build response ────────────────────────────────────────
    elapsed = (_time.monotonic() - t_start) * 1000

    return JSONResponse({
        "query": q.strip(),
        "rewrites": rewrites,
        "results": results,
        "images": sorted(images, key=lambda x: x["score"], reverse=True)[:top_k],
        "fusion": "rrf",
        "latency_ms": round(elapsed, 2),
    })
