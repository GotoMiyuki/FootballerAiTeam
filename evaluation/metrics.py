"""RAG 检索评估指标。

输入约定：
- pred_sources: 按相关度排序的来源文件名（basename）列表
- expected: 预期相关来源的子串列表（命中任一即视为相关）

实现 hit_rate@k / recall@k / MRR / nDCG@k。
"""

import math
from typing import List, Dict


def is_relevant(source: str, expected: List[str]) -> bool:
    """判断某来源是否与任一预期子串匹配（大小写不敏感）。"""
    s = source.lower()
    return any(exp.lower() in s for exp in expected)


def hit_rate_at_k(pred_sources: List[str], expected: List[str], k: int) -> float:
    """Top-k 中是否存在相关文档（单条 0/1）。"""
    return 1.0 if any(is_relevant(s, expected) for s in pred_sources[:k]) else 0.0


def recall_at_k(pred_sources: List[str], expected: List[str], k: int) -> float:
    """Top-k 覆盖了多少预期来源（比例）。"""
    if not expected:
        return 0.0
    covered = {exp for exp in expected if any(exp.lower() in s.lower() for s in pred_sources[:k])}
    return len(covered) / len(expected)


def mrr(pred_sources: List[str], expected: List[str]) -> float:
    """Mean Reciprocal Rank：首个相关文档排名的倒数。"""
    for i, s in enumerate(pred_sources, 1):
        if is_relevant(s, expected):
            return 1.0 / i
    return 0.0


def ndcg_at_k(pred_sources: List[str], expected: List[str], k: int) -> float:
    """Normalized Discounted Cumulative Gain（二值相关）。"""
    rels = [1.0 if is_relevant(s, expected) else 0.0 for s in pred_sources[:k]]
    dcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(rels))
    ideal = sorted(rels, reverse=True)
    idcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(ideal))
    return dcg / idcg if idcg > 0 else 0.0


def evaluate_run(pred_sources_per_query: List[List[str]], expected_per_query: List[List[str]], k: int = 5) -> Dict[str, float]:
    """对一批查询的结果计算聚合指标。"""
    n = len(pred_sources_per_query)
    if n == 0:
        return {"hit_rate@k": 0.0, "recall@k": 0.0, "mrr": 0.0, "ndcg@k": 0.0}

    hit = sum(hit_rate_at_k(p, e, k) for p, e in zip(pred_sources_per_query, expected_per_query)) / n
    rec = sum(recall_at_k(p, e, k) for p, e in zip(pred_sources_per_query, expected_per_query)) / n
    m = sum(mrr(p, e) for p, e in zip(pred_sources_per_query, expected_per_query)) / n
    ndcg = sum(ndcg_at_k(p, e, k) for p, e in zip(pred_sources_per_query, expected_per_query)) / n

    return {"hit_rate@k": hit, "recall@k": rec, "mrr": m, "ndcg@k": ndcg}
