"""文档抽取器注册表。

按文件扩展名分发到对应抽取器。每个抽取器是 async 函数：
    async def extract(path: Path, max_chars: int) -> ExtractedContent

抽取器内部用 asyncio.to_thread 包装同步库调用（pypdf/python-docx/python-pptx 均为同步）。
"""

from __future__ import annotations

from pathlib import Path

from ..models import ExtractedContent
from . import docx, image, pdf, pptx, text

# 扩展名 → 抽取器映射
_REGISTRY: dict[str, str] = {
    # PDF
    ".pdf": "pdf",
    # DOCX
    ".docx": "docx",
    ".doc": "docx",
    # PPTX
    ".pptx": "pptx",
    ".ppt": "pptx",
    # 图片
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".bmp": "image",
    ".webp": "image",
    # 文本
    ".txt": "text",
    ".md": "text",
    ".markdown": "text",
    ".csv": "text",
    ".tsv": "text",
    ".json": "text",
    ".yaml": "text",
    ".yml": "text",
    ".xml": "text",
    ".html": "text",
    ".htm": "text",
    ".log": "text",
    ".py": "text",
    ".js": "text",
    ".ts": "text",
    ".go": "text",
    ".rs": "text",
    ".java": "text",
    ".c": "text",
    ".cpp": "text",
    ".h": "text",
    ".sh": "text",
    ".sql": "text",
}

_EXTRACTORS = {
    "pdf": pdf.extract,
    "docx": docx.extract,
    "pptx": pptx.extract,
    "image": image.extract,
    "text": text.extract,
}


def detect_file_type(filename: str) -> str:
    """根据文件名扩展名推断文件类型。未知扩展名返回 'binary'。"""
    ext = Path(filename).suffix.lower()
    return _REGISTRY.get(ext, "binary")


async def extract_file(path: Path, file_type: str | None = None, max_chars: int = 0) -> ExtractedContent:
    """根据文件类型分发到对应抽取器。

    Args:
        path: 本地文件路径。
        file_type: 文件类型（pdf/docx/pptx/image/text）。None 时按扩展名推断。
        max_chars: 最大抽取字符数。0 = 不限。

    Returns:
        ExtractedContent。
    """
    if file_type is None:
        file_type = detect_file_type(path.name)

    extractor = _EXTRACTORS.get(file_type)
    if extractor is None:
        # 未知类型当文本读（best-effort）。
        return await text.extract(path, max_chars)

    return await extractor(path, max_chars)
