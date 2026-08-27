"""Vision turn assembly for the tool-call loop (MM-2/3/4 helpers, ADR-0105).

Extracted from ``tooling.py`` so the loop itself stays under the R4-4
file-size and complexity gates — this module owns everything "images inside
a model call":

* :func:`extract_screenshot_uris` — hijack screenshot payloads out of tool
  results BEFORE they reach the text context;
* :func:`describe_screenshot_fallback` — text-only-model degrade path that
  turns a screenshot into structured prose via the sub-vision-model tool;
* :func:`decide_turn_vision` — single async decision point computing what
  rides THIS call (attachment parts / queued screenshot parts / a describe
  note) and how many images to bill.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agenthub.tooling.vision")


def extract_screenshot_uris(tool_results: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    """Pull ``screenshot_base64`` payloads out of tool results (MM-4/ADR-0105).

    The raw base64 never enters the text context — it would be truncated to
    garbage by the result budget and pollute the prompt the way the old
    attachment pipeline did. Payloads return as data URIs for vision refeed;
    sanitized results carry a descriptive note instead.
    """
    from app.services.tools.multimodal.content_parts import MAX_IMAGES_PER_TURN

    uris: list[str] = []
    sanitized: list[dict[str, Any]] = []
    for r in tool_results:
        data = r.get("result")
        if (
            isinstance(data, dict)
            and data.get("screenshot_base64")
            and len(uris) < MAX_IMAGES_PER_TURN
        ):
            b64 = str(data["screenshot_base64"]).strip()
            uris.append(f"data:image/png;base64,{b64}")
            r = {
                **r,
                "result": {
                    **data,
                    "screenshot_base64": "",
                    "vision_note": "截图已暂存，将在下一轮作为视觉输入提供",
                },
            }
        sanitized.append(r)
    return uris, sanitized


async def describe_screenshot_fallback(data_uri: str) -> str:
    """Vision-degraded path: turn a screenshot URI into structured text via
    the sub-model ``image_describe`` tool. Failures degrade to a short note,
    never break the tool loop."""
    try:
        from app.services.tools.multimodal.tools import image_describe_handler

        out = await image_describe_handler(
            image_url=data_uri, prompt="描述这张浏览器截图的关键内容")
        if out.get("success"):
            result = out.get("result") or {}
            return str(result.get("description", ""))[:800] or "（无可用描述）"
        return f"（截图视觉描述失败：{out.get('error', '未知错误')}）"
    except Exception as exc:  # noqa: BLE001 — degrade path must never crash the loop
        logger.warning("describe_screenshot_fallback failed: %s", exc)
        return "（截图视觉描述暂不可用）"


async def decide_turn_vision(
    *,
    queued_vision: list[str],
    image_parts: list[dict[str, Any]],
    iteration: int,
    provider: str,
    model_name: str,
) -> tuple[list[dict[str, Any]], str, int]:
    """Decide what vision rides THIS model call.

    Returns ``(extra_parts, describe_note, billed_images)``:

    * queued screenshots prefer real parts on vision-capable models (MM-4);
      otherwise exactly one per turn degrades into a describe note via
      :func:`describe_screenshot_fallback`, the rest stay queued;
    * attachment ``image_parts`` ride only the FIRST iteration (MM-2);
    * ``billed_images`` counts what actually goes over the wire so
      ``fit_prompt`` reserves/bills consistently.

    Mutates ``queued_vision`` (pops consumed entries) by design — the caller
    owns one queue per tool-loop invocation.
    """
    from app.services.tools.multimodal.capability import supports_vision
    from app.services.tools.multimodal.content_parts import image_url_part

    extra_parts: list[dict[str, Any]] = []
    describe_note = ""
    if queued_vision and supports_vision(provider, model_name):
        extra_parts = [image_url_part(u) for u in queued_vision]
        queued_vision.clear()
    elif queued_vision:
        description = await describe_screenshot_fallback(queued_vision.pop(0))
        if description:
            describe_note = "\n\n【上一轮浏览器截图的视觉描述】\n" + description

    billed = len(extra_parts)
    if billed == 0 and iteration == 0:
        billed = len(image_parts)
    return extra_parts, describe_note, max(0, billed)
