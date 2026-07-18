"""
FootballAI Career Agent - 计算器工具

提供运动营养相关的计算功能：
- BMI（身体质量指数）
- BMR（基础代谢率）
- TDEE（每日总能量消耗）
- 宏量营养素推荐量
"""

import json
from typing import Dict, Any
from langchain_core.tools import tool


def calculate_bmi(height_cm: float, weight_kg: float) -> float:
    """计算 BMI（身体质量指数）。
    BMI = 体重(kg) / 身高(m)²
    """
    height_m = height_cm / 100.0
    return round(weight_kg / (height_m ** 2), 1)


def calculate_bmr(height_cm: float, weight_kg: float, age: int, gender: str = "male") -> float:
    """使用 Mifflin-St Jeor 公式计算 BMR（基础代谢率）。

    男性: BMR = 10×体重 + 6.25×身高 - 5×年龄 + 5
    女性: BMR = 10×体重 + 6.25×身高 - 5×年龄 - 161
    """
    bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age
    if gender.lower() == "male":
        bmr += 5
    else:
        bmr -= 161
    return round(bmr, 0)


def calculate_tdee(bmr: float, activity_level: str) -> float:
    """根据活动水平计算 TDEE（每日总能量消耗）。

    活动水平系数：
    - sedentary（久坐）: 1.2
    - light（轻度活动，每周1-3次训练）: 1.375
    - moderate（中度活动，每周3-5次训练）: 1.55
    - high（高强度，每周6-7次训练）: 1.725
    - very_high（极高强度，每日2次训练+比赛）: 1.9
    """
    multipliers = {
        "sedentary": 1.2,
        "light": 1.375,
        "moderate": 1.55,
        "high": 1.725,
        "very_high": 1.9,
    }
    multiplier = multipliers.get(activity_level.lower(), 1.55)
    return round(bmr * multiplier, 0)


def calculate_macros(tdee: float, goal: str, weight_kg: float) -> Dict[str, float]:
    """根据 TDEE 和目标计算宏量营养素推荐量。

    Args:
        tdee: 每日总能量消耗
        goal: 目标（"减脂" / "增肌" / "维持"）
        weight_kg: 体重 (kg)

    Returns:
        包含 calories, protein_g, carbs_g, fat_g 的字典
    """
    if goal == "减脂":
        calories = tdee - 400  # 赤字 400 kcal
        protein_g = round(weight_kg * 2.0, 0)  # 2.0g/kg
        fat_g = round(weight_kg * 0.8, 0)       # 0.8g/kg
    elif goal == "增肌":
        calories = tdee + 300  # 盈余 300 kcal
        protein_g = round(weight_kg * 2.2, 0)  # 2.2g/kg
        fat_g = round(weight_kg * 1.0, 0)       # 1.0g/kg
    else:  # 维持
        calories = tdee
        protein_g = round(weight_kg * 1.8, 0)  # 1.8g/kg
        fat_g = round(weight_kg * 0.9, 0)       # 0.9g/kg

    # 剩余热量分配给碳水（1g碳水=4kcal, 1g蛋白=4kcal, 1g脂肪=9kcal）
    protein_cal = protein_g * 4
    fat_cal = fat_g * 9
    carbs_cal = calories - protein_cal - fat_cal
    carbs_g = max(0, round(carbs_cal / 4, 0))

    return {
        "daily_calories": round(calories, 0),
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
    }


@tool
def NutritionCalculatorTool(input_json: str) -> str:
    """足球运动员营养计算器。根据身体数据和训练强度计算 BMI、BMR、TDEE 及宏量营养素推荐量。

    输入 JSON 格式：
    {
        "height_cm": 173,
        "weight_kg": 58,
        "age": 20,
        "gender": "male",
        "activity_level": "high",
        "goal": "增肌"
    }

    activity_level 可选值: sedentary, light, moderate, high, very_high
    goal 可选值: 减脂, 增肌, 维持

    返回包含 BMI、BMR、TDEE、推荐热量和宏量营养素克数的 JSON。
    """
    try:
        data = json.loads(input_json)
    except json.JSONDecodeError:
        return "错误：输入不是有效的 JSON 格式。"

    height_cm = float(data.get("height_cm", 175))
    weight_kg = float(data.get("weight_kg", 70))
    age = int(data.get("age", 22))
    gender = data.get("gender", "male")
    activity_level = data.get("activity_level", "moderate")
    goal = data.get("goal", "维持")

    bmi = calculate_bmi(height_cm, weight_kg)
    bmr = calculate_bmr(height_cm, weight_kg, age, gender)
    tdee = calculate_tdee(bmr, activity_level)
    macros = calculate_macros(tdee, goal, weight_kg)

    result = {
        "bmi": bmi,
        "bmi_category": _bmi_category(bmi),
        "bmr_kcal": bmr,
        "tdee_kcal": tdee,
        "activity_level": activity_level,
        "goal": goal,
        **macros,
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


def _bmi_category(bmi: float) -> str:
    """BMI 分类。"""
    if bmi < 18.5:
        return "偏瘦"
    elif bmi < 24.0:
        return "正常"
    elif bmi < 28.0:
        return "超重"
    else:
        return "肥胖"


# 工具列表
CALCULATOR_TOOLS = [NutritionCalculatorTool]
