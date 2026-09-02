"""
FootballAI Career Agent - RAG 知识库检索工具

使用 ChromaDB + 本地 Embedding 构建足球知识库的向量检索。
知识来源：knowledge/ 目录下的 PDF 和文本文件。
"""

import os
from typing import List, Optional, Dict
from langchain_core.tools import tool
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import config
from tools.reranker import rerank_documents


# 全局向量存储实例（懒加载）
_vectorstore = None

# 全局本地 embedding 实例（懒加载）
_embedding_model = None


def _get_embedding_model():
    """获取 Embedding 模型（本地懒加载单例）。"""
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = _LocalEmbeddings()
    return _embedding_model


def _resolve_embedding_model_id() -> str:
    """优先使用本地已下载的 embedding 模型目录，否则使用 HF model id。"""
    local_dir = config.EMBEDDING_MODEL_DIR
    if local_dir and os.path.isdir(local_dir):
        return os.path.abspath(local_dir)
    return config.EMBEDDING_MODEL


class _LocalEmbeddings(Embeddings):
    """本地 BGE embedding（bge-m3，多语言）的懒加载包装。

    原实现使用 OpenAI text-embedding-3-small，但 DeepSeek 不提供 embedding 接口（404），
    故改为本地模型，匹配中英混合语料且无 API 依赖。
    """

    def __init__(self):
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from FlagEmbedding import FlagModel
            model_id = _resolve_embedding_model_id()
            print(f"[Embedding] 正在加载本地 Embedding 模型 {model_id} ...")
            self._model = FlagModel(model_id, use_fp16=False)
            print("[Embedding] 模型加载完成")
        return self._model

    def embed_documents(self, texts):
        vectors = self._ensure_model().encode(list(texts), batch_size=32, max_length=512)
        return [v.tolist() for v in vectors]

    def embed_query(self, text):
        vectors = self._ensure_model().encode([text], max_length=512)
        return vectors[0].tolist()


def _load_documents_from_directory(directory: str) -> List[Document]:
    """递归加载目录中的所有 PDF 和文本文件。"""
    documents = []

    for root, _, files in os.walk(directory):
        for filename in files:
            filepath = os.path.join(root, filename)
            ext = os.path.splitext(filename)[1].lower()

            try:
                if ext == ".pdf":
                    docs = _load_pdf(filepath)
                elif ext in (".txt", ".md"):
                    docs = _load_text(filepath)
                else:
                    continue

                # 元数据溯源：根据所在子目录添加来源标签（保证引用准确性）
                rel_dir = os.path.relpath(root, directory)
                category = rel_dir.replace("\\", "/").split("/")[0] if rel_dir != "." else "general"
                for doc in docs:
                    doc.metadata["category"] = category
                    doc.metadata["source"] = filepath

                documents.extend(docs)
            except Exception as e:
                print(f"  [WARN] 无法加载 {filepath}: {e}")

    return documents


def _load_pdf(filepath: str) -> List[Document]:
    """加载 PDF 文件。"""
    from langchain_community.document_loaders import PyPDFLoader
    loader = PyPDFLoader(filepath)
    return loader.load()


def _load_text(filepath: str) -> List[Document]:
    """加载文本文件。"""
    from langchain_community.document_loaders import TextLoader
    try:
        loader = TextLoader(filepath, encoding="utf-8")
        return loader.load()
    except UnicodeDecodeError:
        loader = TextLoader(filepath, encoding="gbk")
        return loader.load()


def _build_txt_splitter() -> RecursiveCharacterTextSplitter:
    """中文/平文本分块器：优先按段落与中文标点切分。"""
    return RecursiveCharacterTextSplitter(
        chunk_size=config.RAG_CHUNK_SIZE,
        chunk_overlap=config.RAG_CHUNK_OVERLAP,
        separators=["\n\n", "\n", "。", "；", "！", "？", "：", ". ", " ", ""],
    )


def _build_pdf_splitter() -> RecursiveCharacterTextSplitter:
    """PDF（多为英文论文/手册）分块器：优先按段落与英文句点切分。"""
    return RecursiveCharacterTextSplitter(
        chunk_size=config.RAG_CHUNK_SIZE,
        chunk_overlap=config.RAG_CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )


def _split_documents_by_type(docs: List[Document]) -> List[Document]:
    """按来源文件扩展名分派不同分块器，避免中文/英文语料混用同一套分隔符。"""
    txt_docs, pdf_docs, other = [], [], []
    for d in docs:
        src = str(d.metadata.get("source", "")).lower()
        if src.endswith(".txt"):
            txt_docs.append(d)
        elif src.endswith(".pdf"):
            pdf_docs.append(d)
        else:
            other.append(d)

    chunks: List[Document] = []
    if txt_docs:
        chunks.extend(_build_txt_splitter().split_documents(txt_docs))
    if pdf_docs:
        chunks.extend(_build_pdf_splitter().split_documents(pdf_docs))
    if other:
        chunks.extend(_build_txt_splitter().split_documents(other))
    return chunks


def _init_vectorstore(force_reload: bool = False):
    """初始化或获取 ChromaDB 向量存储（懒加载 + 缓存）。"""
    global _vectorstore

    if _vectorstore is not None and not force_reload:
        return _vectorstore

    from langchain_chroma import Chroma

    # 因为 ChromaDB 持久化目录，以及前面的全局单例模式，所以不用每次查询都重新加载资料和调用 Embedding 的 API
    persist_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "chroma_db")
    persist_dir = os.path.abspath(persist_dir)

    knowledge_dir = config.KNOWLEDGE_DIR
    embed_model = _get_embedding_model()

    if force_reload or not os.path.exists(persist_dir):
        print(f"[RAG] 正在从 {knowledge_dir} 加载文档并构建向量索引...")
        docs = _load_documents_from_directory(knowledge_dir)
        print(f"[RAG] 已加载 {len(docs)} 个文档片段")

        if not docs:
            print("[RAG] 警告：未找到任何文档！请将 PDF/TXT 文件放入 knowledge/ 目录。")
            # 创建空向量存储
            _vectorstore = Chroma(
                embedding_function=embed_model,
                persist_directory=persist_dir,
            )
        else:
            chunks = _split_documents_by_type(docs)
            print(f"[RAG] 已分块为 {len(chunks)} 个文本块")

            _vectorstore = Chroma.from_documents(
                documents=chunks,
                embedding=embed_model,
                persist_directory=persist_dir,
            )
        print(f"[RAG] 向量索引构建完成，存储于 {persist_dir}")
    else:
        print(f"[RAG] 从 {persist_dir} 加载已有向量索引...")
        _vectorstore = Chroma(
            embedding_function=embed_model,
            persist_directory=persist_dir,
        )

    return _vectorstore


def retrieve_docs(query: str, k: int = 5) -> List[Document]:
    """检索并返回结构化 Document 列表（保留 source/category 元数据），供评估与 Rerank 复用。"""
    vs = _init_vectorstore()
    retriever = vs.as_retriever(search_kwargs={"k": k})
    return retriever.invoke(query)


# 最近一次 RAG 检索的来源引用（供 Agent 输出 references 与评估追踪）
_last_citations: List[Dict[str, str]] = []


def docs_to_citations(docs: List[Document]) -> List[Dict[str, str]]:
    """将 Document 列表转为结构化来源引用。"""
    return [
        {
            "source": os.path.basename(d.metadata.get("source", "unknown")),
            "category": d.metadata.get("category", "general"),
        }
        for d in docs
    ]


def _set_last_citations(docs: List[Document]) -> None:
    global _last_citations
    _last_citations = docs_to_citations(docs)


def get_last_citations() -> List[Dict[str, str]]:
    """返回最近一次 RAG 检索的来源引用（供调用方写入 state.citations）。"""
    return list(_last_citations)


@tool
def FootballKnowledgeRAG(query: str) -> str:
    """足球专业知识库检索工具（RAG）。从官方足球训练手册、营养指南、伤病预防文献中检索相关信息。

    适用场景：
    - 查询特定足球训练动作（如"边锋射门训练方法"）
    - 查询运动营养建议（如"赛前碳水加载策略"）
    - 查询伤病预防知识（如"腘绳肌拉伤预防"）
    - 查询职业发展路径（如"青年球员发展LTAD模型"）

    Args:
        query: 检索查询（中文或英文），例如 "UEFA shooting drills for wingers"

    Returns:
        最相关的 5 条知识片段及其来源。
    """
    try:
        docs = retrieve_docs(query, k=config.RAG_RETRIEVAL_K)
        docs = rerank_documents(query, docs, config.RAG_TOP_K)
        _set_last_citations(docs)

        if not docs:
            return f"未找到与 '{query}' 相关的足球知识。请尝试更换搜索词。"

        results = []
        for i, doc in enumerate(docs, 1):
            source = os.path.basename(doc.metadata.get("source", "unknown"))
            category = doc.metadata.get("category", "general")
            content = doc.page_content[:300].replace("\n", " ").strip()
            results.append(f"{i}. [{category}] {content}...")
            results.append(f"   来源: {source}\n")

        return "\n".join(results)

    except Exception as e:
        return f"RAG 检索出错: {str(e)}"


def reload_knowledge_base() -> str:
    """强制重新构建知识库索引。"""
    global _vectorstore
    _vectorstore = None
    try:
        _init_vectorstore(force_reload=True)
        return "知识库索引已重新构建完成。"
    except Exception as e:
        return f"知识库重建失败: {str(e)}"


RAG_TOOLS = [FootballKnowledgeRAG]
