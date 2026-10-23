"""
FootballAI Career Agent - 联网搜索工具

使用 Tavily API 进行联网搜索，获取最新足球训练建议、规则、运动医学信息。
"""

from langchain_core.tools import tool

from config import config
from tools.errors import ToolExecutionError


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
        raise ToolExecutionError('UNAVAILABLE')

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

        if not results:
            raise ToolExecutionError('NO_DATA')
        return '\n'.join(results)

    except ImportError:
        raise ToolExecutionError('UNAVAILABLE')
    except ToolExecutionError:
        raise
    except Exception:
        raise ToolExecutionError('EXECUTION_ERROR')


SEARCH_TOOLS = [SearchTool]
