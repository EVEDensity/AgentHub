"""纯文本抽取（直读文件内容）。

适用于 .txt / .md / .csv / .json / 源代码等所有文本类文件。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..models import ExtractedContent

logger = logging.getLogger(__name__)


async def extract(path: Path, max_chars: int = 0) -> ExtractedContent:
    """读取文本文件内容。"""
    return await asyncio.to_thread(_extract_sync, path, max_chars)


def _extract_sync(path: Path, max_chars: int) -> ExtractedContent:
    # 尝试 UTF-8，失败回退到 latin-1（best-effort）。
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="latin-1")
        logger.info("file %s not UTF-8, decoded as latin-1", path.name)

    truncated = False
    if max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars]
        truncated = True

    return ExtractedContent(
        text=text,
        file_type="text",
        char_count=len(text),
        metadata={"encoding": "utf-8" if not truncated else "utf-8", "truncated": truncated},
    )
