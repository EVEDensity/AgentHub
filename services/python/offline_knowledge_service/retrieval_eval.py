"""检索评测指标模块。

提供标准的检索质量评估指标：NDCG、MRR、Recall@K、Precision@K、Average Precision。
"""

from __future__ import annotations

import math
from typing import Any


def ndcg_at_k(relevance_scores: list[float], k: int) -> float:
    """Normalized Discounted Cumulative Gain at K。

    Args:
        relevance_scores: 按检索顺序排列的相关性分数列表（如 [3, 2, 0, 1]）。
        k: 截断位置。

    Returns:
        NDCG@K 值 [0, 1]。若 ideal DCG 为 0 则返回 0.0。
    """
    if k <= 0:
        return 0.0
    scores = relevance_scores[:k]
    if not scores:
        return 0.0

    dcg = _dcg(scores)
    ideal_scores = sorted(relevance_scores, reverse=True)[:k]
    idcg = _dcg(ideal_scores)
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def _dcg(scores: list[float]) -> float:
    """Discounted Cumulative Gain。"""
    dcg = 0.0
    for i, s in enumerate(scores):
        rank = i + 1
        discount = 1.0 / math.log2(rank + 1)
        dcg += s * discount
    return dcg


def mrr(relevance_scores: list[float]) -> float:
    """Mean Reciprocal Rank：找到第一个相关结果的位置的倒数。

    Args:
        relevance_scores: 按检索顺序排列的相关性分数列表。

    Returns:
        MRR 值 [0, 1]：1 / rank_of_first_relevant。
        若没有相关结果则返回 0.0。
    """
    for i, s in enumerate(relevance_scores):
        if s > 0:
            return 1.0 / (i + 1)
    return 0.0


def recall_at_k(
    relevant_ids: set[str] | list[str],
    retrieved_ids: list[str],
    k: int,
) -> float:
    """Recall@K：前 K 个检索结果中命中的相关文档比例。

    Args:
        relevant_ids: 黄金标准的相关文档 ID 集合。
        retrieved_ids: 检索结果 ID 列表（按排名顺序）。
        k: 截断位置。

    Returns:
        Recall@K [0, 1]。若 relevant_ids 为空，返回 1.0（定义）。
    """
    if k <= 0:
        return 0.0
    rel = set(relevant_ids)
    if not rel:
        return 1.0
    retrieved_set = set(retrieved_ids[:k])
    return len(rel & retrieved_set) / len(rel)


def precision_at_k(
    relevant_ids: set[str] | list[str],
    retrieved_ids: list[str],
    k: int,
) -> float:
    """Precision@K：前 K 个检索结果中相关文档的占比。

    Args:
        relevant_ids: 黄金标准的相关文档 ID 集合。
        retrieved_ids: 检索结果 ID 列表（按排名顺序）。
        k: 截断位置。

    Returns:
        Precision@K [0, 1]。
    """
    if k <= 0:
        return 0.0
    rel = set(relevant_ids)
    if not rel:
        return 0.0
    retrieved_set = set(retrieved_ids[:k])
    return len(rel & retrieved_set) / min(k, len(retrieved_ids[:k]))


def average_precision(
    relevant_ids: set[str] | list[str],
    retrieved_ids: list[str],
) -> float:
    """Average Precision（MAP-like 单查询版本）。

    计算每个相关结果位置的 precision，取平均。

    Args:
        relevant_ids: 黄金标准的相关文档 ID 集合。
        retrieved_ids: 检索结果 ID 列表（按排名顺序）。

    Returns:
        AP [0, 1]。
    """
    rel = set(relevant_ids)
    if not rel:
        return 0.0
    hit_count = 0
    ap_sum = 0.0
    for i, rid in enumerate(retrieved_ids):
        if rid in rel:
            hit_count += 1
            ap_sum += hit_count / (i + 1)
    if hit_count == 0:
        return 0.0
    return ap_sum / len(rel)


def evaluate_retrieval(
    query: str,
    results: list[dict[str, Any]],
    golden_chunk_ids: list[str],
) -> dict[str, Any]:
    """对单次检索执行全套指标评测。

    Args:
        query: 查询文本（仅用于返回结构中标识）。
        results: 检索结果列表，每个元素至少包含 `id` 字段。
        golden_chunk_ids: 黄金标准的相关 chunk ID 列表。

    Returns:
        dict，包含 query / golden_count / retrieved_count / ndcg_at_5 / ndcg_at_10 /
        mrr / recall_at_5 / recall_at_10 / precision_at_5 / precision_at_10 / ap。
    """
    golden_set = set(golden_chunk_ids)
    retrieved_ids = [str(r.get("id", "")) for r in results]

    # 相关性分数：命中 golden 则 1.0，否则 0.0（二元相关）。
    relevance_scores: list[float] = [
        1.0 if rid in golden_set else 0.0 for rid in retrieved_ids
    ]

    return {
        "query": query,
        "golden_count": len(golden_chunk_ids),
        "retrieved_count": len(results),
        "ndcg_at_5": round(ndcg_at_k(relevance_scores, 5), 6),
        "ndcg_at_10": round(ndcg_at_k(relevance_scores, 10), 6),
        "mrr": round(mrr(relevance_scores), 6),
        "recall_at_5": round(recall_at_k(golden_chunk_ids, retrieved_ids, 5), 6),
        "recall_at_10": round(recall_at_k(golden_chunk_ids, retrieved_ids, 10), 6),
        "precision_at_5": round(precision_at_k(golden_chunk_ids, retrieved_ids, 5), 6),
        "precision_at_10": round(precision_at_k(golden_chunk_ids, retrieved_ids, 10), 6),
        "ap": round(average_precision(golden_chunk_ids, retrieved_ids), 6),
    }
