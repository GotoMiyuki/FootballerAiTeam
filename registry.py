"""
FootballAI Career Agent - Agent Registry

集中管理所有 Agent 的注册信息。新增 Agent 只需在此文件添加一个条目，
graph.py / manager.py 的路由逻辑会自动适配，无需手动修改。
"""

AGENT_REGISTRY = {
    "Nutrition": {
        "node_name": "nutrition",
        "display": "Nutrition",
        "description": (
            "运动营养师，根据身体属性（physical维度）和训练强度"
            "制定减脂/增肌/营养方案"
        ),
        "trigger_keywords": ["体重", "减脂", "增肌", "饮食", "营养", "体脂", "热量"],
    },
    "Coach": {
        "node_name": "coach",
        "display": "Coach",
        "description": (
            "技能教练，基于四维属性（进攻/防守/身体/守门）识别短板，"
            "调用RAG知识库制定针对性训练计划"
        ),
        "trigger_keywords": ["训练", "技术", "体能", "战术", "速度", "力量", "射门", "传球"],
    },
    "Analyst": {
        "node_name": "analyst",
        "display": "Analyst",
        "description": (
            "表现分析师，深度交叉分析属性间不平衡关系，"
            "结合训练/比赛历史评估伤病风险和发展趋势"
        ),
        "trigger_keywords": ["分析", "趋势", "伤病", "短板", "评估", "数据", "比赛表现"],
    },
    "Career": {
        "node_name": "career",
        "display": "Career",
        "description": (
            "职业经纪人（长期战略层），负责职业路径规划(career_planning)和"
            "转会分析(transfer_analysis)，包括战术适配性、联赛环境、市场估值。"
            "注意：不负责公关声明、媒体回应、转会传闻应对、商业代言（这些属于 Document Agent）"
        ),
        "trigger_keywords": ["职业", "转会", "身价", "发展路径", "市场价值", "经纪", "联赛"],
        "excludes": ["公关", "声明", "媒体", "代言", "传闻回应", "辟谣", "采访"],
    },
    "Document": {
        "node_name": "document",
        "display": "Document",
        "description": (
            "首席内容与商务官（场外价值变现层），负责综合报告(comprehensive_report)、"
            "公关声明(pr_statement)、商业代言评估(commercial_advisory)、媒体应答(media_response)。"
            "注意：不负责职业路径规划、转会决策分析（这些属于 Career Agent）"
        ),
        "trigger_keywords": ["报告", "文档", "新闻稿", "公关", "官宣", "声明", "代言", "媒体", "采访", "商业",
                            "传闻", "回应", "辟谣", "品牌", "赞助", "社媒"],
        "excludes": ["职业规划", "转会分析", "战术适配", "联赛选择"],
    },
}

# 子 Agent 的 display 名称列表，供路由按确定顺序迭代使用
SUB_AGENT_NAMES = list(AGENT_REGISTRY.keys())

# display -> node_name 映射
DISPLAY_TO_NODE = {v["display"]: v["node_name"] for v in AGENT_REGISTRY.values()}

# node_name -> display 反向映射
NODE_TO_DISPLAY = {v["node_name"]: v["display"] for v in AGENT_REGISTRY.values()}

# 始终放在执行队列末尾的 Agent（Document 负责汇总）
FINAL_AGENT = "Document"

# Manager 专用节点名称
MANAGER_NODES = ("manager", "manager_aggregate", "manager_confirm")


def get_sub_agent_node_names():
    """返回所有子 Agent 的 node_name 列表。"""
    return [info["node_name"] for info in AGENT_REGISTRY.values()]


def get_agent_descriptions():
    """生成供 LLM prompt 使用的 Agent 描述文本（含职责边界）。"""
    lines = []
    for display, info in AGENT_REGISTRY.items():
        desc = f"- **{display}**: {info['description']}"
        excludes = info.get("excludes", [])
        if excludes:
            desc += f" [明确排除: {', '.join(excludes)}]"
        lines.append(desc)
    return "\n".join(lines)
