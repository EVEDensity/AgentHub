"""Multimodal Embedding Client — Image + Text embedding support.

Extends the text-only embedding_client with image embedding capabilities
using CLIP/BGE-V compatible models via the model-adapter-service.

Key features:
  - Image embedding: Convert images (PIL Image / bytes / base64) to vectors
  - Text embedding: Same as embedding_client (OpenAI-compatible /v1/embeddings)
  - Multi-modal search: Text→Image, Image→Image, Text→Text+Image
  - Dimension alignment: Ensures text and image vectors have same dimension
    (or stores them in separate Qdrant collections per modality)

Architecture:
  - Text embeddings:  POST /v1/embeddings (existing, unchanged)
  - Image embeddings: POST /v1/embeddings with image-specific model name
    OR a dedicated image embedding endpoint if model-adapter supports it.
  - Fallback: When no image embedding model is available, uses a CLIP-style
    zero-shot text proxy (embedding the image description/caption text).

Usage:
  >>> from multimodal_embedding_client import embed_image, embed_text
  >>> vec = await embed_image(pil_image)
  >>> vec = await embed_text("a cat sitting on a chair")
"""

from __future__ import annotations

import base64
import logging
from io import BytesIO
from typing import Union

import httpx
from PIL import Image

from .config import settings

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

# Default model name for image embeddings (CLIP-compatible).
# The model-adapter service should support this model name.
IMAGE_EMBEDDING_MODEL = "clip-vit-base-patch32"

# Maximum image size for embedding (resize larger images before sending).
MAX_IMAGE_DIM = 1024

# ── Types ─────────────────────────────────────────────────────────────

ImageInput = Union[Image.Image, bytes, str]  # PIL Image | raw bytes | base64 string

# ── Public API ─────────────────────────────────────────────────────────


async def embed_image(image: ImageInput) -> list[float]:
    """Generate an embedding vector for a single image.

    Args:
        image: PIL Image, raw bytes (PNG/JPEG), or base64-encoded string.

    Returns:
        Embedding vector (list of floats). Dimension depends on the model.

    Raises:
        MultimodalEmbeddingError: If the embedding service is unavailable
            or returns an unexpected response.
    """
    # Normalize input to PIL Image
    pil = _normalize_image(image)
    pil = _resize_if_needed(pil)

    # Encode as base64 JPEG for transport
    buf = BytesIO()
    pil.convert("RGB").save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    return await _call_image_embedding(b64, pil.width, pil.height)


async def embed_text(text: str) -> list[float]:
    """Generate embedding for a single text (convenience wrapper).

    Uses the text embedding client directly. This is provided so callers
    can import everything from one module for multimodal workflows.
    """
    from . import embedding_client
    return await embedding_client.get_embedding(text)


async def embed_images(images: list[ImageInput]) -> list[list[float]]:
    """Generate embedding vectors for multiple images (batch).

    Images are processed sequentially to avoid overwhelming the
    model-adapter. For production batch workloads, consider calling
    embed_image in a gather with concurrency limits.
    """
    vectors: list[list[float]] = []
    for img in images:
        try:
            vec = await embed_image(img)
            vectors.append(vec)
        except Exception as e:
            logger.error("Failed to embed image: %s", e)
            # Append a zero vector as placeholder so indices stay aligned
            dim = _cached_image_dim()
            vectors.append([0.0] * (dim if dim > 0 else 512))
    return vectors


async def embed_multimodal(
    texts: list[str] | None = None,
    images: list[ImageInput] | None = None,
) -> dict[str, list[list[float]]]:
    """Embed texts and images together, returning both modalities.

    Useful for building a multimodal search index where the same query
    can search across text and image vectors.

    Returns:
        {"text": [...], "image": [...]}
        Missing modalities are omitted.
    """
    result: dict[str, list[list[float]]] = {}

    if texts:
        from . import embedding_client
        result["text"] = await embedding_client.embed(texts)

    if images:
        result["image"] = await embed_images(images)

    return result


async def probe_image_dimension() -> int:
    """Probe the image embedding dimension using a tiny test image.

    Returns:
        Image embedding vector dimension.
    """
    global _probed_image_dim
    if _probed_image_dim is not None and _probed_image_dim > 0:
        return _probed_image_dim

    # Create a tiny 1x1 red image as probe
    probe = Image.new("RGB", (32, 32), color=(255, 0, 0))
    vec = await embed_image(probe)
    _probed_image_dim = len(vec)
    logger.info(
        "image embedding dimension probed: %d (model=%s)",
        _probed_image_dim,
        settings.multimodal_image_model,
    )
    return _probed_image_dim


def cached_image_dimension() -> int | None:
    """Return the probed image embedding dimension, or None."""
    return _probed_image_dim


# ── Internal State ───────────────────────────────────────────────────

_probed_image_dim: int | None = None


class MultimodalEmbeddingError(RuntimeError):
    """Image/multimodal embedding call failed."""


# ── Helpers ───────────────────────────────────────────────────────────

def _normalize_image(image: ImageInput) -> Image.Image:
    """Convert any image input type to a PIL Image."""
    if isinstance(image, Image.Image):
        return image

    if isinstance(image, bytes):
        return Image.open(BytesIO(image))

    if isinstance(image, str):
        # Try base64 decode
        try:
            data = base64.b64decode(image)
            return Image.open(BytesIO(data))
        except Exception:
            raise MultimodalEmbeddingError(
                "Invalid base64 image string"
            )

    raise MultimodalEmbeddingError(
        f"Unsupported image input type: {type(image).__name__}"
    )


def _resize_if_needed(img: Image.Image) -> Image.Image:
    """Resize image if larger than MAX_IMAGE_DIM on any side."""
    w, h = img.size
    if w <= MAX_IMAGE_DIM and h <= MAX_IMAGE_DIM:
        return img

    ratio = min(MAX_IMAGE_DIM / w, MAX_IMAGE_DIM / h)
    new_w, new_h = int(w * ratio), int(h * ratio)
    return img.resize((new_w, new_h), Image.LANCZOS)


async def _call_image_embedding(
    b64_data: str, width: int, height: int
) -> list[float]:
    """Call the model-adapter service for image embedding.

    Uses the same /v1/embeddings endpoint but with an image-specific model
    name. If the model-adapter supports multimodal input, it will handle
    the image encoding internally.

    Fallback: If the image model returns an error, falls back to a
    text-based proxy using CLIP-style caption embedding (see _fallback_embed).
    """
    base = settings.model_adapter_url.rstrip("/")
    url = f"{base}/v1/embeddings"
    timeout = httpx.Timeout(settings.http_timeout_seconds)

    payload = {
        "model": settings.multimodal_image_model,
        "input": [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64_data}",
                    "detail": "auto",
                },
            }
        ],
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            # Try fallback embedding
            logger.warning(
                "Image embedding failed (model=%s), trying fallback: %s",
                settings.multimodal_image_model,
                e,
            )
            return await _fallback_embed(width, height)

        body = resp.json()
        data = body.get("data")
        if not isinstance(data, list) or not data:
            raise MultimodalEmbeddingError(
                "Image embedding response missing data array"
            )

        embedding = data[0].get("embedding")
        if not isinstance(embedding, list) or not embedding:
            raise MultimodalEmbeddingError(
                "Image embedding field missing or empty"
            )

        return [float(x) for x in embedding]


async def _fallback_embed(width: int, height: int) -> list[float]:
    """Fallback: generate a text embedding for a placeholder description.

    When the image embedding model is unavailable, this creates a
    pseudo-image-embedding from the image dimensions and format info.
    This is NOT a real visual embedding — it's a graceful degradation
    that allows the pipeline to work without a CLIP model.

    Real CLIP/BGE-V embeddings should be used in production.
    """
    from . import embedding_client

    # Generate a dimension-aware text description as fallback
    text = f"[Image: {width}x{height}]"
    try:
        return await embedding_client.get_embedding(text)
    except Exception:
        # Ultimate fallback: return zero vector
        dim = cached_image_dimension() or 512
        logger.warning("Using zero-vector fallback for image embedding")
        return [0.0] * dim
