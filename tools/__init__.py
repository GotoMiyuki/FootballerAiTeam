"""
FootballAI Career Agent - 工具聚合模块

各 Agent 的工具分组在此统一定义，Agent 直接引用，避免各自内联造成职责不一致。
"""

from tools.calculator import CALCULATOR_TOOLS
from tools.database import (
    DATABASE_TOOLS,
    UpdatePlayerAttributeTool,
    ReadTrainingHistoryTool,
    ReadMatchHistoryTool,
    ReadCareerHistoryTool,
    ReadPlayerProfileTool,
)
from tools.search import SEARCH_TOOLS
from tools.rag import RAG_TOOLS

# 所有工具的集合
ALL_TOOLS = CALCULATOR_TOOLS + DATABASE_TOOLS + SEARCH_TOOLS + RAG_TOOLS

# 按 Agent 分组的工具（与实际 Agent 职责对齐）
MANAGER_TOOLS = []
NUTRITION_TOOLS = CALCULATOR_TOOLS
COACH_TOOLS = RAG_TOOLS + SEARCH_TOOLS + [UpdatePlayerAttributeTool]
ANALYST_TOOLS = [ReadTrainingHistoryTool, ReadMatchHistoryTool] + SEARCH_TOOLS
CAREER_TOOLS = SEARCH_TOOLS + [ReadCareerHistoryTool, ReadPlayerProfileTool]
