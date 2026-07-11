"""递归文本分块器。

策略：按分隔符层级递归切分（段落 → 行 → 句子 → 词 → 字符），再把小片段合并到
接近 chunk_size 的块，相邻块之间保留 overlap 个字符的重叠，保证语义连续性。

这与 LangChain RecursiveCharacterTextSplitter 思路一致，但自包含实现，不引入
额外依赖。
"""

from __future__ import annotations

from typing import Sequence

from .models import Chunk

# 分隔符层级：从强到弱。强分隔符优先（保留段落边界），不够再降级。
_DEFAULT_SEPARATORS: tuple[str, ...] = (
    "\n\n\n",  # 多空行（章节边界）
    "\n\n",  # 段落
    "\n",  # 行
    "。",  # 中文句号
    ". ",  # 英文句号
    "！",  # 中文感叹号
    "! ",  # 英文感叹号
    "？",  # 中文问号
    "? ",  # 英文问号
    "；",  # 中文分号
    "; ",  # 英文分号
    "，",  # 中文逗号
    ", ",  # 英文逗号
    " ",  # 空格
    "",  # 兜底：逐字符
)


def split_text(
    text: str,
    chunk_size: int = 512,
    overlap: int = 64,
    separators: tuple[str, ...] | None = None,
    document_id: str = "",
) -> list[Chunk]:
    """把 text 切成若干 Chunk。

    Args:
        text: 原文。
        chunk_size: 每块目标最大字符数。
        overlap: 相邻块重叠字符数（< chunk_size）。
        separators: 自定义分隔符层级；None 用默认。
        document_id: 文档/源 ID，用于生成确定性 chunk_id。

    Returns:
        list[Chunk]，每个含 text / index / start_offset / end_offset /
        chunk_id / prev_chunk_id / next_chunk_id。
        偏移量基于原文。空文本返回空列表。
    """
    if not text:
        return []
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0:
        raise ValueError("overlap must be non-negative")
    if overlap >= chunk_size:
        # overlap 不能 >= chunk_size，否则合并逻辑会死循环。
        overlap = chunk_size // 4

    seps = separators if separators is not None else _DEFAULT_SEPARATORS

    # 第一步：递归切分成原子片段（每片 <= chunk_size，保留分隔符）。
    raw_pieces = _recursive_split(text, chunk_size, seps)

    # 第二步：合并原子片段到接近 chunk_size 的块，带 overlap。
    chunks = _merge_with_overlap(raw_pieces, chunk_size, overlap)

    # 第三步：计算每块在原文中的偏移量。
    result: list[Chunk] = []
    search_from = 0
    for i, chunk_text in enumerate(chunks):
        # 去掉合并时可能引入的首尾空白用于定位，但保留 chunk 文本原样。
        # 用 chunk 首个非空白字符定位 start，避免空白漂移。
        stripped = chunk_text.strip()
        if not stripped:
            continue
        # 在原文中查找该块（从 search_from 开始，容忍 overlap 导致的重复定位）。
        pos = text.find(stripped[: min(40, len(stripped))], search_from)
        if pos == -1:
            # fallback：从头找
            pos = text.find(stripped[: min(40, len(stripped))])
        if pos == -1:
            pos = search_from
        start = pos
        end = start + len(chunk_text)
        result.append(Chunk(text=chunk_text, index=i, start_offset=start, end_offset=end))
        # 下次搜索从本块中部开始，容忍 overlap 重叠。
        search_from = max(search_from + 1, start + max(1, len(chunk_text) // 2))

    # 第四步：生成确定性 chunk ID 并链接相邻 chunk。
    _assign_chunk_ids(result, document_id)

    return result


def _recursive_split(
    text: str,
    chunk_size: int,
    separators: tuple[str, ...],
) -> list[str]:
    """递归切分。返回的片段保留分隔符，每片 <= chunk_size（兜底字符级除外）。"""
    if len(text) <= chunk_size:
        return [text]

    # 找第一个能切分的分隔符。
    for idx, sep in enumerate(separators):
        if sep == "":
            # 兜底：按字符硬切。
            return _hard_split(text, chunk_size)
        if sep not in text:
            continue
        # 用 sep 切分，保留 sep 在片段末尾。
        parts = text.split(sep)
        pieces: list[str] = []
        for j, part in enumerate(parts):
            if j < len(parts) - 1:
                part = part + sep  # 保留分隔符
            if len(part) <= chunk_size:
                pieces.append(part)
            else:
                # 该片段仍太长，用下一级分隔符继续切。
                sub_seps = separators[idx + 1 :]
                pieces.extend(_recursive_split(part, chunk_size, sub_seps))
        # 重新合并过短的相邻片段（在 chunk_size 内）。
        return _merge_small(pieces, chunk_size)

    return _hard_split(text, chunk_size)


def _merge_small(pieces: list[str], chunk_size: int) -> list[str]:
    """把过短的相邻片段合并，直到接近 chunk_size。"""
    merged: list[str] = []
    for p in pieces:
        if not p:
            continue
        if merged and len(merged[-1]) + len(p) <= chunk_size:
            merged[-1] += p
        else:
            merged.append(p)
    return merged


def _hard_split(text: str, chunk_size: int) -> list[str]:
    """字符级硬切。"""
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


def _merge_with_overlap(pieces: list[str], chunk_size: int, overlap: int) -> list[str]:
    """把原子片段合并成带 overlap 的块。

    每块累积 pieces 直到接近 chunk_size；下一块从上一块尾部回退 overlap 字符开始。
    """
    if not pieces:
        return []

    # 先把所有 pieces 拼成一个带位置索引的大串，方便 overlap 切。
    # 但为保留分隔符语义，直接按 piece 累积。
    chunks: list[str] = []
    current = ""
    for p in pieces:
        candidate = current + p
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            # current 已满，提交。
            if current:
                chunks.append(current)
            # 下一块从 current 尾部 overlap 字符 + 新 piece 开始。
            tail = current[-overlap:] if overlap > 0 and current else ""
            current = tail + p
            # 若单个 piece 本身就超 chunk_size（不应发生，递归已保证），硬切。
            while len(current) > chunk_size:
                chunks.append(current[:chunk_size])
                tail2 = current[-overlap:] if overlap > 0 else ""
                current = tail2 + current[chunk_size:]
    if current:
        chunks.append(current)
    return chunks


def _assign_chunk_ids(chunks: list[Chunk], document_id: str) -> None:
    """为分块列表生成确定性 chunk_id 并设置 prev/next 链接。

    chunk_id 格式：{document_id}_chunk_{index}。
    每个 chunk 的 prev_chunk_id 和 next_chunk_id 指向相邻 chunk（首尾为空串）。
    """
    if not document_id:
        return
    for i, c in enumerate(chunks):
        c.chunk_id = f"{document_id}_chunk_{i}"
    for i, c in enumerate(chunks):
        if i > 0:
            c.prev_chunk_id = chunks[i - 1].chunk_id
        if i < len(chunks) - 1:
            c.next_chunk_id = chunks[i + 1].chunk_id


def expand_context(
    chunk: Chunk,
    chunks_by_id: dict[str, Chunk],
    window: int = 1,
) -> str:
    """上下文扩展：将 chunk 的文本与其 prev/next 相邻 chunk 拼接。

    Args:
        chunk: 中心 chunk。
        chunks_by_id: chunk_id → Chunk 的查找字典。
        window: 每侧扩展的 chunk 数（默认 1，即前后各 1 个，共 3 个 chunk）。

    Returns:
        拼接后的文本，各 chunk 间用换行分隔。若邻接 chunk 缺失则跳过。
    """
    parts: list[str] = []

    # 向前遍历 prev 链
    cur = chunk
    for _ in range(window):
        if cur.prev_chunk_id and cur.prev_chunk_id in chunks_by_id:
            cur = chunks_by_id[cur.prev_chunk_id]
            parts.insert(0, cur.text)
        else:
            break

    # 中心 chunk
    parts.append(chunk.text)

    # 向后遍历 next 链
    cur = chunk
    for _ in range(window):
        if cur.next_chunk_id and cur.next_chunk_id in chunks_by_id:
            cur = chunks_by_id[cur.next_chunk_id]
            parts.append(cur.text)
        else:
            break

    return "\n".join(parts)
