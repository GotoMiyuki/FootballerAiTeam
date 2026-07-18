"""
FootballAI Career Agent - RAG 知识库检索工具

使用 ChromaDB + 本地 Embedding 构建足球知识库的向量检索。
知识来源：knowledge/ 目录下的 PDF 和文本文件。
"""

import os
from typing import List, Optional
from langchain_core.tools import tool
from langchain_core.documents import Document

from config import config


# 全局向量存储实例（懒加载）
_vectorstore = None


def _get_embedding_model():
    """获取 Embedding 模型。"""
    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings(
        model="text-embedding-3-small",
        api_key=config.OPENAI_API_KEY,
        base_url=config.OPENAI_BASE_URL,
    )


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

                # 根据所在子目录添加来源标签
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


def _init_vectorstore(force_reload: bool = False):
    """初始化或获取 ChromaDB 向量存储（懒加载 + 缓存）。"""
    global _vectorstore

    if _vectorstore is not None and not force_reload:
        return _vectorstore

    from langchain_chroma import Chroma

    # ChromaDB 持久化目录
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
            from langchain_text_splitters import RecursiveCharacterTextSplitter
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=500,
                chunk_overlap=100,
                separators=["\n\n", "\n", "。", ".", " ", ""],
            )
            chunks = splitter.split_documents(docs)
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
        vs = _init_vectorstore()
        retriever = vs.as_retriever(search_kwargs={"k": 5})
        docs = retriever.invoke(query)

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
