"""
FootballAI Career Agent - LangGraph 状态定义与图构建

多智能体协作系统的核心流程控制：
- Manager 节点：规划与聚合
- 专业 Agent 节点：纵向执行
- 条件边：动态路由（基于 AGENT_REGISTRY 自动生成）
"""

import json
import time
from typing import TypedDict, List, Dict, Any, Annotated, Optional
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt

from registry import (
    AGENT_REGISTRY,
    SUB_AGENT_NAMES,
    DISPLAY_TO_NODE,
    FINAL_AGENT,
    get_sub_agent_node_names,
    get_capability_executor,
)
from loop_contracts import (
    Hypothesis,
    LoopControl,
    Plan,
    ReviewResult,
    Subtask,
    SubtaskResult,
    build_v2_state_patch,
    default_loop_control,
    normalise_loop_control,
    normalise_review_result,
)
from utils.telemetry import default_telemetry, make_node_telemetry_delta, merge_telemetry


# ============================================================
# 自定义 Reducer
# ============================================================
# 用于标记 domain_outputs 中需要删除的键
_DELETE_SENTINEL = "__DELETE_KEY__"
_RESET_SENTINEL = "__RESET_STATE__"
MAX_GRAPH_NODE_ITERATIONS = 64  # emergency fuse; LoopControl owns review-loop budgets


def merge_domain_outputs(existing: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """合并 domain_outputs：新值浅层合并到已有值，而非替换。

    LangGraph TypedDict 默认对非 Annotated 字段使用替换语义，
    导致每个子 Agent 返回 {"domain_outputs": {"Coach": ...}} 时会
    清空前一个 Agent 的输出，路由函数认为已执行的 Agent 未执行，形成死循环。

    特殊规则：值为 "__DELETE_KEY__" 的键会被从已有值中删除。
    该规则仅用于兼容旧 checkpoint；V2 Revision 不再删除 Agent 级结果。
    """
    if existing is None:
        existing = {}
    if new is None:
        return existing
    if new.get(_RESET_SENTINEL):
        return {}
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


def merge_observations(existing: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Upsert observations by subtask while preserving first-seen order."""
    existing = list(existing or [])
    if any(item.get(_RESET_SENTINEL) for item in (new or [])):
        return []
    positions = {item.get("subtask_id"): index for index, item in enumerate(existing)
                 if item.get("subtask_id")}
    for item in new or []:
        subtask_id = item.get("subtask_id")
        if subtask_id and subtask_id in positions:
            index = positions[subtask_id]
            merged = dict(existing[index])
            merged.update(item)
            existing[index] = merged
        else:
            existing.append(item)
            if subtask_id:
                positions[subtask_id] = len(existing) - 1
    return existing


def merge_subtask_results(existing: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """Upsert latest V2 results without letting an older version overwrite it."""
    if isinstance(new, dict) and new.get(_RESET_SENTINEL):
        return {}
    merged = dict(existing or {})
    for subtask_id, candidate in (new or {}).items():
        current = merged.get(subtask_id, {})
        try:
            current_version = int(current.get("source_version", 0) or 0)
            candidate_version = int(candidate.get("source_version", 0) or 0)
        except (AttributeError, TypeError, ValueError):
            current_version = 0
            candidate_version = 0
        if candidate_version >= current_version:
            merged[subtask_id] = candidate
    return merged


def _instrument_node(fn: callable, node_name: str, role: str) -> callable:
    """Run a node with bounded operational telemetry, without recording content.

    Factories may expose their BaseAgent through ``_telemetry_agent``.  This
    keeps LLM-call accounting at the shared execution boundary while preserving
    the pure state-update contract of every node.
    """
    telemetry_agent = getattr(fn, "_telemetry_agent", None)

    def instrumented(state: Dict[str, Any], wrapped=fn, agent=telemetry_agent):
        if agent is not None and hasattr(agent, "_reset_telemetry"):
            agent._reset_telemetry()
        started = time.perf_counter()
        result = wrapped(state) or {}
        elapsed_ms = (time.perf_counter() - started) * 1000
        events = []
        if agent is not None and hasattr(agent, "_consume_telemetry"):
            events = agent._consume_telemetry()
        # Some backwards-compatible nodes return the incoming state wholesale.
        # Removing its prior telemetry prevents reducer double-counting.
        result = dict(result)
        reset_telemetry = bool((result.get("telemetry") or {}).get("__RESET_TELEMETRY__"))
        result.pop("telemetry", None)
        result["telemetry"] = make_node_telemetry_delta(
            node_name=node_name, role=role, latency_ms=elapsed_ms, llm_events=events,
        )
        if reset_telemetry:
            result["telemetry"]["__RESET_TELEMETRY__"] = True
        return result

    return instrumented


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _structured_agent_observation(output: Any, evidence: Any) -> Dict[str, Any]:
    """Turn an Agent's public answer into reviewable facts, not chain-of-thought."""
    parsed = output
    if isinstance(output, str):
        candidate = output.strip()
        if candidate.startswith("```"):
            candidate = candidate.replace("```json", "", 1).replace("```", "").strip()
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            parsed = output

    if isinstance(parsed, dict):
        explicit_facts = parsed.get("facts")
        facts = _as_list(explicit_facts) if explicit_facts is not None else [
            {key: value}
            for key, value in list(parsed.items())[:12]
            if key not in {
                "status", "result", "findings", "recommendation", "recommendations",
                "evidence", "data_used", "assumptions", "uncertainties",
                "constraints_checked", "blocked_reason", "blocking_information",
            }
        ]
        findings = _as_list(parsed.get("findings", parsed.get("recommendations", [])))
        structured_result: Any = parsed
    else:
        # Keep the public answer once in ``result``.  Repeating the same text as
        # a synthetic fact and recommendation needlessly inflates Reviewer input.
        facts = []
        findings = []
        structured_result = str(parsed or "")
    return {
        "facts": facts,
        "findings": findings,
        "data_used": _as_list(evidence)[:20],
        "result": structured_result,
    }


def _executor_outcome(structured_result: Any, output: Any) -> tuple[str, str, str, List[str]]:
    """Map an executor's explicit public status onto the Subtask contracts.

    Domain Agents may legitimately report that required facts are missing.  A
    non-empty JSON payload with ``status=BLOCKED`` is therefore not a completed
    result and must reach the Reviewer/HITL path without an automatic retry.
    """
    explicit_status = ""
    blocked_reason = ""
    uncertainties: List[str] = []
    if isinstance(structured_result, dict):
        explicit_status = str(structured_result.get("status", "")).strip().upper()
        reason_value = (
            structured_result.get("blocked_reason")
            or structured_result.get("blocking_information")
            or structured_result.get("information_gap")
            or ""
        )
        if isinstance(reason_value, list):
            blocked_reason = "; ".join(str(item) for item in reason_value if str(item).strip())
        else:
            blocked_reason = str(reason_value).strip()
        uncertainties = [
            str(item) for item in _as_list(structured_result.get("uncertainties"))
            if str(item).strip()
        ]

    if explicit_status == "BLOCKED":
        reason = blocked_reason or "执行器明确报告缺少继续所需的关键输入"
        return "blocked", "BLOCKED", reason, list(dict.fromkeys(uncertainties + [reason]))
    if explicit_status in {"REVISION_REQUIRED", "NEEDS_REVISION", "REVISE", "FAILED", "ERROR"}:
        reason = blocked_reason or "执行器结果需要修订"
        return "needs_revision", "REVISION_REQUIRED", reason, list(dict.fromkeys(uncertainties + [reason]))
    if explicit_status == "SKIPPED":
        return "skipped", "SKIPPED", blocked_reason, uncertainties
    if explicit_status in {"COMPLETED", "COMPLETE", "SUCCESS", "SUCCEEDED", "PASS"}:
        return "completed", "COMPLETED", "", uncertainties

    completed = bool(output.strip()) if isinstance(output, str) else bool(output)
    if completed:
        return "completed", "COMPLETED", "", uncertainties
    return "needs_revision", "REVISION_REQUIRED", "executor_no_result", ["执行器未返回可接受结果"]


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
    user_context: Dict[str, Any]
    domain_outputs: Annotated[Dict[str, Any], merge_domain_outputs]
    synthesis_guide: Dict[str, Any]
    final_report: str
    iteration: int
    max_iterations: int
    current_agent: str
    tool_call_log: Annotated[List[Dict[str, Any]], merge_lists]
    citations: Annotated[List[Dict[str, Any]], merge_lists]
    plan_version: int
    replan_reason: str
    reviewed_data: Dict[str, Any]
    review_passed: bool
    review_findings: List[str]
    review_conflicts: List[Dict[str, Any]]
    review_gaps: List[str]
    # Looping-plan first-class state.  The legacy fields above are retained while
    # agents are incrementally migrated to consume plan/subtask data directly.
    plan: Dict[str, Any]
    hypotheses: List[Dict[str, Any]]
    observations: Annotated[List[Dict[str, Any]], merge_observations]
    review: Dict[str, Any]
    current_subtask: Optional[str]
    termination_reason: str
    final_result: str
    # V2 compatibility fields.  Existing controllers continue reading the
    # legacy fields until their state-machine migration is complete.
    plan_v2: Plan
    subtasks: List[Subtask]
    subtask_results: Annotated[Dict[str, SubtaskResult], merge_subtask_results]
    review_v2: ReviewResult
    hypotheses_v2: List[Hypothesis]
    loop_control: LoopControl
    revision_contexts: Dict[str, Dict[str, Any]]
    manager_decision: str
    telemetry: Annotated[Dict[str, Any], merge_telemetry]


# ============================================================
# 路由函数
# ============================================================
def _ready_subtask(state: AgentState) -> Optional[Dict[str, Any]]:
    """Return the highest-priority pending subtask whose dependencies passed.

    This is deliberately plan-driven: registry order is never used as an
    execution order.  It is only used later to resolve a capability to a
    concrete currently-installed executor.
    """
    plan = state.get("plan") or state.get("plan_v2") or {}
    subtasks = plan.get("subtasks", []) or state.get("subtasks", [])
    by_id = {item.get("id"): item for item in subtasks if item.get("id")}
    ready = []
    for item in subtasks:
        if str(item.get("status", "pending")).lower() != "pending":
            continue
        dependencies = item.get("depends_on", item.get("dependencies", []))
        if all(str(by_id.get(dep, {}).get("status", "")).lower() == "completed"
               for dep in dependencies):
            ready.append(item)
    if not ready:
        return None
    return sorted(ready, key=lambda item: (item.get("priority", 99), item.get("id", "")))[0]


def _route_ready_subtask(state: AgentState) -> Optional[str]:
    task = _ready_subtask(state)
    if not task:
        return None
    return get_capability_executor(task.get("capability"))


def _iteration_limit_reached(state: AgentState) -> bool:
    return state.get("iteration", 0) >= state.get("max_iterations", MAX_GRAPH_NODE_ITERATIONS)


def route_after_manager(state: AgentState) -> str:
    """Manager 节点后的路由（Mission-driven）。

    路由逻辑：
    1. 安全检查：iteration > 20 → END
    2. Mission 需二次确认 → manager_confirm
    3. 从 mission.domain_contributions 获取下一个需要执行的领域 Agent
    4. 所有领域 Agent 已执行 → intent_checkpoint
    5. 其他 → END
    """
    if _iteration_limit_reached(state):
        return "intent_checkpoint"

    mission = state.get("mission", {})
    if mission.get("pending_confirmation"):
        return "manager_confirm"

    # Plan is the source of execution order.  Mission contributions remain a
    # compatibility adapter for existing domain agents, not a workflow.
    planned_agent = _route_ready_subtask(state)
    if planned_agent:
        return planned_agent

    if (state.get("plan") or state.get("plan_v2") or {}).get("subtasks") or state.get("subtasks"):
        return "intent_checkpoint"

    # Compatibility fallback for an old persisted state without a Plan.
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
    if _iteration_limit_reached(state):
        return "intent_checkpoint"

    planned_agent = _route_ready_subtask(state)
    if planned_agent:
        return planned_agent

    if (state.get("plan") or state.get("plan_v2") or {}).get("subtasks") or state.get("subtasks"):
        return "intent_checkpoint"

    mission = state.get("mission", {})
    domain_contributions = mission.get("domain_contributions", {})
    domain_outputs = state.get("domain_outputs", {})

    for display_name in SUB_AGENT_NAMES:
        if display_name == FINAL_AGENT:
            continue
        contrib = domain_contributions.get(display_name, {})
        if contrib.get("needed") and display_name not in domain_outputs:
            return display_name

    # 所有领域 Agent 已完成，直接构造 synthesis context 后交 Reviewer。
    return "intent_checkpoint"


def route_after_assess(state: AgentState) -> str:
    """Manager Assess 节点后的路由：决定继续执行还是进入 Intent Checkpoint。

    - plan_version > 3 → 强制进入 intent_checkpoint（防止无限 Replan）
    - 还有未执行的领域 Agent → 返回下一个
    - 所有领域已执行 → intent_checkpoint
    """
    if state.get("termination_reason"):
        return "Document"

    manager_decision = state.get("manager_decision")
    if manager_decision == "BLOCKED":
        return "human_input"
    if manager_decision == "FINISH":
        return "Document"

    if _iteration_limit_reached(state):
        return "intent_checkpoint"

    planned_agent = _route_ready_subtask(state)
    if planned_agent:
        return planned_agent

    plan_version = state.get("plan_version", 1)
    if plan_version >= 3:
        return "intent_checkpoint"

    if (state.get("plan") or state.get("plan_v2") or {}).get("subtasks") or state.get("subtasks"):
        return "intent_checkpoint"

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
    """Route only on the canonical V2 decision; legacy fields are display-only."""
    review = state.get("review_v2") or state.get("review") or {}
    decision = str(review.get("decision", "")).upper()
    if decision == "PASS":
        return "Document"
    if decision == "REVISE":
        return "manager_revision"
    if decision == "BLOCKED":
        return "human_input"
    if decision == "REPLAN":
        return "manager_replan"
    # A malformed/missing decision cannot safely authorize another loop.
    return "Document"


def route_after_manager_action(state: AgentState) -> str:
    """Continue only the tasks selected by Revision/Replanning."""
    if state.get("termination_reason") or _iteration_limit_reached(state):
        return "Document"
    ready_agent = _route_ready_subtask(state)
    if ready_agent:
        return ready_agent
    return "intent_checkpoint"


def route_after_human_input(state: AgentState) -> str:
    if state.get("termination_reason"):
        return "Document"
    decision = str((state.get("review_v2") or {}).get("decision", "")).upper()
    return {
        "PASS": "Document",
        "REVISE": "manager_revision",
        "REPLAN": "manager_replan",
        "BLOCKED": "human_input",
    }.get(decision, "Document")


def human_input_node(state: AgentState) -> Dict[str, Any]:
    """Pause on BLOCKED and turn supplied facts into a scoped Revision input."""
    review = normalise_review_result(
        state.get("review_v2") or {}, state.get("subtasks", []) or [],
    )
    prompt_payload = {
        "type": "missing_user_input",
        "blocking_information": review.get("blocking_information", []),
        "affected_subtasks": list(dict.fromkeys(
            subtask_id
            for finding in review.get("findings", [])
            if finding.get("action") == "BLOCKED"
            for subtask_id in finding.get("subtask_ids", [])
        )),
    }
    supplied = interrupt(prompt_payload)
    if isinstance(supplied, dict):
        supplied_text = str(supplied.get("information") or supplied.get("content") or "").strip()
    else:
        supplied_text = str(supplied or "").strip()
    if not supplied_text:
        supplied = interrupt({**prompt_payload, "error": "补充信息不能为空"})
        if isinstance(supplied, dict):
            supplied_text = str(supplied.get("information") or supplied.get("content") or "").strip()
        else:
            supplied_text = str(supplied or "").strip()

    blocking_information = [
        str(item).strip() for item in review.get("blocking_information", [])
        if str(item).strip()
    ]
    affected_subtasks = list(dict.fromkeys(
        subtask_id
        for finding in review.get("findings", [])
        if finding.get("action") == "BLOCKED"
        for subtask_id in finding.get("subtask_ids", [])
    ))
    resolved_gap_labels = list(blocking_information)
    revised_findings = []
    for finding in review.get("findings", []):
        updated = dict(finding)
        if updated.get("action") == "BLOCKED":
            for label in (updated.get("reason"), updated.get("description")):
                if str(label or "").strip():
                    resolved_gap_labels.append(str(label).strip())
            updated["action"] = "REVISION"
            updated["severity"] = "HIGH"
            updated["evidence"] = list(updated.get("evidence", [])) + [
                {"user_supplied_information": supplied_text}
            ]
            updated["reason"] = f"用户已补充关键输入；仅修订受影响 Subtask。原原因：{updated.get('reason', '')}"
        revised_findings.append(updated)
    revised_review = normalise_review_result({
        "decision": "REVISE",
        "findings": revised_findings,
        "reviewed_subtasks": list(review.get("reviewed_subtasks", [])),
        "blocking_information": [],
        "summary": f"用户已补充阻塞信息：{supplied_text[:500]}",
    }, state.get("subtasks", []) or [])
    user_context = dict(state.get("user_context") or {})
    prior_inputs = list(user_context.get("human_inputs", []) or [])
    prior_inputs.append(supplied_text)
    user_context["human_inputs"] = prior_inputs
    mission = dict(state.get("mission") or {})
    mission_context = dict(mission.get("context") or {})
    supplied_items = list(mission_context.get("user_supplied_information", []) or [])
    supplied_items.append(supplied_text)
    mission_context["user_supplied_information"] = supplied_items
    normalised_resolved = {item.casefold() for item in resolved_gap_labels}
    prior_mission_gaps = list(mission_context.get("information_gaps", []) or [])
    mission_context["information_gaps"] = [
        item for item in prior_mission_gaps
        if str(item).strip().casefold() not in normalised_resolved
    ]
    resolved_records = list(mission_context.get("resolved_information_gaps", []) or [])
    resolved_records.append({
        "questions": list(dict.fromkeys(resolved_gap_labels)),
        "answer": supplied_text,
        "affected_subtasks": affected_subtasks,
    })
    mission_context["resolved_information_gaps"] = resolved_records
    mission["context"] = mission_context
    plan = dict(state.get("plan") or {})
    if "information_gaps" in plan or prior_mission_gaps:
        plan["information_gaps"] = [
            item for item in list(plan.get("information_gaps", prior_mission_gaps) or [])
            if str(item).strip().casefold() not in normalised_resolved
        ]
    control = normalise_loop_control(state.get("loop_control"))
    next_decision = str(revised_review.get("decision", "PASS")).upper()
    mode_map = {
        "PASS": ("FINISHED", "FINISH"),
        "REVISE": ("REVISION", "REVISION"),
        "REPLAN": ("REPLANNING", "REPLAN"),
        "BLOCKED": ("BLOCKED", "BLOCKED"),
    }
    next_mode, manager_decision = mode_map.get(next_decision, ("FINISHED", "FINISH"))
    control.update({
        "mode": next_mode,
        "last_decision": manager_decision,
        "waiting_for_user": next_decision == "BLOCKED",
        "termination_reason": "",
    })
    legacy_action_map = {
        "KEEP": "accept_with_uncertainty",
        "REVISION": "revise_subtask",
        "REPLAN": "replan",
        "BLOCKED": "request_more_evidence",
    }
    legacy_findings = [{
        "subtask_id": (item.get("subtask_ids") or [None])[0],
        "subtask_ids": item.get("subtask_ids", []),
        "type": item.get("category", "review_issue"),
        "severity": str(item.get("severity", "HIGH")).lower(),
        "description": item.get("description", ""),
        "action": legacy_action_map.get(item.get("action"), "accept_with_uncertainty"),
    } for item in revised_findings]
    legacy_status = {
        "PASS": "passed", "REVISE": "needs_revision",
        "REPLAN": "needs_revision", "BLOCKED": "failed",
    }.get(next_decision, "passed")
    v2_patch = build_v2_state_patch({
        **state,
        "mission": mission,
        "plan": plan,
        "review_v2": revised_review,
        "loop_control": control,
    })
    v2_patch["review_v2"] = revised_review
    v2_patch["loop_control"] = control
    return {
        "mission": mission,
        "plan": plan,
        "user_context": user_context,
        "messages": [{"role": "user", "content": supplied_text}],
        **v2_patch,
        "review_v2": revised_review,
        "review": {"status": legacy_status, "decision": next_decision,
                   "findings": legacy_findings, "summary": revised_review["summary"]},
        "review_gaps": [],
        "loop_control": control,
        "manager_decision": manager_decision,
    }


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
    route_map["manager_revision"] = "manager_revision"
    route_map["manager_replan"] = "manager_replan"
    route_map["human_input"] = "human_input"
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

    # ---- Manager Decision compatibility node ----
    if "manager_assess" in agent_nodes:
        workflow.add_node("manager_assess", _instrument_node(
            agent_nodes["manager_assess"], "manager_assess", "manager",
        ))

    # ---- Same Manager, explicit Revision/Replanning modes ----
    if "manager_revision" in agent_nodes:
        workflow.add_node("manager_revision", _instrument_node(
            agent_nodes["manager_revision"], "manager_revision", "manager",
        ))
    if "manager_replan" in agent_nodes:
        workflow.add_node("manager_replan", _instrument_node(
            agent_nodes["manager_replan"], "manager_replan", "manager",
        ))
    workflow.add_node("human_input", human_input_node)

    # ---- Manager 节点 ----
    manager_fn = agent_nodes["manager"]
    workflow.add_node("manager", _instrument_node(manager_fn, "manager", "manager"))
    workflow.add_node("manager_confirm", _instrument_node(manager_fn, "manager_confirm", "manager"))

    # ---- Intent Checkpoint 节点 ----
    if "intent_checkpoint" in agent_nodes:
        workflow.add_node("intent_checkpoint", _instrument_node(
            agent_nodes["intent_checkpoint"], "intent_checkpoint", "manager",
        ))

    # ---- Reviewer 节点（P3：审查 → 放行或 Replan） ----
    if "reviewer" in agent_nodes:
        reviewer_fn = agent_nodes["reviewer"]

        def reviewer_node(state, fn=reviewer_fn):
            result = fn(state)
            decision = str((result.get("review_v2") or {}).get("decision", "")).upper()
            control = normalise_loop_control(state.get("loop_control"))
            mode_map = {
                "PASS": ("FINISHED", "FINISH", False),
                "REVISE": ("REVIEW", "REVISION", False),
                "REPLAN": ("REVIEW", "REPLAN", False),
                "BLOCKED": ("BLOCKED", "BLOCKED", True),
            }
            mode, manager_decision, waiting = mode_map.get(
                decision, ("FINISHED", "FINISH", False),
            )
            review_targets = list(dict.fromkeys(
                subtask_id
                for finding in (result.get("review_v2") or {}).get("findings", [])
                if finding.get("action") == "REVISION"
                for subtask_id in finding.get("subtask_ids", [])
            )) if decision == "REVISE" else []
            control.update({
                "mode": mode,
                "last_decision": manager_decision,
                "waiting_for_user": waiting,
                "revision_targets": review_targets,
                "termination_reason": "evidence_sufficient" if decision == "PASS" else "",
            })
            plan_v2 = dict(state.get("plan_v2") or {})
            plan_v2["revision_targets"] = review_targets
            if decision == "PASS":
                plan_v2["termination_reason"] = "evidence_sufficient"
            result["plan_v2"] = plan_v2
            result["loop_control"] = control
            result["revision_contexts"] = {}
            result["manager_decision"] = manager_decision
            result["termination_reason"] = "evidence_sufficient" if decision == "PASS" else ""
            return result

        reviewer_node._telemetry_agent = getattr(reviewer_fn, "_telemetry_agent", None)
        workflow.add_node("reviewer", _instrument_node(reviewer_node, "reviewer", "reviewer"))

    # ---- 子 Agent 节点（由 registry 驱动） ----
    for display_name, info in AGENT_REGISTRY.items():
        node_name = info["node_name"]
        if node_name in agent_nodes:
            # Attach the generic runtime bookkeeping at the execution boundary.
            # Domain agents remain specialists and do not need to know about
            # the looping-plan protocol.
            def execution_node(state, fn=agent_nodes[node_name], display_name=display_name):
                task = _ready_subtask(state)
                runtime_state = dict(state)
                if task:
                    runtime_state["current_subtask"] = task.get("id")
                    runtime_plan = dict(state.get("plan") or state.get("plan_v2") or {})
                    runtime_tasks = [dict(item) for item in runtime_plan.get("subtasks", [])]
                    for item in runtime_tasks:
                        if item.get("id") == task.get("id"):
                            item["status"] = "running"
                    runtime_plan["subtasks"] = runtime_tasks
                    runtime_state["plan"] = runtime_plan

                result = fn(runtime_state) or {}
                if not task:
                    return result

                plan = dict(state.get("plan") or state.get("plan_v2") or {})
                subtasks = [dict(item) for item in plan.get("subtasks", [])]
                output = result.get("domain_outputs", {}).get(display_name)
                external_evidence = result.get("citations", []) or result.get("tool_call_log", [])
                structured_observation = _structured_agent_observation(output, external_evidence)
                parsed_result = structured_observation.get("result")
                embedded_evidence = (
                    _as_list(parsed_result.get("evidence"))
                    if isinstance(parsed_result, dict) else []
                )
                evidence = _as_list(external_evidence or embedded_evidence)
                if isinstance(parsed_result, dict) and parsed_result.get("data_used"):
                    structured_observation["data_used"] = _as_list(parsed_result.get("data_used"))[:20]
                else:
                    structured_observation["data_used"] = _as_list(evidence)[:20]
                legacy_status, result_status, blocked_reason, outcome_uncertainties = (
                    _executor_outcome(parsed_result, output)
                )
                was_revision_target = task.get("id") in set(plan.get("revision_targets", []) or [])
                previous_result = state.get("subtask_results", {}).get(task.get("id"), {})
                source_version = int(previous_result.get("source_version", 0) or 0) + 1
                for item in subtasks:
                    if item.get("id") == task.get("id"):
                        item["status"] = legacy_status
                        item["last_result_version"] = source_version
                        item["blocked_reason"] = blocked_reason
                        if result_status == "BLOCKED" and was_revision_target:
                            # Waiting for external facts is not a failed
                            # Revision attempt; keep the per-Subtask budget
                            # available for the actual evidence-backed retry.
                            item["revision_count"] = max(
                                0, int(item.get("revision_count", 0) or 0) - 1,
                            )
                        break
                plan["subtasks"] = subtasks
                plan["revision_targets"] = [
                    subtask_id for subtask_id in list(plan.get("revision_targets", []) or [])
                    if subtask_id != task.get("id")
                ]
                parsed_uncertainties = (
                    [str(item) for item in _as_list(parsed_result.get("uncertainties"))]
                    if isinstance(parsed_result, dict) else []
                )
                all_uncertainties = list(dict.fromkeys(parsed_uncertainties + outcome_uncertainties))
                observation = {
                    "subtask_id": task.get("id"),
                    "result": output if output is not None else "",
                    "evidence": evidence,
                    "facts": structured_observation["facts"],
                    "findings": structured_observation["findings"],
                    "uncertainty": all_uncertainties,
                    "confidence": None,
                    "source_version": source_version,
                }
                v2_patch = build_v2_state_patch({**state, "plan": plan})
                assumptions = _as_list(parsed_result.get("assumptions")) if isinstance(parsed_result, dict) else []
                constraints_checked = _as_list(parsed_result.get("constraints_checked")) if isinstance(parsed_result, dict) else []
                recommendation_value = (
                    parsed_result.get("recommendation", parsed_result.get("result", output))
                    if isinstance(parsed_result, dict) else output
                )
                v2_result: SubtaskResult = {
                    "subtask_id": task.get("id"),
                    "status": result_status,
                    "observation": structured_observation,
                    "recommendation": str(recommendation_value or ""),
                    "evidence": evidence,
                    "assumptions": [str(item) for item in assumptions],
                    "uncertainties": all_uncertainties,
                    "constraints_checked": [str(item) for item in constraints_checked],
                    "source_version": source_version,
                    "blocked_reason": blocked_reason,
                }
                for v2_subtask in v2_patch["subtasks"]:
                    if v2_subtask.get("id") == task.get("id"):
                        v2_subtask["observation"] = dict(v2_result["observation"])
                        v2_subtask["result"] = dict(v2_result)
                        v2_subtask["last_result_version"] = source_version
                        break
                v2_patch["plan_v2"]["subtasks"] = v2_patch["subtasks"]
                v2_patch["plan_v2"]["revision_targets"] = list(plan["revision_targets"])
                next_control = normalise_loop_control(v2_patch.get("loop_control"))
                next_control["revision_targets"] = list(plan["revision_targets"])
                v2_patch["loop_control"] = next_control
                next_iteration = result.get("iteration", state.get("iteration", 0) + 1)
                result.update({
                    "plan": plan,
                    "current_subtask": task.get("id"),
                    "current_agent": display_name,
                    "observations": [observation],
                    **v2_patch,
                    "subtask_results": {task.get("id"): v2_result},
                    "iteration": next_iteration,
                    "termination_reason": "max_iterations" if next_iteration >= state.get("max_iterations", MAX_GRAPH_NODE_ITERATIONS) else "",
                })
                return result

            execution_node._telemetry_agent = getattr(agent_nodes[node_name], "_telemetry_agent", None)
            workflow.add_node(node_name, _instrument_node(execution_node, node_name, "agent"))
        else:
            raise ValueError(
                f"Agent '{display_name}' (node_name='{node_name}') 在 registry 中已注册，"
                f"但 agent_nodes 中未提供对应的节点函数。"
            )

    # ---- 设置入口 ----
    workflow.set_entry_point("manager")

    # ---- 条件边（由 registry 动态生成映射表） ----
    route_map = _build_route_map()
    # Optional controller nodes are useful for focused regression graphs too.
    # LangGraph validates every declared branch target during compilation, even
    # if a route will not be reached by this particular graph instance.
    for optional_node in ("manager_assess", "manager_revision", "manager_replan", "reviewer"):
        if optional_node not in agent_nodes:
            route_map.pop(optional_node, None)

    # Manager → 动态路由
    workflow.add_conditional_edges("manager", route_after_manager, route_map)
    workflow.add_conditional_edges("manager_confirm", route_after_manager, route_map)

    # 每个领域 Agent 执行后 → 下一个 Subtask；全部完成后直接 Reviewer 链路
    # Document 不在此循环中（它有独立的 EDGE → END）
    doc_node = AGENT_REGISTRY.get(FINAL_AGENT, {}).get("node_name", "document")
    for node_name in get_sub_agent_node_names():
        if node_name != doc_node:
            workflow.add_conditional_edges(node_name, route_after_sub_agent, route_map)

    # Manager Assess → 动态路由（下一 Agent 或 Intent Checkpoint）
    if "manager_assess" in agent_nodes:
        workflow.add_conditional_edges("manager_assess", route_after_assess, route_map)

    if "manager_revision" in agent_nodes:
        workflow.add_conditional_edges("manager_revision", route_after_manager_action, route_map)
    if "manager_replan" in agent_nodes:
        workflow.add_conditional_edges("manager_replan", route_after_manager_action, route_map)
    workflow.add_conditional_edges("human_input", route_after_human_input, route_map)

    # Intent Checkpoint → Reviewer
    workflow.add_conditional_edges("intent_checkpoint", route_after_checkpoint, route_map)

    # Reviewer → four semantically distinct V2 paths.
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
        "user_context": {"request": user_input, "player_profile": profile},
        "mission": {},
        "domain_outputs": {},
        "synthesis_guide": {},
        "final_report": "",
        "iteration": 0,
        "max_iterations": MAX_GRAPH_NODE_ITERATIONS,
        "current_agent": "manager",
        "tool_call_log": [],
        "citations": [],
        "plan_version": 1,
        "replan_reason": "",
        "reviewed_data": {},
        "review_passed": False,
        "review_findings": [],
        "review_conflicts": [],
        "review_gaps": [],
        "plan": {},
        "hypotheses": [],
        "observations": [],
        "review": {},
        "current_subtask": None,
        "termination_reason": "",
        "final_result": "",
        "plan_v2": {},
        "subtasks": [],
        "subtask_results": {},
        "review_v2": {},
        "hypotheses_v2": [],
        "loop_control": default_loop_control(),
        "revision_contexts": {},
        "manager_decision": "KEEP",
        "telemetry": default_telemetry(),
    }
