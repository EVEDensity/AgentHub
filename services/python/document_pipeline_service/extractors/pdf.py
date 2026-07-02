"""PDF 文本抽取（pypdf）。

pypdf 是纯 Python 实现，无系统依赖，适合容器化部署。对扫描件（无文字层）
只能拿到空串——后续可扩展 OCR（pytesseract / paddleocr）。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..models import ExtractedContent

logger = logging.getLogger(__name__)


async def extract(path: Path, max_chars: int = 0) -> ExtractedContent:
    """从 PDF 抽取纯文本。

    Args:
        path: PDF 文件路径。
        max_chars: 最大字符数。0 = 不限。

    Returns:
        ExtractedContent，file_type="pdf"，pages=页数。
    """
    return await asyncio.to_thread(_extract_sync, path, max_chars)


def _extract_sync(path: Path, max_chars: int) -> ExtractedContent:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = len(reader.pages)

    parts: list[str] = []
    total = 0
    for i, page in enumerate(reader.pages):
        try:
            page_text = page.extract_text() or ""
        except Exception as e:
            logger.warning("failed to extract page %d of %s: %s", i, path.name, e)
            page_text = ""
        parts.append(page_text)
        total += len(page_text)
        if max_chars > 0 and total >= max_chars:
            # 截断到 max_chars
            combined = "\n".join(parts)
            return ExtractedContent(
                text=combined[:max_chars],
                file_type="pdf",
                pages=pages,
                char_count=min(total, max_chars),
                metadata={"truncated": True},
            )

    text = "\n".join(parts)
    return ExtractedContent(
        text=text,
        file_type="pdf",
        pages=pages,
        char_count=len(text),
    )
