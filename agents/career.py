"""
FootballAI Career Agent - 职业经纪人（长期战略层）

P1 ReAct 升级：
- LLM 自主决定何时调用 SearchTool 搜索俱乐部/联赛信息
- 保留代码层预处理（市场估值、边际价值、目标提取）
- Mode-based 路由：career_planning | transfer_analysis
- Thought → Action → Observation → Finish 循环
"""

import json
from typing import Dict, Any, List
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage

from agents.base import BaseAgent
from prompts.agent_prompts import (
    CAREER_DOMAIN_IDENTITY,
    CAREER_MODE_PROMPTS,
    build_mission_context,
)
from tools.database import (
    ReadCareerHistoryTool,
    ReadPlayerProfileTool,
    read_career_history,
)
from tools.search import SearchTool
from utils.helpers import describe_player_attributes


class CareerAgent(BaseAgent):
    """职业经纪人 Agent — ReAct-powered，聚焦长期战略层"""

    def __init__(self, llm: BaseChatModel):
        super().__init__(llm=llm, tools=[
            SearchTool,
            ReadCareerHistoryTool,
            ReadPlayerProfileTool,
        ])
        self._current_mode = "career_planning"

    @property
    def name(self) -> str:
        return "career"

    @property
    def role(self) -> str:
        return "职业经纪人（Career Agent）"

    @property
    def system_prompt(self) -> str:
        identity = CAREER_DOMAIN_IDENTITY
        guide = CAREER_MODE_PROMPTS.get(self._current_mode, CAREER_MODE_PROMPTS["career_planning"])
        return f"{identity}\n\n{guide}"

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        mission = state.get("mission", {})
        domain_contrib = mission.get("domain_contributions", {}).get("Career", {})

        if not domain_contrib.get("needed", False):
            return {"iteration": state.get("iteration", 0) + 1}

        player = state.get("player_profile", {})

        # ---- 从 Mission 获取任务参数 ----
        mode = domain_contrib.get("mode", "career_planning")
        focus = domain_contrib.get("focus", mission.get("primary_goal", "制定职业发展规划"))

        # mode 合法性校验
        mode_fallback = False
        if mode not in ("career_planning", "transfer_analysis"):
            print(f"[Career Warn] 非法 mode '{mode}'，fallback 到 career_planning")
            mode_fallback = True
            mode = "career_planning"

        self._current_mode = mode

        # ---- 公共预计算 ----
        overall = player.get("overall", 72)
        age = player.get("age", 20)
        position = player.get("position", "LW")
        attributes = player.get("attributes", {})
        other = player.get("other_features", {})
        career_history = read_career_history()

        market_value = self._estimate_market_value(
            overall, age, position,
            other.get("form_consistency", 5),
            other.get("injury_resistance", 3),
            other.get("weak_foot_accuracy", 3),
        )

        marginal = self._compute_marginal_value(
            attributes, overall, age, position,
            other.get("form_consistency", 5),
            other.get("injury_resistance", 3),
            other.get("weak_foot_accuracy", 3),
        )

        current_level = self._get_current_level(overall)

        # ---- Layer 2: Mission Context ----
        mission_context = build_mission_context(mission, "Career")

        # ---- Mode 分派的 ReAct 循环 ----
        if mode == "transfer_analysis":
            result, tool_log = self._run_transfer_analysis_react(
                player, focus, overall, age, position,
                attributes, other, market_value, current_level,
                career_history, mission_context,
            )
        else:
            result, tool_log = self._run_career_planning_react(
                player, focus, overall, age, position,
                attributes, other, market_value, current_level,
                career_history, mission_context, marginal,
            )

        output = json.dumps(result, ensure_ascii=False, indent=2)
        return {
            "domain_outputs": {"Career": output},
            "iteration": state.get("iteration", 0) + 1,
            "tool_call_log": tool_log,
            "_mode_fallback": mode_fallback,
        }

    # ================================================================
    # Mode: career_planning (ReAct)
    # ================================================================
    def _run_career_planning_react(
        self, player, focus, overall, age, position,
        attributes, other, market_value, current_level,
        career_history, mission_context, marginal,
    ) -> tuple:
        task_prompt = f"""{mission_context}

## 球员数据
- 姓名: {player.get('name', '球员')}
- 位置: {position} | 年龄: {age} | 综合评分: {overall}
- 当前级别: {current_level}
- 市场估值: €{market_value:,}

## 四维属性
{describe_player_attributes(attributes, other)}

## 球员特征
- 状态稳定性: {other.get('form_consistency', '?')}/8
- 抗伤能力: {other.get('injury_resistance', '?')}/5
- 非惯用脚: 频率{other.get('weak_foot_frequency','?')}/5, 精度{other.get('weak_foot_accuracy','?')}/5

## 职业历史
{json.dumps(career_history, ensure_ascii=False, indent=2) if career_history else '暂无'}

## 边际价值参考（核心属性 +5 后的身价溢价）
{json.dumps(marginal, ensure_ascii=False, indent=2)}

## 可用工具
- **SearchTool**: 联网搜索足球行业最新动态（联赛薪资水平、球员发展路径案例等）
- **ReadCareerHistoryTool**: 读取完整职业发展历史

## 任务
{focus}

## 执行流程
1. 如需了解行业最新动态，使用 SearchTool 搜索
2. 基于球员数据和边际价值分析，规划职业发展路径
3. 以 Mission 核心目标为最高准则

## 输出格式
JSON，字段：current_status, career_paths（含 direction/description/pros/cons/timeline）,
marginal_value_analysis, recommendations, risks。
只输出 JSON，不要其他文本。"""

        raw_output, tool_log = self._run_react_loop(
            task_prompt=task_prompt,
            player_profile=player,
        )

        content = raw_output.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        try:
            return json.loads(content), tool_log
        except (json.JSONDecodeError, Exception):
            return {
                "current_status": {"overall": overall, "age": age, "estimated_market_value": f"€{market_value:,}"},
                "career_paths": [
                    {"direction": "保守", "description": "国内稳定球队，保障出场时间", "pros": "稳定性高", "cons": "成长空间有限", "timeline": "1-2年"},
                    {"direction": "激进", "description": "欧洲二级联赛，高风险高回报", "pros": "成长空间大", "cons": "竞争激烈", "timeline": "2-3年"},
                    {"direction": "商业", "description": "中东/北美，薪资优先", "pros": "经济回报高", "cons": "竞技成长有限", "timeline": "1-2年"},
                ],
                "marginal_value_analysis": marginal,
                "recommendations": ["优先提升 ROI 最高的属性"],
                "risks": ["需关注抗伤能力和状态稳定性"],
            }, tool_log

    # ================================================================
    # Mode: transfer_analysis (ReAct)
    # ================================================================
    def _run_transfer_analysis_react(
        self, player, focus, overall, age, position,
        attributes, other, market_value, current_level,
        career_history, mission_context,
    ) -> tuple:
        extracted_targets = self._extract_targets(focus)
        search_hint = ""
        if extracted_targets:
            search_hint = (
                f"\n用户提及的目标: {', '.join(extracted_targets)}。"
                f"请使用 SearchTool 搜索这些俱乐部/联赛的战术风格、引援策略、联赛环境。"
                f"搜索示例: \"{extracted_targets[0]} 足球 战术风格 引援策略 {position} {age}岁\""
            )

        task_prompt = f"""{mission_context}

## 球员数据
- 姓名: {player.get('name', '球员')}
- 位置: {position} | 年龄: {age} | 综合评分: {overall}
- 当前级别: {current_level}
- 市场估值: €{market_value:,}

## 四维属性
{describe_player_attributes(attributes, other)}

## 球员特征
- 状态稳定性: {other.get('form_consistency', '?')}/8
- 抗伤能力: {other.get('injury_resistance', '?')}/5
- 非惯用脚: 频率{other.get('weak_foot_frequency','?')}/5, 精度{other.get('weak_foot_accuracy','?')}/5
{search_hint}

## 可用工具
- **SearchTool**: 联网搜索俱乐部战术风格、联赛环境、转会市场行情。每次搜索建议针对一个目标俱乐部/联赛。

## 任务
{focus}

## 分析维度
1. **战术适配性**: 目标俱乐部/联赛的战术体系与球员技术特征的匹配度
2. **联赛生存环境**: 外援政策、身体对抗强度、比赛节奏、薪资结构
3. **成长潜力与风险**: 比赛时间保障、训练水平、伤病风险、Plan B

## 输出格式
JSON，字段：current_status, target_clubs（含 tactical_fit/league_environment/growth_potential/feasibility）,
market_valuation, recommendations。
只输出 JSON，不要其他文本。"""

        raw_output, tool_log = self._run_react_loop(
            task_prompt=task_prompt,
            player_profile=player,
        )

        content = raw_output.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        try:
            return json.loads(content), tool_log
        except (json.JSONDecodeError, Exception):
            return {
                "current_status": {"overall": overall, "age": age, "estimated_market_value": f"€{market_value:,}"},
                "targets_analyzed": extracted_targets or ["未指定目标"],
                "analysis": "JSON 解析失败，已触发 fallback",
                "recommendations": ["建议明确目标俱乐部/联赛后重新分析"],
            }, tool_log

    # ================================================================
    # 目标提取（代码层预处理）
    # ================================================================
    def _extract_targets(self, task: str) -> List[str]:
        extract_prompt = f"""从以下文本中提取用户提及的足球俱乐部名称或联赛名称。
只提取具体的名称，不要编造。如果没有提及任何俱乐部或联赛，返回空列表。

文本：
{task[:1500]}

请只输出 JSON 数组，例如：["皇家马德里", "英超"] 或 []"""

        try:
            response = self.llm.invoke([
                SystemMessage(content="你是一个信息提取助手。只输出 JSON 数组。"),
                HumanMessage(content=extract_prompt),
            ])
            content = response.content.strip()
            if "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            targets = json.loads(content)
            if isinstance(targets, list):
                return targets[:5]
        except Exception:
            pass
        return []

    # ================================================================
    # 辅助：市场估值、边际价值（保留原逻辑不变）
    # ================================================================
    def _estimate_market_value(
        self, overall: int, age: int, position: str,
        form_consistency: int = 5, injury_resistance: int = 3, weak_foot_accuracy: int = 3,
    ) -> int:
        if overall >= 80:
            base = 800000
        elif overall >= 75:
            base = 300000
        elif overall >= 70:
            base = 80000
        elif overall >= 60:
            base = 30000
        elif overall >= 50:
            base = 10000
        else:
            base = 3000

        if 20 <= age <= 25:
            base = int(base * 1.5)
        elif 26 <= age <= 28:
            base = int(base * 1.3)
        elif age > 30:
            base = int(base * 0.6)

        if position in ("RW", "LW", "ST", "CF"):
            base = int(base * 1.2)

        if form_consistency >= 7:
            base = int(base * 1.15)
        elif form_consistency <= 3:
            base = int(base * 0.85)

        if injury_resistance >= 4:
            base = int(base * 1.1)
        elif injury_resistance <= 2:
            base = int(base * 0.8)

        if weak_foot_accuracy >= 4:
            base = int(base * 1.15)

        return base

    def _get_current_level(self, overall: int) -> str:
        if overall >= 80:
            return "欧洲顶级联赛"
        elif overall >= 70:
            return "中超/欧洲次级联赛"
        elif overall >= 60:
            return "中甲/J2联赛"
        elif overall >= 55:
            return "中乙（发展期）"
        elif overall >= 50:
            return "中冠（磨练期）"
        else:
            return "业余联赛/大学校队"

    def _compute_marginal_value(
        self, attributes, overall, age, position,
        form_consistency, injury_resistance, weak_foot_accuracy,
    ) -> List[Dict]:
        baseline = self._estimate_market_value(
            overall, age, position, form_consistency, injury_resistance, weak_foot_accuracy,
        )
        key_attrs = [
            ("speed", attributes.get("physical", {}).get("speed", 0)),
            ("shooting", attributes.get("offense", {}).get("shooting", 0)),
            ("ball_control", attributes.get("offense", {}).get("ball_control", 0)),
            ("passing", attributes.get("offense", {}).get("passing", 0)),
            ("stamina", attributes.get("physical", {}).get("stamina", 0)),
        ]
        results = []
        for attr_name, current_val in key_attrs:
            if current_val == 0:
                continue
            overall_gain = 1.5 if attr_name in ("speed", "shooting") else 1.0
            projected = min(overall + int(overall_gain), 99)
            projected_value = self._estimate_market_value(
                projected, age, position, form_consistency, injury_resistance, weak_foot_accuracy,
            )
            premium = round((projected_value - baseline) / baseline * 100, 1) if baseline > 0 else 0
            results.append({
                "attribute": attr_name,
                "current_value": current_val,
                "projected_after_plus5": min(current_val + 5, 99),
                "estimated_overall_gain": f"+{overall_gain}",
                "premium_pct": f"+{premium}%",
            })
        return results


def create_career_node(llm: BaseChatModel):
    agent = CareerAgent(llm)

    def node_fn(state: Dict[str, Any]) -> Dict[str, Any]:
        return agent.run(state)

    return node_fn
