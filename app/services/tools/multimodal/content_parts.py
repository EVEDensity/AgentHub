"""Multimodal content-part model — builders, validation, token accounting.

This is the single source of truth for "what an image looks like inside an
LLM message" across the codebase (chat pipeline M1, attachment pipeline M2,
tool vision feedback M4 all import from here).

Design decisions (see docs/architecture/components/multimodal.md):
* Wire format is the OpenAI-compatible content array — every provider we
  route through (OpenAI/Kimi/Zhipu/vLLM/Ollama-compat/new-api gateway)
  speaks it natively.
* Validation is fail-closed: unknown MIME types and oversized payloads are
  rejected BEFORE they reach the provider, with explicit errors that name
  the offending constraint.
* Token accounting is a conservative flat cost per image (measured 1049
  prompt tokens on moonshot-v1-8k for a ~700KB PNG; rounded up to 1024 is
  NOT conservative enough, so the constant is deliberately higher than any
  observed value minus text share).
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Any

# ── Policy constants ────────────────────────────────────────────────────

IMAGE_MIME_WHITELIST: frozenset[str] = frozenset({
    "image/png", "image/jpeg", "image/webp", "image/gif",
})

#: Hard cap per inline image payload (post-base64 data URI length).
MAX_IMAGE_URI_CHARS = 8 * 1024 * 1024  # 8 MB

#: Flat token cost charged per accepted image (conservative; measured value
#: was 1049 on moonshot-v1-8k including shared text tokens).
IMAGE_TOKEN_COST = 1280

#: Turn-level caps enforced by callers assembling messages.
MAX_IMAGES_PER_TURN = 4
MAX_IMAGE_BYTES_PER_TURN = 6 * 1024 * 1024

_DATA_URI_RE = re.compile(
    r"^data:(?P<mime>[a-zA-Z0-9.+/-]+);base64,(?P<data>[A-Za-z0-9+/=\s]+)$"
)


class MultimodalError(ValueError):
    """Raised when an image payload violates the multimodal policy."""


@dataclass(frozen=True)
class ImagePart:
    """One validated inline image, ready to embed into a content array."""

    mime: str
    data_uri: str
    size_bytes: int


# ── Builders ─────────────────────────────────────────────────────────────

def text_part(text: str) -> dict[str, Any]:
    """Build a standard ``{"type": "text"}`` content part."""
    return {"type": "text", "text": str(text or "")}


def image_url_part(url_or_data_uri: str, *, detail: str | None = None) -> dict[str, Any]:
    """Build a standard ``{"type": "image_url"}`` content part.

    Accepts an http(s) URL or a validated ``data:image/...;base64,...`` URI.
    Call :func:`validate_image_uri` first when the origin is untrusted.
    """
    part: dict[str, Any] = {
        "type": "image_url",
        "image_url": {"url": url_or_data_uri},
    }
    if detail:
        part["image_url"]["detail"] = detail
    return part


def parse_data_uri(uri: str) -> tuple[str, bytes]:
    """Split a ``data:<mime>;base64,<data>`` URI into ``(mime, raw_bytes)``."""
    match = _DATA_URI_RE.match(str(uri or "").strip())
    if not match:
        raise MultimodalError("invalid data URI (expected data:image/...;base64,...)")
    mime = match.group("mime").lower()
    try:
        return mime, base64.b64decode(match.group("data"), validate=False)
    except (binascii.Error, ValueError) as exc:
        raise MultimodalError(f"invalid base64 payload: {exc}") from exc


# ── Validation ───────────────────────────────────────────────────────────

def validate_image_bytes(mime: str, raw: bytes) -> ImagePart:
    """Validate raw image bytes against policy and build the ImagePart."""
    mime = (mime or "").lower()
    if mime not in IMAGE_MIME_WHITELIST:
        allowed = ", ".join(sorted(IMAGE_MIME_WHITELIST))
        raise MultimodalError(f"unsupported image type {mime!r} (allowed: {allowed})")
    if len(raw) > MAX_IMAGE_URI_CHARS * 3 // 4:
        raise MultimodalError(f"image exceeds {MAX_IMAGE_URI_CHARS // (1024 * 1024)} MB limit")
    encoded = base64.b64encode(raw).decode("ascii")
    uri = f"data:{mime};base64,{encoded}"
    if len(uri) > MAX_IMAGE_URI_CHARS:
        raise MultimodalError(f"data URI exceeds {MAX_IMAGE_URI_CHARS // (1024 * 1024)} MB character limit")
    return ImagePart(mime=mime, data_uri=uri, size_bytes=len(raw))


def validate_image_uri(uri: str) -> ImagePart:
    """Validate an existing data URI against policy (URLs pass through)."""
    if not str(uri).startswith("data:"):
        # Remote URLs are not decoded here; transports decide fetch policy.
        if not str(uri).startswith(("http://", "https://")):
            raise MultimodalError("image source must be http(s) URL or data URI")
        return ImagePart(mime="", data_uri=str(uri), size_bytes=0)
    mime, raw = parse_data_uri(uri)
    if mime not in IMAGE_MIME_WHITELIST:
        allowed = ", ".join(sorted(IMAGE_MIME_WHITELIST))
        raise MultimodalError(f"unsupported image type {mime!r} (allowed: {allowed})")
    if len(str(uri)) > MAX_IMAGE_URI_CHARS:
        raise MultimodalError(f"data URI exceeds {MAX_IMAGE_URI_CHARS // (1024 * 1024)} MB character limit")
    return ImagePart(mime=mime, data_uri=str(uri), size_bytes=len(raw))


# ── Content-array helpers ────────────────────────────────────────────────

def is_multimodal_content(content: Any) -> bool:
    """True when a message body is already a content array (not plain text)."""
    return isinstance(content, list)


def count_images_in_turn(parts: list[dict[str, Any]]) -> int:
    return sum(1 for p in parts if p.get("type") == "image_url")


def assert_turn_limits(parts: list[dict[str, Any]]) -> None:
    """Enforce per-turn image count / byte budget before sending."""
    images = [p for p in parts if p.get("type") == "image_url"]
    if len(images) > MAX_IMAGES_PER_TURN:
        raise MultimodalError(
            f"turn would carry {len(images)} images (limit {MAX_IMAGES_PER_TURN})")
    total = sum(len(str(p["image_url"].get("url", ""))) for p in images)
    if total > MAX_IMAGE_BYTES_PER_TURN:
        raise MultimodalError(
            f"turn image payload {total // (1024 * 1024)} MB exceeds "
            f"{MAX_IMAGE_BYTES_PER_TURN // (1024 * 1024)} MB budget")
