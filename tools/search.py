"""
FootballAI Career Agent - 联网搜索工具

使用 Tavily API 进行联网搜索，获取最新足球训练建议、规则、运动医学信息。
"""

import os
from langchain_core.tools import tool

from config import config


@tool
def SearchTool(query: str) -> str:
    """联网搜索工具。用于检索最新的足球训练方法、营养建议、伤病预防、职业发展趋势等实时信息。

    Args:
        query: 搜索关键词（支持中文或英文）。
               例如："足球边锋速度训练最新方法"、"UEFA winger training drills 2025"

    Returns:
        搜索结果摘要。
    """
    tavily_key = config.TAVILY_API_KEY
    if not tavily_key or tavily_key == "tvly-your-tavily-api-key-here":
        return _fallback_search(query)

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=tavily_key)
        response = client.search(
            query=query,
            search_depth="basic",
            max_results=5,
            include_answer=True,
        )

        results = []
        if response.get("answer"):
            results.append(f"摘要: {response['answer']}\n")

        for item in response.get("results", []):
            results.append(f"- {item.get('title', 'N/A')}")
            results.append(f"  {item.get('content', 'N/A')[:200]}...")
            results.append(f"  来源: {item.get('url', 'N/A')}\n")

        return "\n".join(results) if results else f"未找到关于 '{query}' 的相关结果。"

    except ImportError:
        return _fallback_search(query)
    except Exception as e:
        return f"搜索出错: {str(e)}\n\n{_fallback_search(query)}"


def _fallback_search(query: str) -> str:
    """当 Tavily API 不可用时的回退方案：返回基于知识的静态建议。"""
    knowledge_base = {
        "速度训练": """
【足球速度训练建议】
1. 短距离冲刺（10-30m）：每周2-3次，6-10组，组间休息90秒
2. 抗阻冲刺：使用弹力带或上坡跑，提升爆发力
3. 变向速度：T型测试、5-10-5折返跑
4. 反应速度：视觉信号启动冲刺训练
5. 恢复：速度训练间隔至少48小时
建议参考：FIFA Training Centre - Speed Development
""",
        "射门训练": """
【足球射门训练建议】
1. 定点射门：每天50-100次，注重准确性而非力量
2. 移动中射门：接球后一步调整射门
3. 弱势脚训练：占总射门训练的30%
4. 对抗下射门：增加防守压力模拟比赛场景
5. 定位球专项：任意球/点球单独训练
建议参考：UEFA Coaching - Finishing Drills
""",
        "伤病预防": """
【足球伤病预防建议】
1. FIFA 11+ 热身方案：比赛和训练前必做
2. 腘绳肌强化：北欧腘绳肌训练（Nordic Hamstring）
3. 核心稳定性：每周2-3次核心训练
4. 负荷管理：ACWR（急性:慢性负荷比）控制在0.8-1.3之间
5. 充分恢复：高强度训练后至少48小时恢复
建议参考：FIFA Medical - Injury Prevention
""",
        "营养": """
【足球运动员营养建议】
1. 赛前3天：碳水加载（7-10g/kg体重/天）
2. 赛前餐：赛前3-4小时，高碳水+适量蛋白+低脂肪
3. 比赛中：每15-20分钟补充碳水饮料（6-8%浓度）
4. 赛后恢复：30分钟内补充蛋白+碳水（1:3-4比例）
5. 日常蛋白：1.6-2.2g/kg体重/天
建议参考：UEFA Nutrition Guide, ISSN Sports Nutrition
""",
    }

    for keyword, info in knowledge_base.items():
        if keyword in query:
            return info.strip()

    return f"""未配置 Tavily API Key，且未找到关于 "{query}" 的本地知识。
请在 .env 中配置 TAVILY_API_KEY 以启用联网搜索，或尝试包含以下关键词：速度训练、射门训练、伤病预防、营养。"""


SEARCH_TOOLS = [SearchTool]
