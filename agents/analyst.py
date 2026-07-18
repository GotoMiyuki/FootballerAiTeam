"""
FootballAI Career Agent - Performance Analyst Agent（表现数据分析）

P1 ReAct 升级：
- LLM 自主决定何时读取训练/比赛历史数据
- 保留代码层分析计算（ACWR、交叉维度分析、趋势、伤病风险）
- Thought → Action → Observation → Finish 循环
"""

import json
from typing import Dict, Any, List
from langchain_core.language_models import BaseChatModel

from agents.base import BaseAgent
from prompts.agent_prompts import (
    ANALYST_DOMAIN_IDENTITY,
    ANALYST_GUIDE,
    build_mission_context,
)
from tools.database import (
    ReadTrainingHistoryTool,
    ReadMatchHistoryTool,
    read_training_history,
    read_match_history,
)
from tools.search import SearchTool
from utils.helpers import (
    get_weakest_attributes,
    get_strongest_attributes,
    format_attr_entry,
    describe_player_attributes,
    category_display_name,
    attr_display_name,
)


class AnalystAgent(BaseAgent):
    """表现分析师 Agent — ReAct-powered，聚焦数据诊断"""

    def __init__(self, llm: BaseChatModel):
        super().__init__(llm=llm, tools=[
            ReadTrainingHistoryTool,
            ReadMatchHistoryTool,
            SearchTool,
        ])

    @property
    def name(self) -> str:
        return "analyst"

    @property
    def role(self) -> str:
        return "表现分析师（Performance Analyst）"

    @property
    def system_prompt(self) -> str:
        return f"{ANALYST_DOMAIN_IDENTITY}\n\n{ANALYST_GUIDE}"

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        mission = state.get("mission", {})
        domain_contrib = mission.get("domain_contributions", {}).get("Analyst", {})

        if not domain_contrib.get("needed", False):
            return {"iteration": state.get("iteration", 0) + 1}

        player = state.get("player_profile", {})
        focus = domain_contrib.get("focus", mission.get("primary_goal", "分析球员发展趋势"))

        # ---- 代码层预处理：加载历史数据并计算 ----
        training_history = read_training_history()
        match_history = read_match_history()

        weekly_loads = self._extract_weekly_loads(training_history)
        match_ratings = self._extract_match_ratings(match_history)

        attributes = player.get("attributes", {})
        other = player.get("other_features", {})
        injury_resistance = other.get("injury_resistance", 3)
        form_consistency = other.get("form_consistency", 5)

        weakest = get_weakest_attributes(attributes, n=4)
        strongest = get_strongest_attributes(attributes, n=3)

        cross_analysis = self._cross_category_analysis(attributes, player.get("position", "LW"))
        injury_risk_detail = self._assess_injury_risk(weekly_loads, match_ratings, injury_resistance, player)
        trend_analysis = self._analyze_trends(weekly_loads, match_ratings, player)

        # ---- Layer 2: Mission Context ----
        mission_context = build_mission_context(mission, "Analyst")

        # ---- ReAct Task Prompt ----
        task_prompt = f"""{mission_context}

## 球员档案
- 姓名: {player.get('name', '球员')}
- 位置: {player.get('position', 'LW')}
- 年龄: {player.get('age', 20)}
- 综合评分: {player.get('overall', 72)}

## 四维属性
{describe_player_attributes(attributes, other)}

## 短板（最低4项）
{chr(10).join(f'- {format_attr_entry(e)}' for e in weakest)}

## 长项（最高3项）
{chr(10).join(f'- {format_attr_entry(e)}' for e in strongest)}

## 球员特征
- 非惯用脚频率: {other.get('weak_foot_frequency', '?')}/5
- 非惯用脚精度: {other.get('weak_foot_accuracy', '?')}/5
- 状态稳定性: {form_consistency}/8
- 抗伤能力: {injury_resistance}/5

## 代码预分析结果

### 交叉维度分析（属性不平衡）
{json.dumps(cross_analysis, ensure_ascii=False, indent=2)}

### 伤病风险详情
{json.dumps(injury_risk_detail, ensure_ascii=False, indent=2)}

### 近10周训练负荷
{json.dumps(weekly_loads[-10:], ensure_ascii=False)}

### 近期比赛表现
{json.dumps(match_ratings[-6:], ensure_ascii=False)}

### 基本趋势
{json.dumps(trend_analysis, ensure_ascii=False, indent=2)}

## 可用工具
- **ReadTrainingHistoryTool**: 读取完整训练历史（可查看更早的训练记录）
- **ReadMatchHistoryTool**: 读取完整比赛历史
- **SearchTool**: 联网搜索运动科学最新研究（如 ACWR 安全阈值、伤病预防指南）

## 任务
{focus}

## 执行流程
1. 如果预分析数据足够，直接基于数据生成诊断报告
2. 如需补充数据，使用工具读取更多历史记录
3. 你回答"是什么"和"为什么"，不提供训练方案（那是 Coach 的职责）

## 输出格式
JSON，字段：
- trends（含 attribute/change/status/risk）
- cross_category_findings（含 type/detail/severity）
- injury_risk（含 level/score/factors/detail）
- form_assessment
- recommendations
- summary
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
            result = {
                "period": "近12周",
                "trends": trend_analysis.get("trends", []),
                "cross_category_findings": cross_analysis,
                "injury_risk": injury_risk_detail,
                "form_assessment": f"状态稳定性评分 {form_consistency}/8",
                "recommendations": trend_analysis.get("recommendations", []),
                "summary": "训练负荷整体呈上升趋势。建议关注属性间的不平衡关系。",
            }

        output = json.dumps(result, ensure_ascii=False, indent=2)

        return {
            "domain_outputs": {"Analyst": output},
            "iteration": state.get("iteration", 0) + 1,
            "tool_call_log": tool_log,
        }

    def _extract_weekly_loads(self, history: List[Dict]) -> List[Dict]:
        loads = []
        for week in history[-12:]:
            loads.append({
                "week": week.get("week", ""),
                "focus": week.get("focus", ""),
                "weekly_load": week.get("weekly_load", 0),
                "avg_rpe": week.get("avg_rpe", 0),
                "notes": week.get("notes", ""),
            })
        return loads

    def _extract_match_ratings(self, history: List[Dict]) -> List[Dict]:
        return [
            {
                "date": m.get("date", ""),
                "opponent": m.get("opponent", ""),
                "rating": m.get("rating", 0),
                "goals": m.get("goals", 0),
                "assists": m.get("assists", 0),
                "minutes": m.get("minutes_played", 0),
            }
            for m in history[-6:]
        ]

    def _cross_category_analysis(self, attributes: Dict, position: str) -> list:
        findings = []
        offense = attributes.get("offense", {})
        defense = attributes.get("defense", {})
        physical = attributes.get("physical", {})

        spd = physical.get("speed", 0)
        sta = physical.get("stamina", 0)
        strn = physical.get("strength", 0)
        sht = offense.get("shooting", 0)
        awa = offense.get("attacking_awareness", 0)
        pas = offense.get("passing", 0)
        bct = offense.get("ball_control", 0)
        dfa = defense.get("defensive_awareness", 0)

        if spd >= 80 and sta <= 75:
            findings.append({
                "type": "身体/体能不匹配",
                "detail": f"速度({spd})远高于耐力({sta})，差距≥5点。前30分钟冲击力强但下半场跑动能力骤降。",
                "severity": "high",
            })
        if spd >= 80 and strn <= 60:
            findings.append({
                "type": "身体对抗劣势",
                "detail": f"速度({spd})与力量({strn})差距显著，高速突破时对抗能力不足。",
                "severity": "medium",
            })
        if bct >= 75 and pas <= 73:
            findings.append({
                "type": "技术/决策不匹配",
                "detail": f"控球({bct})出色但传球({pas})相对不足，倾向过度盘带。",
                "severity": "medium",
            })
        if sht >= 70 and awa <= 68:
            findings.append({
                "type": "射门/跑位不匹配",
                "detail": f"射门能力({sht})尚可但进攻意识({awa})偏弱，创造射门机会的跑位能力不足。",
                "severity": "medium",
            })
        if dfa <= 45 and spd >= 80:
            findings.append({
                "type": "高位逼抢战术适配风险",
                "detail": f"防守意识({dfa})极低但速度({spd})优秀，丢球后回防意识严重不足。",
                "severity": "high",
            })

        return findings

    def _assess_injury_risk(self, loads, ratings, injury_resistance, player) -> Dict[str, Any]:
        risk_score = 0
        factors = []

        if len(loads) >= 3:
            recent_rpe = [w["avg_rpe"] for w in loads[-3:]]
            avg_rpe = sum(recent_rpe) / len(recent_rpe)
            if avg_rpe > 8.0:
                risk_score += 3
                factors.append(f"近3周平均RPE={avg_rpe:.1f}，训练强度极高")
            elif avg_rpe > 7.0:
                risk_score += 1
                factors.append(f"近3周平均RPE={avg_rpe:.1f}，训练强度偏高")

            if len(loads) >= 4:
                acute = sum(w["weekly_load"] for w in loads[-1:]) / 1
                chronic = sum(w["weekly_load"] for w in loads[-4:]) / 4
                if chronic > 0:
                    acwr = acute / chronic
                    if acwr > 1.5:
                        risk_score += 2
                        factors.append(f"ACWR={acwr:.2f}，急性负荷远超慢性负荷")
                    elif acwr > 1.3:
                        risk_score += 1
                        factors.append(f"ACWR={acwr:.2f}，负荷增长偏快")

        if injury_resistance <= 2:
            risk_score += 2
            factors.append(f"抗伤能力仅 {injury_resistance}/5，易伤体质")
        elif injury_resistance <= 3:
            risk_score += 1
            factors.append(f"抗伤能力 {injury_resistance}/5，中等水平")

        if player.get("injury") and player["injury"] != "None":
            risk_score += 1
            factors.append(f"当前伤病: {player['injury']}")

        if risk_score >= 5:
            level, risk_detail = "高", "多项风险因素叠加，强烈建议减量周"
        elif risk_score >= 2:
            level, risk_detail = "中等", "存在一定风险因素，建议监控并适当调整"
        else:
            level, risk_detail = "低", "当前各项指标正常"

        return {"level": level, "score": risk_score, "factors": factors, "detail": risk_detail}

    def _analyze_trends(self, loads, ratings, player) -> Dict[str, Any]:
        trends = []

        if len(loads) >= 4:
            recent_loads = [w["weekly_load"] for w in loads[-4:]]
            early_loads = [w["weekly_load"] for w in loads[:4]]
            avg_recent = sum(recent_loads) / len(recent_loads)
            avg_early = sum(early_loads) / len(early_loads)

            if avg_recent > avg_early * 1.15:
                trends.append({"attribute": "训练负荷", "change": f"+{round(avg_recent - avg_early)}",
                               "status": "快速增长", "risk": "high"})
            elif avg_recent > avg_early:
                trends.append({"attribute": "训练负荷", "change": f"+{round(avg_recent - avg_early)}",
                               "status": "稳步增长", "risk": "medium"})

        if len(ratings) >= 3:
            recent_ratings = [r["rating"] for r in ratings[-3:]]
            avg_rating = sum(recent_ratings) / len(recent_ratings)
            trends.append({"attribute": "比赛评分", "change": f"近3场平均 {avg_rating:.1f}",
                           "status": "稳定" if avg_rating >= 6.8 else "需提升", "risk": "low"})

        recommendations = []
        if any(t.get("risk") == "high" for t in trends):
            recommendations.append("训练负荷增长较快，建议安排减量周")
        if player.get("injury") and player["injury"] != "None":
            recommendations.append(f"当前伤病状态：{player['injury']}，建议调整训练强度")

        attributes = player.get("attributes", {})
        if attributes:
            weakest = get_weakest_attributes(attributes, n=3)
            for cat, name, val in weakest:
                recommendations.append(
                    f"短板 [{category_display_name(cat)}] {attr_display_name(name)}({val})，建议关注"
                )

        return {"trends": trends, "recommendations": recommendations or ["保持当前节奏，各方面发展均衡"]}


def create_analyst_node(llm: BaseChatModel):
    agent = AnalystAgent(llm)

    def node_fn(state: Dict[str, Any]) -> Dict[str, Any]:
        return agent.run(state)

    return node_fn
