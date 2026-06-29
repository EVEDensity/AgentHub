"""图片元数据抽取（Pillow）。

图片本身无文字层（除非 OCR）。当前仅提取元数据（尺寸、格式、模式），
为后续 OCR 扩展预留接口。若有 pytesseract 依赖且环境变量启用，则尝试 OCR。
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from ..models import ExtractedContent

logger = logging.getLogger(__name__)


async def extract(path: Path, max_chars: int = 0) -> ExtractedContent:
    """从图片提取元数据 + 可选 OCR 文本。"""
    return await asyncio.to_thread(_extract_sync, path, max_chars)


def _extract_sync(path: Path, max_chars: int) -> ExtractedContent:
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow not installed; image metadata unavailable")
        return ExtractedContent(
            text="", file_type="image",
            metadata={"error": "Pillow not installed"},
        )

    img = Image.open(str(path))
    meta = {
        "format": img.format,
        "width": img.width,
        "height": img.height,
        "mode": img.mode,
    }

    # 可选 OCR（需 pytesseract + tesseract-ocr 系统包）。
    ocr_text = ""
    if os.getenv("ENABLE_OCR", "false").lower() == "true":
        ocr_text = _try_ocr(img, max_chars)

    text = ocr_text if ocr_text else f"[Image: {img.format} {img.width}x{img.height}]"
    return ExtractedContent(
        text=text,
        file_type="image",
        char_count=len(text),
        metadata=meta,
    )


def _try_ocr(img, max_chars: int) -> str:
    """尝试 OCR，失败返回空串。"""
    try:
        import pytesseract

        text = pytesseract.image_to_string(img, lang="chi_sim+eng")
        if max_chars > 0:
            text = text[:max_chars]
        return text.strip()
    except ImportError:
        logger.debug("pytesseract not installed; OCR skipped")
    except Exception as e:
        logger.warning("OCR failed: %s", e)
    return ""
