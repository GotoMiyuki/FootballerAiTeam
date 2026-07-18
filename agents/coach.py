"""
FootballAI Career Agent - Skill Coach Agent（竞技能力发展）

P1 ReAct 升级：
- LLM 自主决定调用 RAG / Search / Database 工具
- 保留代码层预处理（短板识别、属性不平衡检测）
- Thought → Action → Observation → Finish 循环
"""

import json
from typing import Dict, Any
from langchain_core.language_models import BaseChatModel

from agents.base import BaseAgent
from prompts.agent_prompts import (
    COACH_DOMAIN_IDENTITY,
    COACH_GUIDE,
    build_mission_context,
)
from tools.rag import FootballKnowledgeRAG
from tools.database import UpdatePlayerAttributeTool
from tools.search import SearchTool
from utils.helpers import (
    get_weakest_attributes,
    get_strongest_attributes,
    format_attr_entry,
    describe_player_attributes,
    attr_display_name,
    category_display_name,
)


class CoachAgent(BaseAgent):
    """技能教练 Agent — ReAct-powered，聚焦竞技能力发展"""

    def __init__(self, llm: BaseChatModel):
        super().__init__(llm=llm, tools=[
            FootballKnowledgeRAG,
            SearchTool,
            UpdatePlayerAttributeTool,
        ])

    @property
    def name(self) -> str:
        return "coach"

    @property
    def role(self) -> str:
        return "技能教练（Skill Coach）"

    @property
    def system_prompt(self) -> str:
        return f"{COACH_DOMAIN_IDENTITY}\n\n{COACH_GUIDE}"

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        mission = state.get("mission", {})
        domain_contrib = mission.get("domain_contributions", {}).get("Coach", {})

        if not domain_contrib.get("needed", False):
            return {"iteration": state.get("iteration", 0) + 1}

        player = state.get("player_profile", {})
        focus = domain_contrib.get("focus", mission.get("primary_goal", "制定训练计划"))
        attributes = player.get("attributes", {})

        # ---- 代码层预处理（保留，非工具调用） ----
        weakest = get_weakest_attributes(attributes, n=4)
        strongest = get_strongest_attributes(attributes, n=3)
        imbalance_warnings = self._detect_imbalances(attributes)

        # 构建短板/长项的检索提示
        weak_hints = []
        for cat, name, val in weakest:
            weak_hints.append(
                f"[{category_display_name(cat)}] {attr_display_name(name)}={val}"
            )
        strong_hints = []
        for _, name, val in strongest:
            strong_hints.append(attr_display_name(name))

        # ---- Layer 2: Mission Context ----
        mission_context = build_mission_context(mission, "Coach")

        # ---- ReAct Task Prompt ----
        task_prompt = f"""{mission_context}

## 球员档案
- 姓名: {player.get('name', '球员')}
- 位置: {player.get('position', 'LW')}
- 年龄: {player.get('age', 20)}
- 综合评分: {player.get('overall', 72)}
- 训练强度: {player.get('training_intensity', 'High')}

## 能力值（offense/defense/physical/goalkeeping 四维）
{describe_player_attributes(attributes, player.get('other_features', {}))}

## 短板分析（需重点提升）
{chr(10).join(f'- {h}' for h in weak_hints)}

## 长项（维持水平即可）
{chr(10).join(f'- {h}' for h in strong_hints)}

## 属性不平衡预警
{chr(10).join(f'- {w}' for w in imbalance_warnings) if imbalance_warnings else '无明显不平衡'}

## 可用工具
- **FootballKnowledgeRAG**: 检索足球专业知识库（训练方法、伤病预防、战术理论）。
  搜索示例: "边锋速度训练方法 soccer speed drills"、"FIFA 11+ 热身方案"
- **SearchTool**: 联网搜索最新足球训练资讯。
- **UpdatePlayerAttributeTool**: 训练后更新球员属性值（输入 JSON）。

## 任务
{focus}

## 执行流程
1. 使用 FootballKnowledgeRAG 检索短板对应的专项训练方法（至少检索 2-3 个方向）
2. 使用 SearchTool 补充搜索最新训练理念
3. 综合分析后输出 JSON 训练计划

## 输出格式
JSON，字段：focus_areas, weekly_schedule, drill_details（含 name/sets/frequency/description）,
imbalance_notes, attribute_update_suggestions（训练4周后的预期属性变化，如 {{"physical": {{"speed": 83}}}}）, notes。
只输出 JSON，不要其他文本。"""

        # ---- ReAct 循环 ----
        raw_output, tool_log = self._run_react_loop(
            task_prompt=task_prompt,
            player_profile=player,
        )

        # ---- 解析 LLM 输出 ----
        content = raw_output.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        try:
            result = json.loads(content)
        except (json.JSONDecodeError, Exception):
            result = self._generate_default_plan(weakest, strongest, imbalance_warnings)

        # ---- 自动应用属性更新建议 ----
        attr_updates = result.get("attribute_update_suggestions", {})
        if attr_updates and isinstance(attr_updates, dict):
            try:
                UpdatePlayerAttributeTool.invoke(json.dumps(attr_updates, ensure_ascii=False))
            except Exception:
                pass

        output = json.dumps(result, ensure_ascii=False, indent=2)

        return {
            "domain_outputs": {"Coach": output},
            "iteration": state.get("iteration", 0) + 1,
            "tool_call_log": tool_log,
        }

    def _detect_imbalances(self, attributes: Dict[str, Any]) -> list:
        """检测属性间的不平衡关系。"""
        warnings = []
        offense = attributes.get("offense", {})
        defense = attributes.get("defense", {})
        physical = attributes.get("physical", {})

        spd = physical.get("speed", 0)
        sta = physical.get("stamina", 0)
        sht = offense.get("shooting", 0)
        awa = offense.get("attacking_awareness", 0)
        bct = offense.get("ball_control", 0)
        pas = offense.get("passing", 0)
        strn = physical.get("strength", 0)
        dfa = defense.get("defensive_awareness", 0)

        if spd >= 80 and sta <= 75:
            warnings.append(
                f"速度({spd})与耐力({sta})严重不平衡：爆发力优秀但体能储备不足，"
                "比赛后半段速度优势将大幅下降，需增加耐力专项训练"
            )
        if spd >= 80 and strn <= 60:
            warnings.append(
                f"速度({spd})与力量({strn})不平衡：速度快但对抗中容易被挤开，"
                "需加强核心力量和下肢力量训练"
            )
        if sht >= 70 and awa <= 68:
            warnings.append(
                f"射门({sht})与进攻意识({awa})不匹配：终结能力尚可但跑位选择欠佳，"
                "需加强空间感知和无球跑动训练"
            )
        if bct >= 75 and pas <= 73:
            warnings.append(
                f"控球({bct})与传球({pas})不匹配：个人盘带出色但出球质量不足，"
                "需增加传球精度和传球决策训练"
            )
        if spd >= 80 and dfa <= 45:
            warnings.append(
                f"速度({spd})与防守意识({dfa})差距极大：作为边锋在高位逼抢体系中，"
                "丢球后回防意识和选位亟待提高"
            )

        return warnings

    def _generate_default_plan(self, weakest, strongest, imbalances) -> dict:
        focus = [format_attr_entry(e) for e in weakest[:2]]
        return {
            "focus_areas": focus,
            "weekly_schedule": {
                "周一": {"上午": f"{focus[0]}专项训练 + 技术基础", "下午": "传球与控球组合练习"},
                "周二": {"上午": "力量训练（深蹲/硬拉/卧推/核心）", "下午": "恢复拉伸 + 泡沫轴"},
                "周三": {"上午": f"{focus[1]}专项训练", "下午": "敏捷绳梯 + 变向跑 + 射门"},
                "周四": {"上午": "战术训练（位置感与跑位意识）", "下午": "恢复 + 冰浴"},
                "周五": {"上午": f"{focus[0]}与{focus[1]}组合训练", "下午": "7v7 对抗赛"},
                "周六": {"上午": "轻度技术保持 + 定位球", "下午": "完全休息"},
                "周日": {"全天": "休息日"},
            },
            "drill_details": [
                {"name": f"{focus[0]}提升训练", "sets": "6-8组", "frequency": "每周3次",
                 "description": "根据 RAG 检索结果制定"},
                {"name": f"{focus[1]}提升训练", "sets": "4-6组", "frequency": "每周2次",
                 "description": "根据 RAG 检索结果制定"},
            ],
            "imbalance_notes": imbalances or ["各项属性发展相对均衡"],
            "attribute_update_suggestions": {},
            "notes": "训练前必做 FIFA 11+ 热身方案，训练后充分拉伸。每周总训练负荷不超过 280 单位。",
        }


def create_coach_node(llm: BaseChatModel):
    agent = CoachAgent(llm)

    def node_fn(state: Dict[str, Any]) -> Dict[str, Any]:
        return agent.run(state)

    return node_fn
