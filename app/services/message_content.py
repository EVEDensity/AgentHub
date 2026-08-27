"""Dual-track ``messages.content`` support (str | list of content parts).

Closes the MM-1 protocol slice: every request-side constructor may now send
either a plain string (legacy, unchanged) or a list of standard OpenAI-style
content parts. Shapes are validated *before* anything hits the wire so a
malformed list fails loudly instead of being silently serialized upstream;
image parts are gated on the vision-capability registry (fail-closed) per
ADR-0105.
"""

from __future__ import annotations

from typing import Any

from app.services.tools.multimodal.capability import (
    VisionUnsupportedError,
    assert_model_supports_images,
)


class ContentShapeError(ValueError):
    """Raised when a dual-track content value is neither str nor valid parts."""


def validate_dual_track_content(content: Any) -> None:
    """Validate the ``str | list[part]`` contract; raise :class:`ContentShapeError`.

    Accepts:
    * ``str`` — legacy single-track content, returned unchanged by callers;
    * ``list`` of dicts each shaped like one of::

        {"type": "text", "text": "..."}
        {"type": "image_url", "image_url": {"url": "...", ["detail": ...]}}

      Additional keys are ignored (forward compatibility).
    """
    if isinstance(content, str):
        return
    if not isinstance(content, list):
        raise ContentShapeError(
            f"content must be a string or a list of content parts, "
            f"got {type(content).__name__}"
        )
    for index, part in enumerate(content):
        if not isinstance(part, dict):
            raise ContentShapeError(
                f"content[{index}] must be a dict, got {type(part).__name__}"
            )
        ptype = part.get("type")
        if ptype == "text":
            if not isinstance(part.get("text"), str):
                raise ContentShapeError(f"content[{index}]: text part requires a 'text' string")
            continue
        if ptype == "image_url":
            inner = part.get("image_url")
            if not isinstance(inner, dict) or not isinstance(inner.get("url"), str):
                raise ContentShapeError(
                    f"content[{index}]: image_url part requires image_url.url string"
                )
            continue
        raise ContentShapeError(f"content[{index}]: unsupported content part type {ptype!r}")


def has_image_parts(content: Any) -> bool:
    """True when *content* carries at least one ``image_url`` part."""
    if not isinstance(content, list):
        return False
    return any(
        isinstance(part, dict) and part.get("type") == "image_url" for part in content
    )


def assert_parts_allowed_for_model(provider: str, model: str, content: Any) -> None:
    """Fail-closed gate: image parts require a vision-capable model (ADR-0105)."""
    if has_image_parts(content):
        assert_model_supports_images(provider, model)


def flatten_text_content(content: Any) -> str:
    """Best-effort plain-text projection used by mocks and char-length fallbacks.

    Text parts join on newlines; image parts become a bracketed placeholder
    (never their base64 payload — that would poison mock replies and token
    estimates exactly the way the frontend bug this replaces did).
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    lines: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            lines.append(str(part.get("text", "")))
        elif part.get("type") == "image_url":
            lines.append("[image]")
    return "\n".join(line for line in lines if line)


def anthropic_blocks_from_content(content: Any) -> list[dict[str, Any]]:
    """Convert dual-track content into Anthropic Messages API blocks.

    Plain strings wrap into a single text block; ``data:`` URIs become
    base64 sources, http(s) URLs pass through as url sources.
    """
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    blocks: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "text":
            blocks.append({"type": "text", "text": part.get("text", "")})
        elif ptype == "image_url":
            url = (part.get("image_url") or {}).get("url", "")
            if url.startswith("data:"):
                header, _, b64 = url.partition(",")
                mime = header[len("data:"):].split(";", 1)[0] or "image/png"
                blocks.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime, "data": b64},
                })
            else:
                blocks.append({
                    "type": "image",
                    "source": {"type": "url", "url": url},
                })
    return blocks


__all__ = [
    "ContentShapeError",
    "VisionUnsupportedError",
    "validate_dual_track_content",
    "has_image_parts",
    "assert_parts_allowed_for_model",
    "flatten_text_content",
    "anthropic_blocks_from_content",
]
