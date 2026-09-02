"""RAG 检索质量评估 CLI。

运行方式：
    python -m evaluation.run_eval

对每条查询执行两种策略并对比指标：
- baseline: 纯向量检索 top-5（无 rerank）
- rerank:   过度召回 20 → BGE rerank → top-5
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import config
from tools.rag import retrieve_docs
from tools.reranker import rerank_documents
from evaluation.dataset import EVAL_DATASET
from evaluation.metrics import evaluate_run, is_relevant


def _sources(docs):
    return [os.path.basename(d.metadata.get("source", "unknown")) for d in docs]


def run_strategy(strategy: str):
    """对全部查询运行给定策略，返回 (pred_sources_per_query, expected_per_query)。"""
    pred_all, exp_all = [], []
    for item in EVAL_DATASET:
        query = item["query"]
        if strategy == "baseline":
            docs = retrieve_docs(query, k=5)
        else:  # rerank
            docs = retrieve_docs(query, k=config.RAG_RETRIEVAL_K)
            docs = rerank_documents(query, docs, config.RAG_TOP_K)
        pred_all.append(_sources(docs))
        exp_all.append(item["expected"])
    return pred_all, exp_all


def _print_metrics(label, metrics):
    print(f"  {label:<24} hit_rate@5={metrics['hit_rate@k']:.3f}  recall@5={metrics['recall@k']:.3f}  "
          f"MRR={metrics['mrr']:.3f}  nDCG@5={metrics['ndcg@k']:.3f}")


def main():
    print("=" * 78)
    print("  RAG 检索质量评估")
    print(f"  评测样本数: {len(EVAL_DATASET)}  |  召回 {config.RAG_RETRIEVAL_K} / top {config.RAG_TOP_K}"
          f"  |  Rerank: {'启用' if config.RERANK_ENABLED else '禁用'}")
    print("=" * 78)

    baseline_pred, exp_all = run_strategy("baseline")
    rerank_pred, _ = run_strategy("rerank")

    print("\n【聚合指标对比】")
    _print_metrics("baseline (向量 top-5)", evaluate_run(baseline_pred, exp_all, k=5))
    _print_metrics("rerank (召回20→精排5)", evaluate_run(rerank_pred, exp_all, k=5))

    print("\n【rerank 逐条明细】")
    for i, (item, pred) in enumerate(zip(EVAL_DATASET, rerank_pred), 1):
        hit = any(is_relevant(s, item["expected"]) for s in pred)
        mark = "HIT" if hit else "MISS"
        top3 = " | ".join(pred[:3])
        print(f"  {i:>2}. [{mark}] {item['query']}")
        print(f"       -> {top3}")

    print("\n" + "=" * 78)
    print("  评估完成")
    print("=" * 78)


if __name__ == "__main__":
    main()
