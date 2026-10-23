"""
FootballAI Career Agent - Skill Coach Agent（竞技能力发展）

P1 ReAct 升级：
- LLM 自主决定调用 RAG / Search / Database 工具
- 保留代码层预处理（短板识别、属性不平衡检测）
- Thought → Action → Observation → Finish 循环
"""

import json
from output_validation import parse_json, validate_specialist, validate_plan, validate_review_shape
from typing import Dict, Any
from langchain_core.language_models import BaseChatModel

from agents.base import BaseAgent
from execution_contracts import guarded, OutputError, MissingInput, review_passed
from prompts.agent_prompts import (
    COACH_DOMAIN_IDENTITY,
    COACH_GUIDE,
    build_mission_context,
)
from tools import COACH_TOOLS
from registry import get_active_subtask
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
        super().__init__(llm=llm, tools=COACH_TOOLS)

    @property
    def name(self) -> str:
        return "coach"

    @property
    def role(self) -> str:
        return "技能教练（Skill Coach）"

    @property
    def system_prompt(self) -> str:
        return f"{COACH_DOMAIN_IDENTITY}\n\n{COACH_GUIDE}"

    @guarded("Coach")
    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        mission = state.get("mission", {})
        domain_contrib = mission.get("domain_contributions", {}).get("Coach", {})
        subtask = get_active_subtask(state, {"skill_training"})

        if not subtask and not domain_contrib.get("needed", False):
            return {"iteration": state.get("iteration", 0) + 1}

        player = state.get("player_profile", {})
        focus = subtask.get("goal") if subtask else domain_contrib.get("focus", mission.get("primary_goal", "制定训练计划"))
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
        mission_context = build_mission_context(mission, "Coach", subtask)

        # ---- ReAct Task Prompt ----
        task_prompt = f"""{mission_context}

## 球员档案
- 姓名: {player.get('name', '球员')}
- 位置: {player.get('position') or '未知'}
- 年龄: {player.get('age') if player.get('age') is not None else '未知'}
- 综合评分: {player.get('overall') if player.get('overall') is not None else '未知'}
- 训练强度: {player.get('training_intensity') or '未知'}

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
- 工具仅可读取资料。属性变化是预测，不更新实际档案。

## 任务
{focus}

## 执行流程
1. 使用 FootballKnowledgeRAG 检索短板对应的专项训练方法（至少检索 2-3 个方向）
2. 使用 SearchTool 补充搜索最新训练理念
3. 综合分析后输出 JSON 训练计划

## 输出格式
JSON，字段：focus_areas, weekly_schedule, drill_details（含 name/sets/frequency/description）,
imbalance_notes, attribute_update_suggestions（训练4周后的预期属性变化，如 {{"physical": {{"speed": 83}}}}）, notes。
focus_areas 使用字符串列表；weekly_schedule 使用“日期/星期 → 活动文本或字符串列表”的对象，或 day/activities 的对象列表。sets 为正整数或明确的组数说明。
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
            result = validate_specialist("skill_training", parse_json(content))
        except Exception:
            raise

        citations = [citation for call in tool_log if call.get('status') == 'SUCCESS' for citation in call.get('citations', [])]
        result["references"] = citations

        output = json.dumps(result, ensure_ascii=False, indent=2)

        return {
            "domain_outputs": {"Coach": output},
            "iteration": state.get("iteration", 0) + 1,
            "tool_call_log": tool_log,
            "citations": citations,
        }

    def _detect_imbalances(self, attributes: Dict[str, Any]) -> list:
        """检测属性间的不平衡关系。"""
        needed = {'offense': ['shooting', 'attacking_awareness', 'ball_control', 'passing'],
                  'physical': ['speed', 'stamina', 'strength'], 'defense': ['defensive_awareness']}
        if any((attributes.get(category) or {}).get(key) is None for category, keys in needed.items() for key in keys):
            return ['能力观察不足，无法完成属性间不平衡评估']
        if any(v is None for category in attributes.values() if isinstance(category, dict) for v in category.values()):
            return ['存在未知能力值，仅分析已记录信息']
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



def create_coach_node(llm: BaseChatModel):
    agent = CoachAgent(llm)

    def node_fn(state: Dict[str, Any]) -> Dict[str, Any]:
        return agent.run(state)

    node_fn._telemetry_agent = agent
    return node_fn
