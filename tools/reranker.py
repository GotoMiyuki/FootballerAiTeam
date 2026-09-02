"""
FootballAI Career Agent - BGE Reranker 重排序模块

两阶段检索的第二阶段：先过度召回，再用 BGE 交叉编码器对 (query, doc) 精排。
使用 FlagEmbedding 的 FlagReranker（bge-reranker-v2-m3，中英双语）。
"""

import os
from typing import List
from langchain_core.documents import Document

from config import config

# 全局 reranker 实例（懒加载，避免每次查询都重载模型）
_reranker = None


def _resolve_model_id() -> str:
    """优先使用本地已下载的模型目录，否则使用 HF model id 联网下载。"""
    local_dir = config.RERANK_MODEL_DIR
    if local_dir and os.path.isdir(local_dir):
        return os.path.abspath(local_dir)
    return config.RERANK_MODEL


def _get_reranker():
    global _reranker
    if _reranker is None:
        if config.HF_ENDPOINT:
            os.environ.setdefault("HF_ENDPOINT", config.HF_ENDPOINT)
        from FlagEmbedding import FlagReranker
        model_id = _resolve_model_id()
        print(f"[Rerank] 正在加载 Reranker 模型 {model_id} ...")
        # CPU-only 环境下 fp16 不可用，使用 fp32
        _reranker = FlagReranker(model_id, use_fp16=False)
        print("[Rerank] 模型加载完成")
    return _reranker


def rerank_documents(query: str, docs: List[Document], top_k: int) -> List[Document]:
    """对检索到的文档按 (query, doc) 相关性重排序，返回 top_k。

    当 RERANK_ENABLED=false、文档数不足、或模型加载失败时，
    降级为按原始向量相似度顺序截断。
    """
    if not docs:
        return []

    if len(docs) <= top_k:
        return docs

    if not config.RERANK_ENABLED:
        return docs[:top_k]

    try:
        model = _get_reranker()
        pairs = [(query, doc.page_content) for doc in docs]
        scores = model.compute_score(pairs)
        if not isinstance(scores, (list, tuple)):
            scores = [scores]
        ranked = sorted(zip(scores, docs), key=lambda x: x[0], reverse=True)
        return [doc for _, doc in ranked[:top_k]]
    except Exception as e:
        print(f"[Rerank] 重排序失败，降级为原始顺序: {e}")
        return docs[:top_k]
