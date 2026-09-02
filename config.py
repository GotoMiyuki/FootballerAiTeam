"""
FootballAI Career Agent - 配置文件
从 .env 读取 API Key 和基础配置，初始化大模型。
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """全局配置类"""

    # --- 大模型配置 ---
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    MODEL_NAME: str = os.getenv("MODEL_NAME", "deepseek-chat")

    # --- Tavily 搜索配置 ---
    TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")

    # --- RAG 检索与 Rerank 配置 ---
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    EMBEDDING_MODEL_DIR: str = os.getenv("EMBEDDING_MODEL_DIR", "models/bge-m3")
    RERANK_ENABLED: bool = os.getenv("RERANK_ENABLED", "true").lower() == "true"
    RERANK_MODEL: str = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
    RERANK_MODEL_DIR: str = os.getenv("RERANK_MODEL_DIR", "models/bge-reranker-v2-m3")
    RAG_RETRIEVAL_K: int = int(os.getenv("RAG_RETRIEVAL_K", "20"))
    RAG_TOP_K: int = int(os.getenv("RAG_TOP_K", "5"))
    RAG_CHUNK_SIZE: int = int(os.getenv("RAG_CHUNK_SIZE", "900"))
    RAG_CHUNK_OVERLAP: int = int(os.getenv("RAG_CHUNK_OVERLAP", "200"))
    # HuggingFace 模型下载镜像（国内访问 huggingface.co 不稳定时可设为 https://hf-mirror.com）
    HF_ENDPOINT: str = os.getenv("HF_ENDPOINT", "")

    # --- 应用配置 ---
    MAX_CONVERSATION_TURNS: int = int(os.getenv("MAX_CONVERSATION_TURNS", "10"))
    SHORT_MEMORY_SIZE: int = int(os.getenv("SHORT_MEMORY_SIZE", "5"))
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    # --- 路径配置 ---
    MEMORY_DIR: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory")
    KNOWLEDGE_DIR: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge")
    PLAYER_FILE: str = os.path.join(MEMORY_DIR, "player.json")
    TRAINING_HISTORY_FILE: str = os.path.join(MEMORY_DIR, "training_history.json")
    MATCH_HISTORY_FILE: str = os.path.join(MEMORY_DIR, "match_history.json")
    CAREER_HISTORY_FILE: str = os.path.join(MEMORY_DIR, "career_history.json")


config = Config()
