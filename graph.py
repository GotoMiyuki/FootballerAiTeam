"""
FootballAI Career Agent - LangGraph 状态定义与图构建

多智能体协作系统的核心流程控制：
- Manager 节点：规划与聚合
- 专业 Agent 节点：纵向执行
- 条件边：动态路由（基于 AGENT_REGISTRY 自动生成）
"""

import json
from typing import TypedDict, List, Dict, Any, Annotated
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver

from registry import (
    AGENT_REGISTRY,
    SUB_AGENT_NAMES,
    DISPLAY_TO_NODE,
    FINAL_AGENT,
    get_sub_agent_node_names,
)


# ============================================================
# 自定义 Reducer
# ============================================================
# 用于标记 domain_outputs 中需要删除的键
_DELETE_SENTINEL = "__DELETE_KEY__"


def merge_domain_outputs(existing: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """合并 domain_outputs：新值浅层合并到已有值，而非替换。

    LangGraph TypedDict 默认对非 Annotated 字段使用替换语义，
    导致每个子 Agent 返回 {"domain_outputs": {"Coach": ...}} 时会
    清空前一个 Agent 的输出，路由函数认为已执行的 Agent 未执行，形成死循环。

    特殊规则：值为 "__DELETE_KEY__" 的键会被从已有值中删除，
    用于 Manager Assess 清除低质量输出后触发重新执行。
    """
    if existing is None:
        existing = {}
    if new is None:
        return existing
    merged = dict(existing)
    for key, value in new.items():
        if value == _DELETE_SENTINEL:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def merge_lists(existing: List[Any], new: List[Any]) -> List[Any]:
    """合并列表 reducer：追加而非替换，用于 tool_call_log 累积。"""
    if existing is None:
        existing = []
    if new is None:
        return existing
    return existing + new


# ============================================================
# AgentState 定义
# ============================================================
class AgentState(TypedDict):
    """多智能体系统全局状态。

    字段说明：
    - messages: 对话历史（支持 add_messages reducer）
    - player_profile: 球员档案（从 memory/player.json 加载）
    - mission: Manager 创建的全局 Mission 对象（整个 Workflow 的北极星）
    - domain_outputs: 各领域 Agent 的贡献输出（使用 merge reducer 累积）
    - synthesis_guide: Intent Checkpoint 生成的合成指引
    - final_report: Document Agent 生成的最终报告
    - iteration: 当前迭代计数（防止无限循环）
    - current_agent: 当前正在执行的 Agent 名称
    - tool_call_log: 各 Agent 的工具调用记录（使用 merge reducer 累积）
    - plan_version: Mission 计划版本号（每次 Replan 递增，上限 3）
    - replan_reason: 最近一次 Replan 的原因
    - reviewed_data: Reviewer 审查通过后整理的数据（供 Reporter 使用）
    - review_passed: Reviewer 审查是否通过
    - review_findings: Reviewer 发现的问题列表
    - review_conflicts: Reviewer 检测到的 Agent 间冲突
    - review_gaps: Reviewer 发现的信息缺口
    """
    messages: Annotated[List[Dict[str, Any]], add_messages]
    player_profile: Dict[str, Any]
    mission: Dict[str, Any]
    domain_outputs: Annotated[Dict[str, Any], merge_domain_outputs]
    synthesis_guide: Dict[str, Any]
    final_report: str
    iteration: int
    current_agent: str
    tool_call_log: Annotated[List[Dict[str, Any]], merge_lists]
    plan_version: int
    replan_reason: str
    reviewed_data: Dict[str, Any]
    review_passed: bool
    review_findings: Annotated[List[str], merge_lists]
    review_conflicts: Annotated[List[Dict[str, Any]], merge_lists]
    review_gaps: Annotated[List[str], merge_lists]


# ============================================================
# 路由函数
# ============================================================
def route_after_manager(state: AgentState) -> str:
    """Manager 节点后的路由（Mission-driven）。

    路由逻辑：
    1. 安全检查：iteration > 20 → END
    2. Mission 需二次确认 → manager_confirm
    3. 从 mission.domain_contributions 获取下一个需要执行的领域 Agent
    4. 所有领域 Agent 已执行 → intent_checkpoint
    5. 其他 → END
    """
    iteration = state.get("iteration", 0)
    if iteration > 20:
        return END

    mission = state.get("mission", {})
    if mission.get("pending_confirmation"):
        return "manager_confirm"

    # 从 Mission 的 domain_contributions 中找下一个需要执行的 Agent
    domain_contributions = mission.get("domain_contributions", {})
    domain_outputs = state.get("domain_outputs", {})

    for display_name in SUB_AGENT_NAMES:
        if display_name == FINAL_AGENT:
            continue  # Document 最后单独执行
        contrib = domain_contributions.get(display_name, {})
        if contrib.get("needed") and display_name not in domain_outputs:
            return display_name

    # 所有非 Document Agent 已执行（或被跳过），进入 Intent Checkpoint
    if "Document" not in domain_outputs and not state.get("final_report"):
        return "intent_checkpoint"

    return END


def route_after_sub_agent(state: AgentState) -> str:
    """领域 Agent 节点后的路由：检查还有哪些领域需要执行。

    - 还有未执行的领域 Agent → 返回下一个
    - 所有领域已执行 → intent_checkpoint
    - 迭代次数超限 → END（熔断）
    """
    iteration = state.get("iteration", 0)
    if iteration > 20:
        return END

    mission = state.get("mission", {})
    domain_contributions = mission.get("domain_contributions", {})
    domain_outputs = state.get("domain_outputs", {})

    for display_name in SUB_AGENT_NAMES:
        if display_name == FINAL_AGENT:
            continue
        contrib = domain_contributions.get(display_name, {})
        if contrib.get("needed") and display_name not in domain_outputs:
            return display_name

    # 所有领域 Agent 已完成，进入 Manager Assess 评估
    return "manager_assess"


def route_after_assess(state: AgentState) -> str:
    """Manager Assess 节点后的路由：决定继续执行还是进入 Intent Checkpoint。

    - plan_version > 3 → 强制进入 intent_checkpoint（防止无限 Replan）
    - 还有未执行的领域 Agent → 返回下一个
    - 所有领域已执行 → intent_checkpoint
    """
    plan_version = state.get("plan_version", 1)
    if plan_version > 3:
        return "intent_checkpoint"

    iteration = state.get("iteration", 0)
    if iteration > 20:
        return END

    mission = state.get("mission", {})
    domain_contributions = mission.get("domain_contributions", {})
    domain_outputs = state.get("domain_outputs", {})

    for display_name in SUB_AGENT_NAMES:
        if display_name == FINAL_AGENT:
            continue
        contrib = domain_contributions.get(display_name, {})
        if contrib.get("needed") and display_name not in domain_outputs:
            return display_name

    return "intent_checkpoint"


def route_after_checkpoint(state: AgentState) -> str:
    """Intent Checkpoint 后的路由：进入 Reviewer 审查。"""
    if state.get("final_report"):
        return END
    return "reviewer"


def route_after_reviewer(state: AgentState) -> str:
    """Reviewer 后的路由：通过 → Reporter，不通过 → Manager Assess (Replan)。

    - review_passed == True → Document (Reporter)
    - plan_version > 3 且未通过 → 强制 Document（不再 Replan）
    - 未通过 → manager_assess
    """
    if state.get("review_passed", False):
        return "Document"

    plan_version = state.get("plan_version", 1)
    if plan_version > 3:
        return "Document"

    return "manager_assess"


# ============================================================
# 条件边映射构建（由 registry 驱动）
# ============================================================
def _build_route_map():
    """根据 AGENT_REGISTRY 动态构建条件边路由映射表。

    路由表格式：{display_name → node_name, "intent_checkpoint" → "intent_checkpoint", ...}
    供所有条件边复用。
    """
    route_map = {}
    for display_name, info in AGENT_REGISTRY.items():
        route_map[display_name] = info["node_name"]
    route_map["intent_checkpoint"] = "intent_checkpoint"
    route_map["manager_confirm"] = "manager_confirm"
    route_map["manager_assess"] = "manager_assess"
    route_map["reviewer"] = "reviewer"
    route_map[END] = END
    return route_map


# ============================================================
# 图构建工厂函数
# ============================================================
def build_graph(agent_nodes: Dict[str, callable], **kwargs):
    """构建并编译 LangGraph 工作流（Mission-driven）。

    节点和条件边完全由 AGENT_REGISTRY 驱动。

    Args:
        agent_nodes: 节点函数字典，格式为:
            {
                "manager": manager_node_fn,
                "intent_checkpoint": intent_checkpoint_fn,
                "nutrition": nutrition_node_fn,
                "coach": coach_node_fn,
                "analyst": analyst_node_fn,
                "career": career_node_fn,
                "document": document_node_fn,
            }
            键名必须与 AGENT_REGISTRY 中各 Agent 的 node_name 一致。

    Returns:
        编译后的 LangGraph 图对象。
    """
    workflow = StateGraph(AgentState)

    # ---- Manager Assess 节点（P2：动态评估 + Replan） ----
    if "manager_assess" in agent_nodes:
        workflow.add_node("manager_assess", agent_nodes["manager_assess"])

    # ---- Manager 节点 ----
    manager_fn = agent_nodes["manager"]
    workflow.add_node("manager", manager_fn)
    workflow.add_node("manager_confirm", manager_fn)

    # ---- Intent Checkpoint 节点 ----
    if "intent_checkpoint" in agent_nodes:
        workflow.add_node("intent_checkpoint", agent_nodes["intent_checkpoint"])

    # ---- Reviewer 节点（P3：审查 → 放行或 Replan） ----
    if "reviewer" in agent_nodes:
        workflow.add_node("reviewer", agent_nodes["reviewer"])

    # ---- 子 Agent 节点（由 registry 驱动） ----
    for display_name, info in AGENT_REGISTRY.items():
        node_name = info["node_name"]
        if node_name in agent_nodes:
            workflow.add_node(node_name, agent_nodes[node_name])
        else:
            raise ValueError(
                f"Agent '{display_name}' (node_name='{node_name}') 在 registry 中已注册，"
                f"但 agent_nodes 中未提供对应的节点函数。"
            )

    # ---- 设置入口 ----
    workflow.set_entry_point("manager")

    # ---- 条件边（由 registry 动态生成映射表） ----
    route_map = _build_route_map()

    # Manager → 动态路由
    workflow.add_conditional_edges("manager", route_after_manager, route_map)
    workflow.add_conditional_edges("manager_confirm", route_after_manager, route_map)

    # 每个领域 Agent 执行后 → 路由到下一个领域 Agent 或 Manager Assess
    # Document 不在此循环中（它有独立的 EDGE → END）
    doc_node = AGENT_REGISTRY.get(FINAL_AGENT, {}).get("node_name", "document")
    for node_name in get_sub_agent_node_names():
        if node_name != doc_node:
            workflow.add_conditional_edges(node_name, route_after_sub_agent, route_map)

    # Manager Assess → 动态路由（下一 Agent 或 Intent Checkpoint）
    if "manager_assess" in agent_nodes:
        workflow.add_conditional_edges("manager_assess", route_after_assess, route_map)

    # Intent Checkpoint → Reviewer
    workflow.add_conditional_edges("intent_checkpoint", route_after_checkpoint, route_map)

    # Reviewer → Reporter (通过) 或 Manager Assess (Replan)
    if "reviewer" in agent_nodes:
        workflow.add_conditional_edges("reviewer", route_after_reviewer, route_map)

    # Document → END
    doc_node_name = AGENT_REGISTRY.get(FINAL_AGENT, {}).get("node_name", "document")
    if doc_node_name in agent_nodes:
        workflow.add_edge(doc_node_name, END)

    # ---- 编译 ----
    memory = MemorySaver()
    interrupt_before = kwargs.get("interrupt_before", [])
    return workflow.compile(checkpointer=memory, interrupt_before=interrupt_before)


# ============================================================
# 辅助函数
# ============================================================
def trim_messages_for_next_round(messages: List[Dict[str, Any]], summary: str) -> List[Dict[str, Any]]:
    """阶段性修剪消息列表：只保留用户原始意图 + 本轮完成摘要。

    丢弃所有中间推理过程（Manager规划、Agent工具调用、ReAct细节），
    防止上下文污染。下一轮追问时 LLM 只看到干净的摘要。

    Args:
        messages: 当前累积的消息列表。
        summary: 本轮完成摘要文本。

    Returns:
        修剪后的消息列表（最多2条：用户原始消息 + 摘要）。
    """
    trimmed = []
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "user":
            trimmed.append(msg)
            break  # 只保留第一条用户消息
    trimmed.append({"role": "assistant", "content": summary})
    return trimmed


def load_player_profile() -> Dict[str, Any]:
    """从 memory/player.json 加载球员档案。"""
    from config import config
    try:
        with open(config.PLAYER_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def create_initial_state(user_input: str) -> AgentState:
    """创建初始状态。

    Args:
        user_input: 用户输入的初始需求。

    Returns:
        初始化的 AgentState。
    """
    profile = load_player_profile()

    return {
        "messages": [{"role": "user", "content": user_input}],
        "player_profile": profile,
        "mission": {},
        "domain_outputs": {},
        "synthesis_guide": {},
        "final_report": "",
        "iteration": 0,
        "current_agent": "manager",
        "tool_call_log": [],
        "plan_version": 1,
        "replan_reason": "",
        "reviewed_data": {},
        "review_passed": False,
        "review_findings": [],
        "review_conflicts": [],
        "review_gaps": [],
    }
