"""
FootballAI Career Agent - 球员数据库工具

提供长时记忆（Long Memory）的读写接口。
支持 offense/defense/physical/goalkeeping 四维属性结构。
"""

import json
import os
from datetime import datetime
from typing import Dict, Any, List, Optional
from langchain_core.tools import tool

from config import config
from utils.helpers import deep_merge_attributes


def _read_json(filepath: str) -> Dict[str, Any]:
    """读取 JSON 文件，不存在则返回空字典/空列表。"""
    if not os.path.exists(filepath):
        return {}
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(filepath: str, data: Any) -> None:
    """写入 JSON 文件。"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# 基础读写函数
# ============================================================

def read_player_profile() -> Dict[str, Any]:
    """从 memory/player.json 读取球员档案。"""
    return _read_json(config.PLAYER_FILE)


def update_player_profile(updates: Dict[str, Any]) -> Dict[str, Any]:
    """更新 memory/player.json 中的球员数据。

    Args:
        updates: 需要更新的字段，会与现有数据合并。
    """
    profile = read_player_profile()
    profile.update(updates)
    profile["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    _write_json(config.PLAYER_FILE, profile)
    return profile


def read_training_history() -> List[Dict[str, Any]]:
    """读取训练历史记录。"""
    data = _read_json(config.TRAINING_HISTORY_FILE)
    return data if isinstance(data, list) else []


def read_match_history() -> List[Dict[str, Any]]:
    """读取比赛历史记录。"""
    data = _read_json(config.MATCH_HISTORY_FILE)
    return data if isinstance(data, list) else []


def read_career_history() -> Dict[str, Any]:
    """读取职业发展历史记录。"""
    return _read_json(config.CAREER_HISTORY_FILE)


def append_training_record(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """向训练历史追加一条记录。"""
    history = read_training_history()
    history.append(record)
    _write_json(config.TRAINING_HISTORY_FILE, history)
    return history


def append_match_record(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """向比赛历史追加一条记录。"""
    history = read_match_history()
    history.append(record)
    _write_json(config.MATCH_HISTORY_FILE, history)
    return history


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
    """更新球员档案顶层数据（身高、体重、年龄等）。不适用于嵌套属性更新。
    如需更新能力值，请使用 UpdatePlayerAttributeTool。

    Args:
        updates_json: JSON 格式的更新数据字符串，例如 '{"weight": 57, "training_intensity": "Medium"}'
    """
    try:
        updates = json.loads(updates_json)
    except json.JSONDecodeError:
        return "错误：输入不是有效的 JSON 格式。"
    updated = update_player_profile(updates)
    return json.dumps(updated, ensure_ascii=False, indent=2)


@tool
def UpdatePlayerAttributeTool(update_json: str) -> str:
    """更新球员能力属性值，支持深层嵌套更新 offense/defense/physical/goalkeeping/other_features。

    使用方式：
    - 更新单项: '{"offense": {"shooting": 73}}'
    - 更新多项: '{"physical": {"speed": 83, "stamina": 77}}'
    - 更新特征: '{"other_features": {"form_consistency": 7}}'
    - 组合更新: '{"offense": {"passing": 75}, "physical": {"speed": 83}}'

    工具会自动将新值与现有值深度合并，不会覆盖未提及的属性。

    Args:
        update_json: JSON 格式的属性更新数据。
    """
    try:
        updates = json.loads(update_json)
    except json.JSONDecodeError:
        return "错误：输入不是有效的 JSON 格式。"

    profile = read_player_profile()
    if not profile:
        return "错误：未找到球员档案。"

    # 深度合并 attributes
    current_attrs = profile.get("attributes", {})
    if "attributes" in updates:
        profile["attributes"] = deep_merge_attributes(current_attrs, updates["attributes"])
        del updates["attributes"]

    # other_features 深度合并
    current_other = profile.get("other_features", {})
    if "other_features" in updates:
        profile["other_features"] = deep_merge_attributes(current_other, updates["other_features"])
        del updates["other_features"]

    # offense/defense/physical/goalkeeping 直接合并到 attributes 下
    for category in ("offense", "defense", "physical", "goalkeeping"):
        if category in updates:
            current_attrs = profile.get("attributes", {})
            profile["attributes"] = deep_merge_attributes(
                current_attrs, {category: updates[category]}
            )
            del updates[category]

    # 其余顶层字段更新
    if updates:
        profile.update(updates)

    profile["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    _write_json(config.PLAYER_FILE, profile)
    return json.dumps(profile, ensure_ascii=False, indent=2)


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
    UpdatePlayerProfileTool,
    UpdatePlayerAttributeTool,
    ReadTrainingHistoryTool,
    ReadMatchHistoryTool,
    ReadCareerHistoryTool,
]
