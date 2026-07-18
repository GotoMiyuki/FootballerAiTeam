"""
FootballAI Career Agent - Base Agent 抽象类

P1 升级：增加 ReAct 循环（Thought → Action → Observation → Finish）。
"""

import json
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from config import config

# ReAct 循环最大迭代次数（防止死循环和 Token 爆炸）
MAX_REACT_ITERATIONS = 5


class BaseAgent(ABC):
    """所有 Agent 的基类，提供 ReAct 推理循环与短期记忆机制。"""

    def __init__(
        self,
        llm: BaseChatModel,
        tools: Optional[List[BaseTool]] = None,
        memory_size: int = None,
    ):
        self.llm = llm
        self.tools = tools or []
        self.memory_size = memory_size or config.SHORT_MEMORY_SIZE
        self._short_memory: List[Dict[str, Any]] = []
        self._tool_call_log: List[Dict[str, Any]] = []

    @property
    @abstractmethod
    def name(self) -> str:
        """Agent 唯一名称"""
        ...

    @property
    @abstractmethod
    def role(self) -> str:
        """Agent 角色描述（中文）"""
        ...

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """Agent 系统提示词"""
        ...

    def get_context(
        self,
        messages: List[Dict[str, Any]],
        current_task: str = None,
    ) -> List[Dict[str, Any]]:
        """构建上下文窗口。"""
        if current_task:
            self._short_memory = [{"role": "user", "content": current_task}]
        else:
            self._short_memory = messages[-self.memory_size :] if messages else []
        return self._short_memory

    def add_to_memory(self, message: Dict[str, Any]) -> None:
        """向短期记忆中追加一条消息。"""
        self._short_memory.append(message)
        if len(self._short_memory) > self.memory_size:
            self._short_memory = self._short_memory[-self.memory_size :]

    def build_messages(self, user_input: str, context: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """构建发送给 LLM 的完整消息列表。"""
        ctx = context or self._short_memory
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(ctx)
        messages.append({"role": "user", "content": user_input})
        return messages

    @abstractmethod
    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Agent 核心执行逻辑。接收状态，返回更新后的状态。"""
        ...

    # ================================================================
    # P1: ReAct 推理循环（Thought → Action → Observation → Finish）
    # ================================================================

    def _run_react_loop(
        self,
        task_prompt: str,
        player_profile: Dict[str, Any] = None,
        max_iterations: int = MAX_REACT_ITERATIONS,
    ) -> tuple[str, List[Dict[str, Any]]]:
        """执行 ReAct 推理循环。

        LLM 绑定工具后自主决定何时调用工具、何时输出最终结果。
        循环结构：
            Thought(LLM推理) → Action(LLM选择工具) → Observation(工具执行结果)
            → Thought(LLM基于结果继续推理) → ... → Finish(LLM输出最终答案)

        Args:
            task_prompt: 任务描述 prompt。
            player_profile: 球员档案（可选，注入为上下文）。
            max_iterations: 最大推理迭代次数。

        Returns:
            (final_output, tool_call_log) — LLM 最终文本输出 + 工具调用日志。
        """
        self._tool_call_log = []

        messages = [SystemMessage(content=self.system_prompt)]

        if player_profile:
            profile_summary = self._format_player_context(player_profile)
            if profile_summary:
                messages.append(HumanMessage(content=profile_summary))

        messages.append(HumanMessage(content=task_prompt))

        llm_with_tools = self.llm.bind_tools(self.tools) if self.tools else self.llm

        for iteration in range(max_iterations):
            response = llm_with_tools.invoke(messages)
            messages.append(response)

            if hasattr(response, "tool_calls") and response.tool_calls:
                for tool_call in response.tool_calls:
                    tool_name = tool_call.get("name", "")
                    tool_args = tool_call.get("args", {})
                    tool_id = tool_call.get("id", "")

                    result_str = self._execute_tool(tool_name, tool_args)

                    self._tool_call_log.append({
                        "agent": self.name,
                        "tool": tool_name,
                        "args": tool_args,
                        "result_preview": result_str[:300],
                    })

                    messages.append(ToolMessage(
                        content=result_str,
                        tool_call_id=tool_id,
                    ))
            else:
                return response.content, self._tool_call_log

        return messages[-1].content, self._tool_call_log

    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """根据工具名称查找并执行工具。

        Args:
            tool_name: 工具名称（LLM function_call 中的 name）。
            tool_args: 工具参数字典。

        Returns:
            工具执行结果字符串。
        """
        for tool in self.tools:
            if tool.name == tool_name:
                try:
                    # LangChain Tool.invoke 接受 dict 参数
                    result = tool.invoke(tool_args)
                    return str(result) if not isinstance(result, str) else result
                except Exception as e:
                    return f"工具执行错误 [{tool_name}]: {type(e).__name__}: {str(e)}"

        return f"未找到工具 '{tool_name}'。可用工具: {[t.name for t in self.tools]}"

    @staticmethod
    def _format_player_context(player: Dict[str, Any]) -> str:
        """将球员档案格式化为简短上下文注入 ReAct 循环。"""
        if not player:
            return ""
        parts = [
            f"球员: {player.get('name', '未知')}",
            f"位置: {player.get('position', 'N/A')}",
            f"年龄: {player.get('age', 'N/A')}",
            f"身高: {player.get('height', 'N/A')}cm",
            f"体重: {player.get('weight', 'N/A')}kg",
            f"综合评分: {player.get('overall', 'N/A')}",
            f"训练强度: {player.get('training_intensity', 'N/A')}",
        ]
        attrs = player.get("attributes", {})
        if attrs:
            from utils.helpers import describe_player_attributes
            parts.append(f"\n能力值:\n{describe_player_attributes(attrs, player.get('other_features', {}))}")
        return "\n".join(parts)
