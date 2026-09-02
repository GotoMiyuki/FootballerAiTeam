"""
FootballAI Career Agent - 联网搜索工具

使用 Tavily API 进行联网搜索，获取最新足球训练建议、规则、运动医学信息。
"""

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
        return "联网搜索不可用（未配置 TAVILY_API_KEY）。请改用 FootballKnowledgeRAG 检索本地足球知识库。"

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
        return "联网搜索不可用（未安装 tavily-python）。请改用 FootballKnowledgeRAG 检索本地足球知识库。"
    except Exception as e:
        return f"搜索出错: {str(e)}"


SEARCH_TOOLS = [SearchTool]
