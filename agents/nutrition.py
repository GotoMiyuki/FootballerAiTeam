"""
FootballAI Career Agent - Nutrition Agent（运动营养与恢复）

P1 ReAct 升级：
- LLM 自主决定调用 NutritionCalculatorTool 计算 BMI/BMR/TDEE/宏量营养素
- 保留代码层预处理（营养目标推断）
- Thought → Action → Observation → Finish 循环
"""

import json
from output_validation import parse_json, validate_specialist, validate_plan, validate_review_shape
from typing import Dict, Any
from langchain_core.language_models import BaseChatModel

from agents.base import BaseAgent
from execution_contracts import guarded, OutputError, MissingInput, review_passed
from prompts.agent_prompts import (
    NUTRITION_DOMAIN_IDENTITY,
    NUTRITION_GUIDE,
    build_mission_context,
)
from tools import NUTRITION_TOOLS
from utils.helpers import describe_player_attributes
from registry import get_active_subtask


class NutritionAgent(BaseAgent):
    """运动营养师 Agent — ReAct-powered"""

    def __init__(self, llm: BaseChatModel):
        super().__init__(llm=llm, tools=NUTRITION_TOOLS)

    @property
    def name(self) -> str:
        return "nutrition"

    @property
    def role(self) -> str:
        return "运动营养师（Sports Nutritionist）"

    @property
    def system_prompt(self) -> str:
        return f"{NUTRITION_DOMAIN_IDENTITY}\n\n{NUTRITION_GUIDE}"

    @guarded("Nutrition")
    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        mission = state.get("mission", {})
        domain_contrib = mission.get("domain_contributions", {}).get("Nutrition", {})
        subtask = get_active_subtask(state, {"nutrition_plan"})

        if not subtask and not domain_contrib.get("needed", False):
            return {"iteration": state.get("iteration", 0) + 1}

        player = state.get("player_profile", {})
        missing = [key for key in ('height', 'weight', 'age', 'training_intensity') if player.get(key) is None]
        if missing:
            raise MissingInput('营养计算缺少实际记录：' + '、'.join(missing))
        focus = subtask.get("goal") if subtask else domain_contrib.get("focus", mission.get("primary_goal", "制定营养方案"))

        # ---- 代码层预处理 ----
        height, weight, age, intensity = (player[key] for key in ('height', 'weight', 'age', 'training_intensity'))
        intensity_map = {"Low": "light", "Medium": "moderate", "High": "high", "Very High": "very_high"}
        if intensity not in intensity_map:
            raise MissingInput('营养计算需要明确的训练强度：Low、Medium、High 或 Very High')
        activity_level = intensity_map[intensity]
        nutrition_goal = self._infer_nutrition_goal(mission, player, focus)

        # ---- Layer 2: Mission Context ----
        mission_context = build_mission_context(mission, "Nutrition", subtask)

        # ---- ReAct Task Prompt ----
        task_prompt = f"""{mission_context}

## 球员数据
- 身高: {height}cm
- 体重: {weight}kg
- 年龄: {age}岁
- 位置: {player.get('position') or '未知'}
- 训练强度: {intensity} (activity_level={activity_level})
- 营养目标: {nutrition_goal}

## 身体属性
{describe_player_attributes(player.get('attributes', {}), player.get('other_features', {}))}

## 可用工具
- **NutritionCalculatorTool**: 运动营养计算器。输入 JSON 参数:
  height_cm={height}, weight_kg={weight}, age={age}, gender="male",
  activity_level="{activity_level}", goal="{nutrition_goal}"
  gender="male" 是当前计算规则的假设，不是已观察球员事实；必须在方案限制中说明。
  返回 BMI, BMR, TDEE, 推荐热量和宏量营养素克数。

## 任务
{focus}

## 执行流程
1. 首先调用 NutritionCalculatorTool 获取基础代谢数据
2. 基于计算结果制定营养方案

## 输出格式
JSON，字段：daily_calories, carbs_g, protein_g, fat_g, bmi, bmr_kcal, tdee_kcal,
meal_plan（含 meal/time/food）, supplements, hydration_plan。
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
            result = validate_specialist("nutrition_plan", parse_json(content))
        except Exception:
            raise

        output = json.dumps(result, ensure_ascii=False, indent=2)

        return {
            "domain_outputs": {"Nutrition": output},
            "iteration": state.get("iteration", 0) + 1,
            "tool_call_log": tool_log,
        }

    @staticmethod
    def _infer_nutrition_goal(mission: Dict, player: Dict, task_focus: str = "") -> str:
        """从 Mission 推断营养目标。"""
        primary_goal = mission.get("primary_goal", "")
        focus = task_focus or mission.get("domain_contributions", {}).get("Nutrition", {}).get("focus", "")
        combined = f"{primary_goal} {focus}".lower()

        if any(w in combined for w in ["减脂", "减重", "降体重", "瘦"]):
            return "减脂"
        elif any(w in combined for w in ["增肌", "增重", "增肥", "壮"]):
            return "增肌"

        bmi_val = player['weight'] / (player['height'] / 100) ** 2
        if bmi_val < 18.5:
            return "增肌"
        elif bmi_val >= 25:
            return "减脂"
        return "维持"



def create_nutrition_node(llm: BaseChatModel):
    agent = NutritionAgent(llm)

    def node_fn(state: Dict[str, Any]) -> Dict[str, Any]:
        return agent.run(state)

    node_fn._telemetry_agent = agent
    return node_fn
