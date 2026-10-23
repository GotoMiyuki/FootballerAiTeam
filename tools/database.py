"""
FootballAI Career Agent - 球员数据库工具

提供统一 Repository 快照的只读接口；旧写入符号仅保留拒绝调用的兼容入口。
支持 offense/defense/physical/goalkeeping 四维属性结构。
"""

import json
from typing import Dict, Any, List
from langchain_core.tools import tool

from player_data.repository import read_snapshot


def _write_json(filepath: str, data: Any) -> None:
    """已停用的旧直接写入入口。"""
    raise PermissionError("Uncontrolled fact writes are disabled; use a configured ObservationImporter")


# ============================================================
# 基础读取函数与拒绝写入的兼容符号
# ============================================================

def read_player_profile() -> Dict[str, Any]:
    """从当前任务固定的只读快照读取球员档案。"""
    return read_snapshot().profile


def update_player_profile(updates: Dict[str, Any]) -> Dict[str, Any]:
    """已停用；事实更新必须通过应用配置的 ObservationImporter。"""
    raise PermissionError("Uncontrolled fact writes are disabled; use a configured ObservationImporter")


def read_training_history() -> List[Dict[str, Any]]:
    """读取训练历史记录。"""
    return read_snapshot().training


def read_match_history() -> List[Dict[str, Any]]:
    """读取比赛历史记录。"""
    return read_snapshot().matches


def read_career_history() -> Dict[str, Any]:
    """读取职业发展历史记录。"""
    return read_snapshot().career


def append_training_record(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """已停用的训练历史直接写入入口。"""
    raise PermissionError("Uncontrolled fact writes are disabled; use a configured ObservationImporter")


def append_match_record(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """已停用的比赛历史直接写入入口。"""
    raise PermissionError("Uncontrolled fact writes are disabled; use a configured ObservationImporter")


# ============================================================
# LangChain Tool 封装
# ============================================================

@tool
def ReadPlayerProfileTool() -> str:
    """读取当前球员的完整档案数据，包括身体数据、能力值、伤病历史等。
    在需要了解球员基本信息时调用此工具。
    """
    profile = read_player_profile()
    if not profile:
        return "未找到球员档案数据。"
    return json.dumps(profile, ensure_ascii=False, indent=2)


@tool
def UpdatePlayerProfileTool(updates_json: str) -> str:
    """已停用的旧工具符号，调用一律拒绝；不注册给模型。"""
    raise PermissionError("Uncontrolled fact writes are disabled; use a configured ObservationImporter")


@tool
def UpdatePlayerAttributeTool(update_json: str) -> str:
    """已停用的旧工具符号，预测不得更新能力事实；不注册给模型。"""
    raise PermissionError("Uncontrolled fact writes are disabled; use a configured ObservationImporter")


@tool
def ReadTrainingHistoryTool() -> str:
    """读取球员过去 3 个月的训练历史记录，包括每周训练内容、强度和负荷。
    用于分析训练趋势和评估伤病风险。
    """
    history = read_training_history()
    if not history:
        return "暂无训练历史数据。"
    # 返回最近 12 周记录
    recent = history[-12:] if len(history) > 12 else history
    return json.dumps(recent, ensure_ascii=False, indent=2)


@tool
def ReadMatchHistoryTool() -> str:
    """读取球员的比赛历史记录，包括出场时间、进球、助攻、评分等。
    用于分析比赛表现趋势。
    """
    history = read_match_history()
    if not history:
        return "暂无比赛历史数据。"
    return json.dumps(history, ensure_ascii=False, indent=2)


@tool
def ReadCareerHistoryTool() -> str:
    """读取球员的职业发展历史，包括里程碑事件、市场价值变化等。
    用于职业规划分析。
    """
    history = read_career_history()
    if not history:
        return "暂无职业发展历史数据。"
    return json.dumps(history, ensure_ascii=False, indent=2)


# 工具列表，方便 Agent 注册
DATABASE_TOOLS = [
    ReadPlayerProfileTool,
    ReadTrainingHistoryTool,
    ReadMatchHistoryTool,
    ReadCareerHistoryTool,
]
