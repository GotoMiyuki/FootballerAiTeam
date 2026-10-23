"""
FootballAI Career Agent - Base Agent 抽象类

P1 升级：增加 ReAct 循环（Thought → Action → Observation → Finish）。
"""

import json
import time
from dataclasses import dataclass, field
from execution_contracts import ReactError
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from config import config
from utils.telemetry import token_usage_from_response

# ReAct 循环最大迭代次数（防止死循环和 Token 爆炸）
MAX_REACT_ITERATIONS = 5


def model_text(content):
    """Accept only provider text/string blocks, never stringify arbitrary data."""
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(isinstance(b, dict) and b.get('type') in {'text', 'output_text'}
                                         and isinstance(b.get('text'), str) for b in content):
        return '\n'.join(b['text'] for b in content)
    raise ValueError('Unsupported content shape')


@dataclass
class ReactResult:
    exit_reason: str
    text: str = ''
    tool_log: list = field(default_factory=list)
    observations: list = field(default_factory=list)

    def __iter__(self):
        # Transitional tuple consumption fails closed in all existing specialists.
        if self.exit_reason != 'FINAL':
            raise ReactError(self)
        yield self.text
        yield self.tool_log


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
        self._telemetry_events: List[Dict[str, Any]] = []

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

    # Telemetry is deliberately scoped to one graph-node invocation.  The graph
    # starts and drains this buffer, so agent implementations remain focused on
    # their domain result contract.
    def _reset_telemetry(self) -> None:
        self._telemetry_events = []

    def _consume_telemetry(self) -> List[Dict[str, Any]]:
        events = list(self._telemetry_events)
        self._telemetry_events = []
        return events

    def _invoke_model(self, model: Any, messages: Any, operation: str = "llm") -> Any:
        """Invoke a chat model and retain metadata only for controller telemetry."""
        started = time.perf_counter()
        try:
            response = model.invoke(messages)
        except Exception:
            # A provider failure is still an attempted LLM call.  Keep its
            # count/latency visible without retaining exception text or input.
            self._telemetry_events.append({
                "kind": "llm", "agent": self.name, "operation": operation,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "input_tokens": 0, "output_tokens": 0, "failed": True,
            })
            raise
        usage = token_usage_from_response(response)
        self._telemetry_events.append({
            "kind": "llm", "agent": self.name, "operation": operation,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3), **usage,
        })
        return response

    def _invoke_llm(self, messages: Any, operation: str = "llm") -> Any:
        return self._invoke_model(self.llm, messages, operation)

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
    ) -> ReactResult:
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
            ReactResult — 终止原因、最终文本、结构化工具日志与有效观察。
        """
        self._tool_call_log = []
        observations = []
        required_failure = False

        messages = [SystemMessage(content=self.system_prompt)]

        if player_profile:
            profile_summary = self._format_player_context(player_profile)
            if profile_summary:
                messages.append(HumanMessage(content=profile_summary))

        messages.append(HumanMessage(content=task_prompt))

        try:
            llm_with_tools = self.llm.bind_tools(self.tools) if self.tools else self.llm
        except Exception:
            return ReactResult('MODEL_FAILED')

        for iteration in range(max_iterations):
            try:
                response = self._invoke_model(llm_with_tools, messages, "react")
            except Exception:
                return ReactResult('MODEL_FAILED', tool_log=self._tool_call_log, observations=observations)
            messages.append(response)

            if hasattr(response, "tool_calls") and response.tool_calls:
                for tool_call in response.tool_calls:
                    tool_name = tool_call.get("name", "")
                    tool_args = tool_call.get("args", {})
                    tool_id = tool_call.get("id", "")

                    record = self._execute_tool_result(tool_name, tool_args, tool_id)
                    result_str = record['result']
                    self._tool_call_log.append(record)
                    if record['status'] == 'SUCCESS':
                        observations.append(record)
                    elif record['required']:
                        required_failure = True

                    messages.append(ToolMessage(
                        content=result_str,
                        tool_call_id=tool_id,
                    ))
            else:
                try:
                    final_text = model_text(response.content)
                except ValueError:
                    return ReactResult('INVALID_CONTENT', tool_log=self._tool_call_log, observations=observations)
                reason = 'TOOL_ERROR' if required_failure else 'FINAL' if final_text.strip() else 'EMPTY_OUTPUT'
                return ReactResult(reason, final_text if reason == 'FINAL' else '', self._tool_call_log, observations)

        return ReactResult('BUDGET_EXHAUSTED', tool_log=self._tool_call_log, observations=observations)

    def _execute_tool_result(self, tool_name, tool_args, tool_id):
        required = tool_name not in {'SearchTool'}
        record = {'agent': self.name, 'tool': tool_name, 'call_id': tool_id,
                  'required': required, 'status': 'ERROR', 'error_type': '', 'result': ''}
        selected = next((tool for tool in self.tools if tool.name == tool_name), None)
        if selected is None:
            record['error_type'] = 'UNKNOWN_TOOL'
        elif not isinstance(tool_args, dict):
            record['error_type'] = 'INVALID_ARGUMENTS'
        else:
            try:
                result = selected.invoke(tool_args)
                record.update(status='SUCCESS', result=result if isinstance(result, str) else json.dumps(result, ensure_ascii=False))
                if tool_name == 'FootballKnowledgeRAG':
                    from tools.rag import get_last_citations
                    record['citations'] = get_last_citations()
            except Exception as exc:
                record['error_type'] = getattr(exc, 'error_type', None) or ('INVALID_ARGUMENTS' if type(exc).__name__ in {'ValidationError', 'TypeError'} else 'EXECUTION_ERROR')
        if record['status'] == 'ERROR':
            record['result'] = json.dumps({'tool_error': record['error_type'], 'tool': tool_name})
        record['result_preview'] = record['result'][:300]
        return record

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
