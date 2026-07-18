"""
FootballAI Career Agent - Nutrition Agent（运动营养与恢复）

P1 ReAct 升级：
- LLM 自主决定调用 NutritionCalculatorTool 计算 BMI/BMR/TDEE/宏量营养素
- 保留代码层预处理（营养目标推断）
- Thought → Action → Observation → Finish 循环
"""

import json
from typing import Dict, Any
from langchain_core.language_models import BaseChatModel

from agents.base import BaseAgent
from prompts.agent_prompts import (
    NUTRITION_DOMAIN_IDENTITY,
    NUTRITION_GUIDE,
    build_mission_context,
)
from tools.calculator import NutritionCalculatorTool
from utils.helpers import describe_player_attributes


class NutritionAgent(BaseAgent):
    """运动营养师 Agent — ReAct-powered"""

    def __init__(self, llm: BaseChatModel):
        super().__init__(llm=llm, tools=[NutritionCalculatorTool])

    @property
    def name(self) -> str:
        return "nutrition"

    @property
    def role(self) -> str:
        return "运动营养师（Sports Nutritionist）"

    @property
    def system_prompt(self) -> str:
        return f"{NUTRITION_DOMAIN_IDENTITY}\n\n{NUTRITION_GUIDE}"

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        mission = state.get("mission", {})
        domain_contrib = mission.get("domain_contributions", {}).get("Nutrition", {})

        if not domain_contrib.get("needed", False):
            return {"iteration": state.get("iteration", 0) + 1}

        player = state.get("player_profile", {})
        focus = domain_contrib.get("focus", mission.get("primary_goal", "制定营养方案"))

        # ---- 代码层预处理 ----
        height = player.get("height", 175)
        weight = player.get("weight", 70)
        age = player.get("age", 22)
        intensity = player.get("training_intensity", "High")
        intensity_map = {"Low": "light", "Medium": "moderate", "High": "high", "Very High": "very_high"}
        activity_level = intensity_map.get(intensity, "moderate")
        nutrition_goal = self._infer_nutrition_goal(mission, player)

        # ---- Layer 2: Mission Context ----
        mission_context = build_mission_context(mission, "Nutrition")

        # ---- ReAct Task Prompt ----
        task_prompt = f"""{mission_context}

## 球员数据
- 身高: {height}cm
- 体重: {weight}kg
- 年龄: {age}岁
- 位置: {player.get('position', 'LW')}
- 训练强度: {intensity} (activity_level={activity_level})
- 营养目标: {nutrition_goal}

## 身体属性
{describe_player_attributes(player.get('attributes', {}), player.get('other_features', {}))}

## 可用工具
- **NutritionCalculatorTool**: 运动营养计算器。输入 JSON 参数:
  height_cm={height}, weight_kg={weight}, age={age}, gender="male",
  activity_level="{activity_level}", goal="{nutrition_goal}"
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
            result = json.loads(content)
        except (json.JSONDecodeError, Exception):
            result = self._generate_default_nutrition_plan()

        output = json.dumps(result, ensure_ascii=False, indent=2)

        return {
            "domain_outputs": {"Nutrition": output},
            "iteration": state.get("iteration", 0) + 1,
            "tool_call_log": tool_log,
        }

    @staticmethod
    def _infer_nutrition_goal(mission: Dict, player: Dict) -> str:
        """从 Mission 推断营养目标。"""
        primary_goal = mission.get("primary_goal", "")
        focus = mission.get("domain_contributions", {}).get("Nutrition", {}).get("focus", "")
        combined = f"{primary_goal} {focus}".lower()

        if any(w in combined for w in ["减脂", "减重", "降体重", "瘦"]):
            return "减脂"
        elif any(w in combined for w in ["增肌", "增重", "增肥", "壮"]):
            return "增肌"

        bmi_val = player.get("weight", 70) / (player.get("height", 175) / 100) ** 2
        if bmi_val < 18.5:
            return "增肌"
        elif bmi_val >= 25:
            return "减脂"
        return "维持"

    @staticmethod
    def _generate_default_nutrition_plan() -> dict:
        return {
            "daily_calories": 2800,
            "carbs_g": 350,
            "protein_g": 140,
            "fat_g": 85,
            "meal_plan": [
                {"meal": "早餐", "food": "燕麦粥、香蕉2根、水煮蛋3个", "time": "07:30"},
                {"meal": "午餐", "food": "糙米饭、烤鸡胸肉200g、西兰花", "time": "12:00"},
                {"meal": "训练后加餐", "food": "蛋白奶昔+全麦面包2片", "time": "16:30"},
                {"meal": "晚餐", "food": "红薯、三文鱼150g、混合蔬菜沙拉", "time": "19:00"},
            ],
            "supplements": ["乳清蛋白粉", "维生素D3", "鱼油"],
            "hydration_plan": "每日饮水3L，训练中每15分钟补水150-200ml",
        }


def create_nutrition_node(llm: BaseChatModel):
    agent = NutritionAgent(llm)

    def node_fn(state: Dict[str, Any]) -> Dict[str, Any]:
        return agent.run(state)

    return node_fn
