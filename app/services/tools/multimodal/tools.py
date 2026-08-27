"""Concrete pluggable multimodal tools.

Currently provided:

* ``image_describe`` — structured text description of an image produced by a
  vision-capable sub-model. This is the degrade path that makes images
  useful TODAY even while the main chat model is text-only (the LangChain
  "structured extraction tool" pattern), and it doubles as the QA oracle
  for the MM-1 protocol slice.

Endpoint resolution (in order):
1. new-api gateway when ``AGENTHUB_LLM_GATEWAY=newapi``
   (``AGENTHUB_NEWAPI_BASE_URL`` / ``AGENTHUB_NEWAPI_API_KEY``)
2. any OpenAI-compatible endpoint via
   ``OPENAI_COMPATIBLE_BASE_URL`` / ``OPENAI_COMPATIBLE_API_KEY``
3. explicit error naming the missing configuration — never silent mock.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from app.utils.async_file import aread_bytes

from .capability import VisionUnsupportedError, assert_model_supports_images
from .content_parts import (
    IMAGE_TOKEN_COST,
    MultimodalError,
    image_url_part,
    parse_data_uri,
    text_part,
    validate_image_bytes,
    validate_image_uri,
)

logger = logging.getLogger("agenthub.tools.multimodal")

DEFAULT_DESCRIBE_MODEL = "moonshot-v1-8k-vision-preview"
_HTTP_TIMEOUT = 60.0

_SUFFIX_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
}


def _resolve_endpoint() -> tuple[str, str]:
    """Return ``(chat_completions_url, api_key)`` or raise with config help."""
    from app.config import LLM_GATEWAY, NEWAPI_API_KEY, NEWAPI_BASE_URL

    if (LLM_GATEWAY or "").strip().lower() == "newapi" and NEWAPI_API_KEY:
        return NEWAPI_BASE_URL.rstrip("/") + "/chat/completions", NEWAPI_API_KEY
    compat_base = os.getenv("OPENAI_COMPATIBLE_BASE_URL", "").strip()
    compat_key = os.getenv("OPENAI_COMPATIBLE_API_KEY", "").strip()
    if compat_base and compat_key:
        return compat_base.rstrip("/") + "/chat/completions", compat_key
    raise MultimodalError(
        "no vision endpoint configured: set AGENTHUB_LLM_GATEWAY=newapi + "
        "AGENTHUB_NEWAPI_* , or OPENAI_COMPATIBLE_BASE_URL/OPENAI_COMPATIBLE_API_KEY"
    )


def describe_model() -> str:
    return os.getenv("AGENTHUB_IMAGE_DESCRIBE_MODEL", DEFAULT_DESCRIBE_MODEL).strip()


def _resolve_provider_hint() -> str:
    from app.config import LLM_GATEWAY

    if (LLM_GATEWAY or "").strip().lower() == "newapi":
        return "newapi"
    return "openai-compatible"


async def _load_image_source(arguments: dict[str, Any]) -> str:
    """Resolve the tool's three input forms into a validated data URI / URL."""
    data_uri = str(arguments.get("image_base64") or "")
    if data_uri:
        if not data_uri.startswith("data:"):
            data_uri = f"data:image/png;base64,{data_uri}"
        return validate_image_uri(data_uri).data_uri

    url = str(arguments.get("image_url") or "")
    if url:
        validate_image_uri(url)
        return url

    path = str(arguments.get("image_path") or "")
    if path:
        candidate = Path(path)
        if not candidate.is_absolute():
            # Workspace-relative, matching file_read/file_write semantics.
            from app.services.workspace_context import get_workspace_root

            candidate = get_workspace_root() / candidate
        raw = await aread_bytes(candidate)
        mime = _SUFFIX_MIME.get(candidate.suffix.lower())
        if not mime:
            raise MultimodalError(
                f"unsupported file extension {candidate.suffix} "
                f"(allowed: {', '.join(sorted(_SUFFIX_MIME))})")
        return validate_image_bytes(mime, raw).data_uri

    raise MultimodalError("one of image_path / image_url / image_base64 is required")


def _describe_prompt_text(prompt: str) -> str:
    shape = (
        '{"description": string, "objects": [string], "text_in_image": string, '
        '"dominant_colors": [string], "table_present": boolean}'
    )
    user_bit = f'\nUser instruction to honour inside "description": {prompt}' if prompt else ""
    header = (
        "Analyze the attached image and answer strictly with one JSON object of this "
        f"shape:\n{shape}{user_bit}"
    )
    return header


def _extract_json_object(text: str) -> dict[str, Any]:
    """Best-effort JSON object extraction from a fenced or raw model reply."""
    candidates = [text.strip()]
    if "```" in text:
        for chunk in text.split("```"):
            stripped = chunk.strip()
            if stripped.startswith("{"):
                candidates.insert(0, stripped)
                break
    for candidate in candidates:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            continue
        try:
            parsed = json.loads(candidate[start:end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    raise ValueError("model reply contains no JSON object")


async def _call_vision_endpoint(
    url: str,
    api_key: str,
    model: str,
    image_part: dict[str, Any],
    prompt_text: str,
) -> tuple[str, dict[str, Any]]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [image_part, text_part(prompt_text)]}],
        "max_tokens": 800,
    }
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.post(url, headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }, json=payload)
    if resp.status_code >= 400:
        raise MultimodalError(f"vision endpoint HTTP {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    message = body["choices"][0]["message"]
    reply = str(message.get("content") or message.get("reasoning_content") or "")
    return reply, body.get("usage") or {}


async def image_describe_handler(
    image_path: str = "",
    image_url: str = "",
    image_base64: str = "",
    prompt: str = "",
    detail: str = "",
) -> dict[str, Any]:
    """Describe an image through a vision-capable sub-model; returns TEXT.

    The main (possibly text-only) agent consumes the structured description
    like any other tool result — no upstream protocol changes required.
    """
    try:
        provider_hint = _resolve_provider_hint()
        model = describe_model()
        assert_model_supports_images(provider_hint, model)

        data_uri = await _load_image_source({
            "image_path": image_path, "image_url": image_url,
            "image_base64": image_base64,
        })
        validate_image_uri(data_uri)

        url, api_key = _resolve_endpoint()
        part = image_url_part(data_uri, detail=detail or None)
        mime, raw = ("", b"")
        if data_uri.startswith("data:"):
            mime, raw = parse_data_uri(data_uri)

        reply, usage = await _call_vision_endpoint(
            url, api_key, model, part, _describe_prompt_text(prompt))
        try:
            structured = _extract_json_object(reply)
        except ValueError:
            structured = {"description": reply}
        return {
            "success": True,
            "result": {
                "description": structured.get("description", ""),
                "objects": structured.get("objects", []),
                "text_in_image": structured.get("text_in_image", ""),
                "dominant_colors": structured.get("dominant_colors", []),
                "table_present": bool(structured.get("table_present", False)),
            },
            "metadata": {
                "vision_model": model,
                "image_mime": mime,
                "image_size_bytes": len(raw),
                "estimated_image_tokens": IMAGE_TOKEN_COST,
                "usage": usage,
            },
        }
    except (MultimodalError, VisionUnsupportedError) as exc:
        return {"success": False, "error": str(exc)}
    except Exception as exc:
        logger.exception("image_describe failed")
        return {"success": False, "error": f"image_describe failed: {exc}"}


# ── Tool schema (kept beside the handler; converted to ToolDefinition by
#    the modality plugin at registration time) ──────────────────────────

IMAGE_DESCRIBE_PARAMETERS = [
    {"name": "image_path", "type": "string", "required": False,
     "description": "工作区内图片的相对路径（支持 png/jpg/jpeg/webp/gif）"},
    {"name": "image_url", "type": "string", "required": False,
     "description": "公开可访问的图片 http(s) 地址"},
    {"name": "image_base64", "type": "string", "required": False,
     "description": "base64 图片数据（可含 data:image/...;base64, 前缀）"},
    {"name": "prompt", "type": "string", "required": False,
     "description": "对图片描述的额外要求，会合并进结构化输出指令"},
    {"name": "detail", "type": "string", "required": False,
     "description": "视觉细节级别：high / low / auto"},
]
