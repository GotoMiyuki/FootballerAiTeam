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
