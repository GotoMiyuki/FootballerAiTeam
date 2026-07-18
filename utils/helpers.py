"""
FootballAI Career Agent - 工具函数
"""

from typing import List, Tuple, Dict, Any
from langchain_openai import ChatOpenAI
from config import config


def create_llm(temperature: float = 0.3) -> ChatOpenAI:
    """创建大模型实例。"""
    return ChatOpenAI(
        model=config.MODEL_NAME,
        api_key=config.OPENAI_API_KEY,
        base_url=config.OPENAI_BASE_URL,
        temperature=temperature,
    )


def check_config() -> bool:
    """检查配置是否完整。"""
    issues = []

    if not config.OPENAI_API_KEY or "your-api-key" in config.OPENAI_API_KEY:
        issues.append("OPENAI_API_KEY 未配置，请在 .env 中设置")

    if not config.TAVILY_API_KEY or "your-tavily" in config.TAVILY_API_KEY:
        print("[WARN] TAVILY_API_KEY 未配置，联网搜索将使用本地回退")

    if issues:
        print("[ERROR] 配置检查失败：")
        for issue in issues:
            print(f"  - {issue}")
        return False

    return True


# ============================================================
# 球员属性工具函数（适配 offense/defense/physical/goalkeeping 四维结构）
# ============================================================

AttrEntry = Tuple[str, str, int]  # (category, attr_name, value)

# 属性中文名映射
ATTR_DISPLAY_NAMES: Dict[str, str] = {
    "attacking_awareness": "进攻意识",
    "ball_control": "控球",
    "passing": "传球",
    "shooting": "射门",
    "defensive_awareness": "防守意识",
    "speed": "速度",
    "strength": "力量",
    "stamina": "耐力",
    "jumping": "弹跳",
    "gk_awareness": "守门意识",
    "gk_reflexes": "守门反应",
}

CATEGORY_DISPLAY_NAMES: Dict[str, str] = {
    "offense": "进攻",
    "defense": "防守",
    "physical": "身体",
    "goalkeeping": "守门",
}


def flatten_attributes(attributes: Dict[str, Any]) -> List[AttrEntry]:
    """将嵌套属性展平为 (category, attr_name, value) 三元组列表。"""
    result = []
    for category, attrs in attributes.items():
        if isinstance(attrs, dict):
            for name, value in attrs.items():
                result.append((category, name, int(value)))
    return result


def get_weakest_attributes(attributes: Dict[str, Any], n: int = 3) -> List[AttrEntry]:
    """获取能力值最低的 n 项属性（排除守门属性，除非球员是门将）。"""
    flat = flatten_attributes(attributes)
    # 默认排除守门属性
    flat = [(c, a, v) for c, a, v in flat if c != "goalkeeping"]
    flat.sort(key=lambda x: x[2])
    return flat[:n]


def get_strongest_attributes(attributes: Dict[str, Any], n: int = 3) -> List[AttrEntry]:
    """获取能力值最高的 n 项属性（排除守门属性）。"""
    flat = flatten_attributes(attributes)
    flat = [(c, a, v) for c, a, v in flat if c != "goalkeeping"]
    flat.sort(key=lambda x: x[2], reverse=True)
    return flat[:n]


def attr_display_name(attr_name: str) -> str:
    """获取属性的中文显示名。"""
    return ATTR_DISPLAY_NAMES.get(attr_name, attr_name)


def category_display_name(category: str) -> str:
    """获取分类的中文显示名。"""
    return CATEGORY_DISPLAY_NAMES.get(category, category)


def format_attr_entry(entry: AttrEntry) -> str:
    """格式化单条属性为可读字符串。"""
    cat, name, val = entry
    return f"[{category_display_name(cat)}] {attr_display_name(name)} = {val}"


def describe_player_attributes(attributes: Dict[str, Any], other_features: Dict[str, Any] = None) -> str:
    """生成球员能力值的人类可读描述。

    Args:
        attributes: 四维属性字典 (offense/defense/physical/goalkeeping)
        other_features: 球员特征字典 (weak_foot_frequency/weak_foot_accuracy/form_consistency/injury_resistance)
    """
    lines = []
    for category in ("offense", "defense", "physical", "goalkeeping"):
        attrs = attributes.get(category, {})
        if not attrs:
            continue
        cat_name = category_display_name(category)
        items = ", ".join(
            f"{attr_display_name(k)}={v}" for k, v in attrs.items()
        )
        lines.append(f"  {cat_name}: {items}")

    other = other_features or {}
    if other:
        lines.append(f"  特征: 非惯用脚频率={other.get('weak_foot_frequency','?')}/5, "
                     f"非惯用脚精度={other.get('weak_foot_accuracy','?')}/5, "
                     f"状态稳定性={other.get('form_consistency','?')}/8, "
                     f"抗伤能力={other.get('injury_resistance','?')}/5")
    return "\n".join(lines)


def deep_merge_attributes(current: Dict, updates: Dict) -> Dict:
    """深度合并球员属性，支持嵌套的 offense/defense/physical/goalkeeping/other_features。"""
    result = dict(current)
    for key, value in updates.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge_attributes(result[key], value)
        else:
            result[key] = value
    return result
