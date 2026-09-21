"""
FootballAI Career Agent - Manager Agent (Mission Creator + Intent Holder)

足球俱乐部总经理，负责：
1. Mission Creation — 分析用户意图，创建贯穿整个 Workflow 的 Mission 对象
2. Intent Guardianship — 在关键节点校验执行方向（Intent Checkpoint）
3. Confirmation — 低置信度时的二次确认

核心设计变化（v2）：
- 从 "生成 execution_plan + 退出" 变为 "创建 Mission + 持续守护"
- 从 Task-centric 变为 Mission-centric
"""

import json
import uuid
from typing import List, Dict, Any
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from agents.base import BaseAgent
from prompts.agent_prompts import MANAGER_PROMPT
from registry import (
    AGENT_REGISTRY,
    SUB_AGENT_NAMES,
    FINAL_AGENT,
    get_available_capabilities,
    get_capability_executor,
)
from utils.helpers import describe_player_attributes
from graph import _RESET_SENTINEL
from loop_contracts import (
    build_v2_state_patch,
    normalise_loop_control,
    normalise_review_result,
)


MAX_PLAN_SUBTASKS = 8

MANAGER_REVISION_RULES = """1. 默认保留 Reviewer 未指认有问题的 Subtask。
2. 不得因为一个 Subtask 的问题而重建整个 Plan。
3. 只有 Finding 明确列出其他 Subtask 时，才扩大 revision scope。
4. 不得删除未受影响的已有有效结果。
5. Revision 只修复 Finding，不重新生成一份全新的答案。"""

VALID_MODES = {name: set(info.get("capabilities", [])) for name, info in AGENT_REGISTRY.items()}
VALID_MODES[FINAL_AGENT] = {"comprehensive_report", "pr_statement", "commercial_advisory", "media_response"}

MODE_TO_AGENT = {mode: get_capability_executor(mode) for mode in get_available_capabilities()}

OUTPUT_TYPE_MODE_MAP = {
    "report": "comprehensive_report",
    "statement": "pr_statement",
    "advisory": "commercial_advisory",
    "response": "media_response",
    "plan": None,  # 由具体 Agent 决定
    "analysis": None,
}


class ManagerAgent(BaseAgent):
    """总经理 Agent — Mission Creator + Intent Holder。"""

    def __init__(self, llm: BaseChatModel):
        super().__init__(llm=llm)
        self._execution_phase = "planning"

    @property
    def name(self) -> str:
        return "manager"

    @property
    def role(self) -> str:
        return "足球俱乐部总经理（General Manager）"

    @property
    def system_prompt(self) -> str:
        return MANAGER_PROMPT

    # ================================================================
    # 主入口
    # ================================================================
    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Manager 核心执行逻辑（作为 LangGraph 节点）。"""
        messages = state.get("messages", [])
        player_profile = state.get("player_profile", {})

        # ---- Phase: Confirmation ----
        if self._execution_phase == "confirming":
            return self._handle_confirmation(state)

        # ---- Phase: Mission Creation ----
        if self._execution_phase == "planning":
            user_input = self._extract_last_user_message(messages)
            mission = self._create_mission(user_input, player_profile)
            hypotheses = self._create_hypotheses(user_input, mission)
            plan = self._create_plan(mission, hypotheses)
            v2_patch = build_v2_state_patch({
                "mission": mission,
                "plan": plan,
                "hypotheses": hypotheses,
                "review": {},
            })

            result = {
                "mission": mission,
                "plan": plan,
                "hypotheses": hypotheses,
                "observations": [],
                "review": {},
                "current_subtask": None,
                "termination_reason": "",
                **v2_patch,
                "domain_outputs": {},
                "current_agent": "manager",
                "messages": [{
                    "role": "assistant",
                    "content": (
                        f"[Manager] {mission.get('intent_summary', '')}\n"
                        f"[置信度] {mission.get('confidence', '?')}/10\n"
                        f"[核心目标] {mission.get('primary_goal', '')}\n"
                        f"[产出类型] {mission.get('output_type', '')}"
                    ),
                }],
            }

            return result

        return {"current_agent": "manager"}

    # ================================================================
    # Mission Creation（替代原 _analyze_intent_and_plan）
    # ================================================================
    def _create_mission(
        self, user_input: str, player_profile: Dict[str, Any]
    ) -> Dict[str, Any]:
        """核心：使用 LLM 分析意图并创建 Mission 对象。"""
        mission_prompt = f"""你是一名足球俱乐部总经理。分析球员需求并创建 Mission 对象。

## 球员档案
{json.dumps(player_profile, ensure_ascii=False, indent=2) if player_profile else "暂无球员数据"}

## 球员能力概览
{describe_player_attributes(player_profile.get('attributes', {}), player_profile.get('other_features', {})) if player_profile else "暂无"}

## 用户需求
{user_input}

## Mission 输出格式（严格JSON）

```json
{{
  "intent_summary": "用户意图的一句话概括",
  "objective": "用户真正要达成的业务目标",
  "primary_goal": "本次 Mission 的核心目标（简洁明确，所有 Agent 以此为准）",
  "constraints": ["必须遵守的限制"],
  "context": {{"关键球员或比赛背景": "..."}},
  "required_deliverable": "最终交付物名称",
  "output_type": "report / statement / advisory / response / plan / analysis",
  "audience": "球员本人 / 媒体与公众 / 俱乐部管理层 / 商业伙伴 / 综合",
  "tone": "正式权威 / 专业咨询 / 亲和真诚 / 坚定克制 / 综合",
  "success_criteria": ["成功标准1", "成功标准2"],
  "confidence": 8
}}
```

## 决策规则
1. Mission 只描述为什么做、约束与成功标准，不包含 Agent、capability 或执行顺序。
2. 不得臆造用户未提供的事实；未知内容留在 context 的 information_gaps 中。
3. output_type 只描述最终交付形式，不代表执行方案。

请只输出JSON，不要有任何其他文本。"""

        try:
            response = self._invoke_llm([
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=mission_prompt),
            ])

            content = response.content
            if isinstance(content, str):
                # 处理 LLM 返回中的代理字符（surrogates）
                content = content.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")
            content = content.strip()
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            mission = json.loads(content)

            # 规范化
            mission["confidence"] = int(mission.get("confidence", 7))
            mission.setdefault("mission_id", str(uuid.uuid4())[:8])
            mission.setdefault("intent_summary", user_input[:100])
            mission.setdefault("objective", mission.get("primary_goal", user_input))
            mission.setdefault("primary_goal", user_input)
            mission.setdefault("constraints", mission.get("global_constraints", []))
            mission.setdefault("context", {})
            mission.setdefault("required_deliverable", mission.get("output_type", "report"))
            mission.setdefault("audience", "综合")
            mission.setdefault("tone", "专业咨询")
            mission.setdefault("success_criteria", ["完成最终产出", "产出符合受众需求"])
            mission["pending_confirmation"] = False
            # Compatibility alias; constraints remains the source of truth.
            mission["global_constraints"] = list(mission.get("constraints", []))

            return mission

        except (json.JSONDecodeError, Exception) as e:
            try:
                print(f"[Manager] Mission 创建失败，使用保守策略: {type(e).__name__}")
            except Exception:
                pass
            return self._fallback_mission(user_input)

    def _create_hypotheses(self, user_input: str, mission: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Create candidate explanations only when the request is uncertain.

        Hypotheses are intentionally lightweight structured beliefs, rather
        than a Bayesian model.  They give later observations somewhere stable
        to attach supporting or contradicting evidence.
        """
        prompt = f"""根据用户请求判断是否存在需要调查的原因或不确定性。
用户请求：{user_input}
Mission：{json.dumps({'objective': mission.get('objective'), 'constraints': mission.get('constraints', [])}, ensure_ascii=False)}

若这是明确的生成/整理任务，输出 []。若需要诊断或比较，输出不超过 4 个候选假设。
只输出 JSON 数组，每项严格包含：id、statement、confidence(0到1)、status(open)、supporting_evidence([])、contradicting_evidence([])。"""
        try:
            response = self._invoke_llm([SystemMessage(content=self.system_prompt), HumanMessage(content=prompt)], "manager_hypotheses")
            raw = response.content.strip()
            if "```" in raw:
                raw = raw.split("```", 2)[1].replace("json", "", 1).strip()
            hypotheses = json.loads(raw)
            if not isinstance(hypotheses, list):
                raise ValueError("hypotheses must be a list")
            normalized = []
            for index, item in enumerate(hypotheses[:4], 1):
                if not isinstance(item, dict) or not item.get("statement"):
                    continue
                normalized.append({
                    "id": str(item.get("id", f"h{index}")),
                    "statement": str(item["statement"]),
                    "confidence": max(0.0, min(1.0, float(item.get("confidence", 0.5)))),
                    "status": item.get("status") if item.get("status") in {"open", "supported", "weakened", "rejected"} else "open",
                    "supporting_evidence": list(item.get("supporting_evidence", [])),
                    "contradicting_evidence": list(item.get("contradicting_evidence", [])),
                    "created_in_plan_version": 1,
                    "updated_in_plan_version": 1,
                })
            return normalized
        except Exception:
            return []

    def _create_plan(self, mission: Dict[str, Any], hypotheses: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Ask the Manager LLM for a problem-oriented, executable Plan."""
        prompt = f"""你是 Manager。为 Mission 动态生成当前最小执行计划，不要生成固定 Agent 流程。
Mission: {json.dumps(mission, ensure_ascii=False)}
Hypotheses: {json.dumps(hypotheses, ensure_ascii=False)}

可用 capability 仅限 {json.dumps(get_available_capabilities(), ensure_ascii=False)}。
只安排为解决当前目标或验证假设所必需的工作。Subtask 的 goal/purpose 必须描述问题，不得写“调用某 Agent”。允许并行任务；有依赖时，depends_on 只能引用前面 subtask id。
只输出 JSON：{{"plan_id":"...","version":1,"objective":"...","hypotheses":["h1"],"information_gaps":[],"subtasks":[{{"id":"subtask_01","goal":"...","purpose":"...","capability":"...","priority":1,"depends_on":[],"status":"pending"}}],"dependencies":[],"constraints":[],"termination_conditions":["goal_satisfied","no_meaningful_improvement","max_total_loops","blocked"]}}"""
        try:
            response = self._invoke_llm([SystemMessage(content=self.system_prompt), HumanMessage(content=prompt)], "manager_plan")
            raw = response.content.strip()
            if "```json" in raw:
                raw = raw.split("```json", 1)[1].split("```", 1)[0].strip()
            elif "```" in raw:
                raw = raw.split("```", 2)[1].strip()
            plan = json.loads(raw)
            normalised = self._normalise_plan(plan, mission, hypotheses)
            normalised["version"] = 1
            return normalised
        except Exception:
            # Do not guess an intent->agent workflow when planning is unavailable.
            # The synthesis node can still produce a transparent best-effort answer.
            return self._normalise_plan({"subtasks": []}, mission, hypotheses)

    @staticmethod
    def _normalise_plan(plan: Dict[str, Any], mission: Dict[str, Any], hypotheses: List[Dict[str, Any]]) -> Dict[str, Any]:
        valid_capabilities = set(MODE_TO_AGENT)
        clean = []
        seen_ids = set()
        prior_ids = set()
        manager_assignable_statuses = {"pending", "blocked", "skipped"}
        raw_subtasks = plan.get("subtasks", [])
        if not isinstance(raw_subtasks, list):
            raw_subtasks = []
        for index, task in enumerate(raw_subtasks[:MAX_PLAN_SUBTASKS], 1):
            if not isinstance(task, dict) or task.get("capability") not in valid_capabilities:
                continue
            capability = task["capability"]
            task_id = str(task.get("id") or f"subtask_{index:02d}")
            if task_id in seen_ids:
                task_id = f"subtask_{index:02d}"
            seen_ids.add(task_id)
            status = str(task.get("status", "pending")).lower()
            try:
                priority = int(task.get("priority", index))
            except (TypeError, ValueError):
                priority = index
            try:
                revision_count = max(0, int(task.get("revision_count", 0) or 0))
            except (TypeError, ValueError):
                revision_count = 0
            try:
                result_version = max(0, int(task.get("last_result_version", 0) or 0))
            except (TypeError, ValueError):
                result_version = 0
            clean.append({"id": task_id, "goal": str(task.get("goal") or task.get("objective") or mission.get("objective", "")),
                          "purpose": str(task.get("purpose", "减少当前信息缺口")), "capability": capability,
                          "assigned_agent": MODE_TO_AGENT.get(capability, ""), "priority": priority,
                          "depends_on": [dep for dep in task.get("depends_on", task.get("dependencies", [])) if dep in prior_ids],
                          # A planning model cannot claim execution completed;
                          # unchanged completed work is restored explicitly in run_replan.
                          "status": status if status in manager_assignable_statuses else "pending",
                          "revision_count": revision_count,
                          "last_result_version": result_version,
                          "blocked_reason": str(task.get("blocked_reason", ""))})
            prior_ids.add(task_id)
        try:
            plan_version = max(1, int(plan.get("version", 1) or 1))
        except (TypeError, ValueError):
            plan_version = 1
        if "information_gaps" in plan:
            information_gaps = list(plan.get("information_gaps") or [])
        else:
            information_gaps = list(mission.get("context", {}).get("information_gaps", []) or [])
        return {"plan_id": str(plan.get("plan_id", uuid.uuid4()))[:8], "version": plan_version,
                "objective": plan.get("objective", mission.get("objective", mission.get("primary_goal", ""))),
                "hypotheses": [item["id"] for item in hypotheses], "subtasks": clean,
                "dependencies": [{"subtask_id": item["id"], "depends_on": item["depends_on"]}
                                 for item in clean if item["depends_on"]],
                "constraints": mission.get("constraints", mission.get("global_constraints", [])),
                "information_gaps": information_gaps,
                "revision_targets": [],
                "termination_reason": "",
                "termination_conditions": plan.get("termination_conditions", [
                    "goal_satisfied", "no_meaningful_improvement", "max_total_loops", "blocked",
                ])}

    def _update_hypotheses_from_observations(self, state: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Update beliefs after execution; retain prior beliefs on model failure."""
        hypotheses = state.get("hypotheses", [])
        if not hypotheses or not state.get("observations"):
            return hypotheses
        compact_observations = [{
            "subtask_id": item.get("subtask_id"),
            "result": str(item.get("result", ""))[:1200],
            "evidence": item.get("evidence", [])[:5],
            "findings": item.get("findings", []),
            "uncertainty": item.get("uncertainty", []),
        } for item in state.get("observations", [])[-6:]]
        prompt = f"""依据新的 Observation 更新候选 Hypothesis，不要创造新假设。
Hypotheses: {json.dumps(hypotheses, ensure_ascii=False)}
Observations: {json.dumps(compact_observations, ensure_ascii=False, default=str)}
返回 JSON 数组。保留每项 id、statement、confidence(0到1)、status(open/supported/weakened/rejected)、supporting_evidence、contradicting_evidence。"""
        try:
            response = self._invoke_llm([SystemMessage(content=self.system_prompt), HumanMessage(content=prompt)], "manager_hypothesis_update")
            updated = json.loads(response.content.strip().replace("```json", "").replace("```", ""))
            by_id = {str(item.get("id")): item for item in updated if isinstance(item, dict)}
            merged = []
            plan_version = int(state.get("plan", {}).get("version", state.get("plan_version", 1)) or 1)
            for old in hypotheses:
                candidate = by_id.get(str(old.get("id")), old)
                candidate_status = str(candidate.get("status", old.get("status", "open"))).lower()
                if candidate_status not in {"open", "supported", "weakened", "rejected"}:
                    candidate_status = str(old.get("status", "open")).lower()
                next_values = {
                    "confidence": max(0.0, min(1.0, float(candidate.get("confidence", old.get("confidence", .5))))),
                    "status": candidate_status,
                    "supporting_evidence": list(candidate.get("supporting_evidence", old.get("supporting_evidence", []))),
                    "contradicting_evidence": list(candidate.get("contradicting_evidence", old.get("contradicting_evidence", []))),
                }
                changed = any(old.get(key) != value for key, value in next_values.items())
                merged.append({
                    **old,
                    **next_values,
                    "created_in_plan_version": int(old.get("created_in_plan_version", 1) or 1),
                    "updated_in_plan_version": plan_version if changed else int(old.get("updated_in_plan_version", 1) or 1),
                })
            return merged
        except Exception:
            return hypotheses

    @staticmethod
    def _compact_result(value: Any) -> Any:
        """Keep revision context reviewable without replaying the full history."""
        if not isinstance(value, dict):
            return str(value)[:2500]
        compact = dict(value)
        if "recommendation" in compact:
            compact["recommendation"] = str(compact["recommendation"])[:2500]
        if isinstance(compact.get("evidence"), list):
            compact["evidence"] = compact["evidence"][:5]
        return compact

    def _normalised_review(self, state: Dict[str, Any]) -> Dict[str, Any]:
        patch = build_v2_state_patch(state)
        raw = state.get("review_v2") or state.get("review") or {}
        return normalise_review_result(raw, patch.get("subtasks", []))

    def run_revision(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Manager REVISION mode: reopen only explicitly affected Subtasks."""
        plan = dict(state.get("plan") or state.get("plan_v2") or {})
        subtasks = [dict(item) for item in plan.get("subtasks", [])]
        known_ids = {item.get("id") for item in subtasks}
        review = self._normalised_review(state)
        targets = list(dict.fromkeys(
            subtask_id
            for finding in review.get("findings", [])
            if finding.get("action") == "REVISION"
            for subtask_id in finding.get("subtask_ids", [])
            if subtask_id in known_ids
        ))
        loop_control = normalise_loop_control(state.get("loop_control"))
        budget = loop_control["budget"]
        warnings = list(loop_control.get("warnings", []))

        if loop_control["total_iterations"] >= budget["max_total_iterations"]:
            warnings.append("已达到总 Loop 预算，保留现有有效结果并停止修订")
            return self._finish_loop(state, loop_control, warnings, "max_total_loops")

        active_targets = []
        revision_contexts: Dict[str, Dict[str, Any]] = {}
        result_by_id = state.get("subtask_results", {}) or {}
        task_by_id = {item.get("id"): item for item in subtasks}
        findings_by_id: Dict[str, List[Dict[str, Any]]] = {}
        for finding in review.get("findings", []):
            if finding.get("action") != "REVISION":
                continue
            for subtask_id in finding.get("subtask_ids", []):
                findings_by_id.setdefault(subtask_id, []).append(dict(finding))

        for task in subtasks:
            task_id = task.get("id")
            if task_id not in targets:
                continue
            if not MODE_TO_AGENT.get(task.get("capability")):
                warnings.append(f"{task_id} 没有可用 capability executor，停止该目标")
                continue
            unresolved_dependencies = [
                dependency_id for dependency_id in task.get("depends_on", task.get("dependencies", []))
                if str(task_by_id.get(dependency_id, {}).get("status", "")).lower() != "completed"
                and dependency_id not in targets
            ]
            if unresolved_dependencies:
                warnings.append(
                    f"{task_id} 的依赖尚未完成且未被 Finding 纳入 scope: "
                    + ", ".join(unresolved_dependencies)
                )
                continue
            try:
                revision_count = max(0, int(task.get("revision_count", 0) or 0))
            except (TypeError, ValueError):
                revision_count = 0
            if revision_count >= budget["max_revisions"]:
                warnings.append(f"{task_id} 已达到单 Subtask Revision 预算")
                continue
            task["status"] = "pending"
            task["revision_count"] = revision_count + 1
            task["blocked_reason"] = ""
            active_targets.append(task_id)
            dependency_results = {
                dependency_id: self._compact_result(result_by_id.get(dependency_id, {}))
                for dependency_id in task.get("depends_on", task.get("dependencies", []))
                if dependency_id in result_by_id
            }
            revision_contexts[task_id] = {
                "mission_summary": {
                    "objective": state.get("mission", {}).get("objective")
                    or state.get("mission", {}).get("primary_goal", ""),
                    "constraints": list(state.get("mission", {}).get("constraints", []) or []),
                },
                "target_subtask": dict(task_by_id.get(task_id, task)),
                "relevant_dependencies": dependency_results,
                "reviewer_findings": findings_by_id.get(task_id, []),
                "previous_result": self._compact_result(result_by_id.get(task_id, {})),
                "revision_rules": MANAGER_REVISION_RULES,
            }

        if not active_targets:
            reason = "revision_budget_exhausted" if targets else "no_revision_targets"
            if not targets:
                warnings.append("Reviewer 未给出可执行的 Subtask revision scope")
            return self._finish_loop(state, loop_control, warnings, reason)

        updated_plan = dict(plan)
        updated_plan["subtasks"] = subtasks
        updated_plan["revision_targets"] = active_targets
        updated_plan["termination_reason"] = ""
        loop_control.update({
            "mode": "REVISION",
            "total_iterations": loop_control["total_iterations"] + 1,
            "revision_targets": active_targets,
            "last_decision": "REVISION",
            "waiting_for_user": False,
            "warnings": warnings,
            "termination_reason": "",
        })
        v2_patch = build_v2_state_patch({**state, "plan": updated_plan, "review_v2": review,
                                         "loop_control": loop_control})
        v2_patch["plan_v2"]["revision_targets"] = active_targets
        v2_patch["loop_control"] = loop_control
        return {
            "plan": updated_plan,
            "review_v2": review,
            "revision_contexts": revision_contexts,
            "manager_decision": "REVISION",
            "current_subtask": None,
            "termination_reason": "",
            **v2_patch,
            "iteration": state.get("iteration", 0) + 1,
            "messages": [{"role": "assistant", "content":
                          f"[Manager Revision] 仅修订: {', '.join(active_targets)}"}],
        }

    def _request_replan(self, state: Dict[str, Any], hypotheses: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Ask for a genuinely new plan only after a core REPLAN finding."""
        compact_observations = [{
            "subtask_id": item.get("subtask_id"),
            "result": str(item.get("result", ""))[:1200],
            "findings": item.get("findings", [])[:3],
            "uncertainty": item.get("uncertainty", [])[:3],
        } for item in state.get("observations", [])[-6:]]
        prompt = f"""你是同一个 Manager 的 REPLANNING mode。Reviewer 已判定核心假设、共享基础假设或 Mission 解释发生变化。

Mission 摘要: {json.dumps({"objective": state.get('mission', {}).get('objective'), "constraints": state.get('mission', {}).get('constraints', []), "information_gaps": state.get('mission', {}).get('context', {}).get('information_gaps', [])}, ensure_ascii=False)}
旧 Plan: {json.dumps(state.get('plan', {}), ensure_ascii=False)}
当前 Hypotheses: {json.dumps(hypotheses, ensure_ascii=False)}
Reviewer: {json.dumps(state.get('review_v2', {}), ensure_ascii=False)}
相关 Observations: {json.dumps(compact_observations, ensure_ascii=False, default=str)}

必须说明：哪个 Hypothesis / 共享假设 / Mission 解释为何改变，以及为何局部 Revision 不足。保留仍与新问题一致的 completed Subtask；不得为了“更完整”而扩张范围。
可用 capability 仅限 {json.dumps(get_available_capabilities(), ensure_ascii=False)}。
若 Mission 解释确实变化，mission_changed=true 且 mission_change_reason 必须给出证据，并仅通过 mission_patch 更新 objective、constraints、context、success_criteria、required_deliverable；用户原始 primary_goal 不可改。否则 mission_patch 必须为空对象。
只输出 JSON：{{"reason":"核心变化及证据","mission_changed":false,"mission_change_reason":"无或具体原因","mission_patch":{{}},"hypotheses":[{{"id":"h1","statement":"...","confidence":0.5,"status":"open|supported|weakened|rejected","supporting_evidence":[],"contradicting_evidence":[]}}],"plan":{{"objective":"...","information_gaps":[],"subtasks":[{{"id":"subtask_01","goal":"...","purpose":"...","capability":"...","priority":1,"depends_on":[],"status":"pending"}}]}}}}"""
        try:
            response = self._invoke_llm([SystemMessage(content=self.system_prompt), HumanMessage(content=prompt)], "manager_replan")
            raw = response.content.strip()
            if "```json" in raw:
                raw = raw.split("```json", 1)[1].split("```", 1)[0].strip()
            elif "```" in raw:
                raw = raw.split("```", 2)[1].strip()
            result = json.loads(raw)
            return result if isinstance(result, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _apply_mission_reinterpretation(
        mission: Dict[str, Any], decision: Dict[str, Any],
    ) -> tuple[Dict[str, Any], bool]:
        """Apply only an evidenced, whitelisted Mission reinterpretation.

        ``primary_goal`` is the user's original north star and is deliberately
        immutable.  The model's boolean declaration alone never counts as a
        change, so it cannot bypass the no-progress fuse.
        """
        original = dict(mission or {})
        updated = dict(original)
        patch = decision.get("mission_patch")
        reason = str(decision.get("mission_change_reason", "")).strip()
        if decision.get("mission_changed") is not True or not reason or not isinstance(patch, dict):
            return updated, False

        for field in ("objective", "required_deliverable"):
            value = patch.get(field)
            if isinstance(value, str) and value.strip():
                updated[field] = value.strip()
        for field in ("constraints", "success_criteria"):
            if field in patch and isinstance(patch.get(field), list):
                updated[field] = [str(item) for item in patch[field][:20]]
        if isinstance(patch.get("context"), dict):
            context = dict(updated.get("context") or {})
            for key, value in list(patch["context"].items())[:20]:
                context[str(key)] = value
            updated["context"] = context
        changed = any(
            updated.get(field) != original.get(field)
            for field in (
                "objective", "constraints", "context",
                "success_criteria", "required_deliverable",
            )
        )
        if not changed:
            return original, False
        updated["global_constraints"] = list(updated.get("constraints", []) or [])
        return updated, True

    @staticmethod
    def _normalise_replan_hypotheses(raw: Any, existing: List[Dict[str, Any]],
                                     plan_version: int) -> List[Dict[str, Any]]:
        candidates = raw if isinstance(raw, list) else existing
        result = []
        existing_by_id = {str(item.get("id")): item for item in existing}
        for index, item in enumerate(candidates[:4], 1):
            if not isinstance(item, dict) or not item.get("statement"):
                continue
            item_id = str(item.get("id", f"h{index}"))
            previous = existing_by_id.get(item_id, {})
            status = str(item.get("status", previous.get("status", "open"))).lower()
            if status not in {"open", "supported", "weakened", "rejected"}:
                status = "open"
            try:
                confidence = max(0.0, min(1.0, float(item.get("confidence", previous.get("confidence", .5)))))
            except (TypeError, ValueError):
                confidence = .5
            result.append({
                "id": item_id,
                "statement": str(item["statement"]),
                "confidence": confidence,
                "status": status,
                "supporting_evidence": list(item.get("supporting_evidence", previous.get("supporting_evidence", [])) or []),
                "contradicting_evidence": list(item.get("contradicting_evidence", previous.get("contradicting_evidence", [])) or []),
                "created_in_plan_version": int(previous.get("created_in_plan_version", plan_version) or plan_version),
                "updated_in_plan_version": plan_version,
            })
        return result

    def run_replan(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Manager REPLANNING mode: rebuild only after a validated core change."""
        loop_control = normalise_loop_control(state.get("loop_control"))
        budget = loop_control["budget"]
        warnings = list(loop_control.get("warnings", []))
        if loop_control["total_iterations"] >= budget["max_total_iterations"]:
            warnings.append("已达到总 Loop 预算，拒绝扩大计划")
            return self._finish_loop(state, loop_control, warnings, "max_total_loops")
        if loop_control["replan_count"] >= budget["max_replans"]:
            warnings.append("已达到 Mission Replan 预算，保留当前最佳结果")
            return self._finish_loop(state, loop_control, warnings, "replan_budget_exhausted")

        review = self._normalised_review(state)
        core_findings = [item for item in review.get("findings", [])
                         if item.get("action") == "REPLAN"
                         and item.get("severity") in {"HIGH", "CRITICAL"}]
        if not core_findings:
            if any(item.get("action") == "REVISION" for item in review.get("findings", [])):
                downgraded = dict(review)
                downgraded["decision"] = "REVISE"
                return self.run_revision({**state, "review_v2": downgraded})
            warnings.append("Replan 缺少核心 Hypothesis / Mission 变化证据")
            return self._finish_loop(state, loop_control, warnings, "replan_not_justified")

        # Replanning prompt updates hypotheses and Plan together, avoiding a
        # second Manager LLM call over the same observations.
        hypotheses = list(state.get("hypotheses") or state.get("hypotheses_v2") or [])
        old_plan = state.get("plan") or state.get("plan_v2") or {}
        decision = self._request_replan(
            {**state, "plan": old_plan, "review_v2": review}, hypotheses,
        )
        raw_plan = decision.get("plan") if isinstance(decision.get("plan"), dict) else {}
        if not raw_plan or not str(decision.get("reason", "")).strip():
            warnings.append("Manager 未形成有证据的新 Plan，停止无改善循环")
            return self._finish_loop(state, loop_control, warnings, "replanning_no_longer_improves")

        new_version = int(old_plan.get("version", state.get("plan_version", 1)) or 1) + 1
        updated_mission, mission_changed = self._apply_mission_reinterpretation(
            state.get("mission", {}) or {}, decision,
        )
        if decision.get("mission_changed") is True and not mission_changed:
            warnings.append("声明的 Mission 变化没有有效且有证据的 mission_patch，已忽略")
        updated_hypotheses = self._normalise_replan_hypotheses(
            decision.get("hypotheses"), hypotheses, new_version,
        )
        new_plan = self._normalise_plan(raw_plan, updated_mission, updated_hypotheses)
        new_plan["version"] = new_version
        old_task_shape = [(
            item.get("id"), item.get("goal") or item.get("objective"), item.get("purpose"),
            item.get("capability"), tuple(item.get("depends_on", item.get("dependencies", []))),
        ) for item in old_plan.get("subtasks", [])]
        new_task_shape = [(
            item.get("id"), item.get("goal") or item.get("objective"), item.get("purpose"),
            item.get("capability"), tuple(item.get("depends_on", item.get("dependencies", []))),
        ) for item in new_plan.get("subtasks", [])]
        old_gaps = (
            old_plan.get("information_gaps")
            if "information_gaps" in old_plan
            else state.get("mission", {}).get("context", {}).get("information_gaps", [])
        )
        old_constraints = (
            old_plan.get("constraints")
            if "constraints" in old_plan
            else state.get("mission", {}).get("constraints", [])
        )
        old_plan_shape = (
            old_plan.get("objective", state.get("mission", {}).get("objective")),
            tuple(map(str, old_gaps or [])),
            tuple(map(str, old_constraints or [])),
            tuple(old_task_shape),
        )
        new_plan_shape = (
            new_plan.get("objective"), tuple(map(str, new_plan.get("information_gaps", []) or [])),
            tuple(map(str, new_plan.get("constraints", []) or [])), tuple(new_task_shape),
        )
        old_hypothesis_shape = [(
            item.get("id"), item.get("statement"), item.get("confidence"),
            str(item.get("status", "")).lower(),
            tuple(map(str, item.get("supporting_evidence", []) or [])),
            tuple(map(str, item.get("contradicting_evidence", []) or [])),
        ) for item in state.get("hypotheses", [])]
        new_hypothesis_shape = [(
            item.get("id"), item.get("statement"), item.get("confidence"),
            str(item.get("status", "")).lower(),
            tuple(map(str, item.get("supporting_evidence", []) or [])),
            tuple(map(str, item.get("contradicting_evidence", []) or [])),
        ) for item in updated_hypotheses]
        if (old_plan_shape == new_plan_shape
                and old_hypothesis_shape == new_hypothesis_shape
                and not mission_changed):
            warnings.append("Replan 未改变 Hypothesis、Mission 或任务结构，停止无改善循环")
            return self._finish_loop(state, loop_control, warnings, "replanning_no_longer_improves")
        old_by_id = {item.get("id"): item for item in old_plan.get("subtasks", [])}
        for task in new_plan.get("subtasks", []):
            previous = old_by_id.get(task.get("id"))
            if not previous:
                continue
            same_work = (
                previous.get("capability") == task.get("capability")
                and str(previous.get("goal") or previous.get("objective", ""))
                == str(task.get("goal") or task.get("objective", ""))
            )
            if same_work and str(previous.get("status", "")).lower() == "completed":
                task["status"] = "completed"
                task["revision_count"] = int(previous.get("revision_count", 0) or 0)
                task["last_result_version"] = int(previous.get("last_result_version", 0) or 0)

        if not new_plan.get("subtasks"):
            warnings.append("Replan 未生成可执行 Subtask，保留旧 Plan")
            return self._finish_loop(state, loop_control, warnings, "replanning_no_longer_improves")

        reason = str(decision.get("reason", "")).strip()
        mission_reason = str(decision.get("mission_change_reason", "")).strip()
        replan_reason = (
            f"{reason}; Mission: {mission_reason}"
            if mission_changed and mission_reason
            else reason
        )
        loop_control.update({
            "mode": "REPLANNING",
            "total_iterations": loop_control["total_iterations"] + 1,
            "replan_count": loop_control["replan_count"] + 1,
            "revision_targets": [],
            "last_decision": "REPLAN",
            "waiting_for_user": False,
            "warnings": warnings,
            "termination_reason": "",
        })
        v2_patch = build_v2_state_patch({**state, "mission": updated_mission, "plan": new_plan,
                                         "hypotheses": updated_hypotheses,
                                         "review_v2": review, "loop_control": loop_control})
        v2_patch["plan_v2"]["revision_targets"] = []
        v2_patch["loop_control"] = loop_control
        return {
            "mission": updated_mission,
            "plan": new_plan,
            "hypotheses": updated_hypotheses,
            "plan_version": new_version,
            "replan_reason": replan_reason,
            "revision_contexts": {},
            "manager_decision": "REPLAN",
            "current_subtask": None,
            "termination_reason": "",
            **v2_patch,
            "iteration": state.get("iteration", 0) + 1,
            "messages": [{"role": "assistant", "content": f"[Manager Replan v{new_version}] {replan_reason}"}],
        }

    @staticmethod
    def _finish_loop(state: Dict[str, Any], loop_control: Dict[str, Any],
                     warnings: List[str], reason: str) -> Dict[str, Any]:
        control = normalise_loop_control(loop_control)
        control.update({
            "mode": "FINISHED",
            "last_decision": "FINISH",
            "waiting_for_user": False,
            "warnings": list(dict.fromkeys(warnings)),
            "termination_reason": reason,
            "revision_targets": [],
        })
        plan = dict(state.get("plan") or state.get("plan_v2") or {})
        plan["termination_reason"] = reason
        plan["revision_targets"] = []
        patch = build_v2_state_patch({**state, "plan": plan, "loop_control": control})
        patch["plan_v2"]["termination_reason"] = reason
        patch["plan_v2"]["revision_targets"] = []
        patch["loop_control"] = control
        return {
            "plan": plan,
            "termination_reason": reason,
            "revision_contexts": {},
            "manager_decision": "FINISH",
            **patch,
            "iteration": state.get("iteration", 0) + 1,
            "messages": [{"role": "assistant", "content": f"[Loop Controller] {reason}"}],
        }

    def _apply_replan(self, state: Dict[str, Any], hypotheses: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Deprecated compatibility alias: old 'replan' calls are local Revision."""
        return self.run_revision({**state, "hypotheses": hypotheses})

    # ================================================================
    # Mode 推断与校验
    # ================================================================
    @staticmethod
    def _infer_default_mode(agent_name: str) -> str:
        defaults = {
            "Career": "career_planning",
            "Coach": "skill_training",
            "Nutrition": "nutrition_plan",
            "Analyst": "performance_analysis",
            "Document": "comprehensive_report",
        }
        return defaults.get(agent_name, "default")

    @staticmethod
    def _validate_mission(mission: Dict[str, Any]) -> Dict[str, Any]:
        """校验 Mission 中各 Agent 的 mode 是否合法。"""
        contributions = mission.get("domain_contributions", {})
        for agent_name, contrib in contributions.items():
            if not contrib.get("needed"):
                continue
            mode = contrib.get("mode", "")
            valid_modes = VALID_MODES.get(agent_name, set())

            if mode not in valid_modes:
                correct_agent = MODE_TO_AGENT.get(mode)
                default_mode = ManagerAgent._infer_default_mode(agent_name)
                if correct_agent and correct_agent != agent_name:
                    print(f"[Manager Warn] mode '{mode}' 属于 {correct_agent}，"
                          f"不应分配给 {agent_name}，已修正为 '{default_mode}'")
                else:
                    print(f"[Manager Warn] {agent_name} 的 mode '{mode}' 不合法，"
                          f"已修正为 '{default_mode}'")
                contrib["mode"] = default_mode

        return mission

    @staticmethod
    def _fallback_mission(user_input: str) -> Dict[str, Any]:
        """Minimal semantic fallback; never guesses a fixed domain workflow."""
        return {
            "mission_id": str(uuid.uuid4())[:8],
            "intent_summary": user_input[:100],
            "objective": user_input,
            "primary_goal": user_input,
            "constraints": [],
            "context": {"information_gaps": ["Manager 模型未能完成结构化意图解析"]},
            "required_deliverable": "best_effort_response",
            "output_type": "report",
            "audience": "用户",
            "tone": "专业咨询",
            "success_criteria": ["完成最终产出", "产出符合受众需求"],
            "global_constraints": [],
            "confidence": 3,
            "pending_confirmation": False,
        }

    # ================================================================
    # Confirmation（二次确认）
    # ================================================================
    def _handle_confirmation(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """低置信度 + 多领域的二次确认：裁剪为最核心领域。"""
        mission = state.get("mission", {})
        contributions = mission.get("domain_contributions", {})

        # 保留 primary + 最多 1 个 secondary，其余设为 needed=false
        secondary_count = 0
        for agent_name, contrib in contributions.items():
            if agent_name == FINAL_AGENT:
                continue
            if contrib.get("priority") == "primary":
                continue
            if contrib.get("priority") == "secondary" and secondary_count < 1:
                secondary_count += 1
                continue
            contrib["needed"] = False

        mission["pending_confirmation"] = False
        mission["confidence"] = min(mission.get("confidence", 5) + 2, 10)

        needed_names = [n for n, c in contributions.items() if c.get("needed")]
        return {
            "mission": mission,
            "domain_outputs": state.get("domain_outputs", {}),
            "current_agent": "manager",
            "messages": [{
                "role": "assistant",
                "content": (
                    f"[Manager 二次确认] 已聚焦为核心领域: {', '.join(needed_names)}"
                ),
            }],
        }

    # ================================================================
    # Intent Checkpoint（Intent Holder 的守护节点）
    # ================================================================
    def run_checkpoint(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Intent Checkpoint：在所有领域 Agent 执行完毕后调用。

        检查各领域输出与 Mission 的相关性，生成 synthesis_guide 供 Document 使用。
        """
        mission = state.get("mission", {})
        domain_outputs = state.get("domain_outputs", {})
        contributions = mission.get("domain_contributions", {})
        plan = state.get("plan") or state.get("plan_v2") or {}
        task_by_id = {task.get("id"): task for task in plan.get("subtasks", [])}

        primary_list = []
        secondary_list = []
        supplementary_list = []

        if plan.get("subtasks"):
            for observation in state.get("observations", []):
                task = task_by_id.get(observation.get("subtask_id"), {})
                if not task or not observation.get("result"):
                    continue
                numeric_priority = int(task.get("priority", 99))
                priority = "primary" if numeric_priority == 1 else ("secondary" if numeric_priority <= 3 else "supplementary")
                entry = {
                    "domain": task.get("capability", "unknown_capability"),
                    "subtask_id": task.get("id"),
                    "content_summary": self._summarize_for_synthesis(str(observation.get("result", ""))),
                    "usage_hint": task.get("purpose", "为 Mission 提供依据"),
                    "priority": priority,
                }
                {"primary": primary_list, "secondary": secondary_list,
                 "supplementary": supplementary_list}[priority].append(entry)
        else:
            # Compatibility path for older persisted Mission-only runs.
            for display_name in SUB_AGENT_NAMES:
                if display_name == FINAL_AGENT:
                    continue
                contrib = contributions.get(display_name, {})
                output = domain_outputs.get(display_name)
                if not contrib.get("needed") or not output:
                    continue
                entry = {"domain": display_name, "content_summary": self._summarize_for_synthesis(output),
                         "usage_hint": contrib.get("output_usage", f"{display_name}领域的专业分析"),
                         "priority": contrib.get("priority", "secondary")}
                {"primary": primary_list, "secondary": secondary_list,
                 "supplementary": supplementary_list}.get(entry["priority"], secondary_list).append(entry)

        synthesis_guide = {
            "mission_brief": {
                "primary_goal": mission.get("primary_goal", ""),
                "output_type": mission.get("output_type", "report"),
                "audience": mission.get("audience", "球员本人"),
                "tone": mission.get("tone", "专业咨询"),
                "success_criteria": mission.get("success_criteria", []),
                "global_constraints": mission.get("global_constraints", []),
            },
            "primary_outputs": primary_list,
            "supporting_outputs": secondary_list,
            "supplementary_outputs": supplementary_list,
        }

        doc_mode = OUTPUT_TYPE_MODE_MAP.get(mission.get("output_type")) or "comprehensive_report"
        print(f"[Intent Checkpoint] Mission: {mission.get('primary_goal', '')[:60]}...")
        print(f"[Intent Checkpoint] 核心数据: {len(primary_list)} 份, 支撑数据: {len(secondary_list)} 份")
        print(f"[Intent Checkpoint] Document mode: {doc_mode}")

        return {
            "synthesis_guide": synthesis_guide,
            "current_agent": "manager",
            "iteration": state.get("iteration", 0) + 1,
        }

    @staticmethod
    def _summarize_for_synthesis(output: str) -> str:
        """生成面向合成指引的输出摘要（轻量级文本截取，非 LLM 调用）。"""
        if not output:
            return ""
        # 取前 300 字符作为摘要
        return output[:300] + "..." if len(output) > 300 else output

    # ================================================================
    # 工具方法
    # ================================================================
    @staticmethod
    def _extract_last_user_message(messages: List[Dict]) -> str:
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                return msg.get("content", "")
            if hasattr(msg, "type") and msg.type == "human":
                return msg.content
        return ""

    def set_phase(self, phase: str) -> None:
        self._execution_phase = phase

    # ================================================================
    # P2: Manager Assess — 动态评估与 Replan
    # ================================================================
    def run_assess(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Compatibility Manager Decision node for persisted/older graphs.

        The V2 graph routes directly to the three explicit modes.  This method
        remains as a small controller rather than the former heuristic that
        treated every short Agent output as a reason to rebuild the workflow.
        """
        review = self._normalised_review(state)
        decision = review.get("decision", "PASS")
        if decision == "REVISE":
            return self.run_revision({**state, "review_v2": review})
        if decision == "REPLAN":
            return self.run_replan({**state, "review_v2": review})

        control = normalise_loop_control(state.get("loop_control"))
        if decision == "BLOCKED":
            control.update({
                "mode": "BLOCKED",
                "last_decision": "BLOCKED",
                "waiting_for_user": True,
                "revision_targets": [],
            })
            return {
                "review_v2": review,
                "loop_control": control,
                "manager_decision": "BLOCKED",
                "iteration": state.get("iteration", 0) + 1,
            }

        control.update({"mode": "FINISHED", "last_decision": "FINISH",
                        "waiting_for_user": False, "revision_targets": []})
        return {
            "review_v2": review,
            "loop_control": control,
            "manager_decision": "FINISH",
            "iteration": state.get("iteration", 0) + 1,
            "messages": [{"role": "assistant", "content": "[Manager Decision] FINISH"}],
        }


# ================================================================
# 节点工厂函数
# ================================================================
def create_manager_node(llm: BaseChatModel):
    """创建 Manager 节点函数 + Intent Checkpoint 节点函数。

    Returns:
        (manager_node, intent_checkpoint_node, manager_instance)
    """
    manager = ManagerAgent(llm)

    def manager_node(state: Dict[str, Any]) -> Dict[str, Any]:
        mission = state.get("mission", {})
        final_report = state.get("final_report", "")

        # ---- 多轮对话检测：上一轮已完成，用户追问 → 重置 ----
        if final_report:
            manager.set_phase("planning")
            manager._short_memory = []
            result = manager.run(state)
            result["final_report"] = ""
            result["synthesis_guide"] = {}
            result["plan_version"] = 1
            result["replan_reason"] = ""
            result["domain_outputs"] = {_RESET_SENTINEL: True}
            result["observations"] = [{_RESET_SENTINEL: True}]
            result["subtask_results"] = {_RESET_SENTINEL: True}
            result["iteration"] = 0
            result["review"] = {}
            result["reviewed_data"] = {}
            result["review_passed"] = False
            result["review_findings"] = []
            result["review_conflicts"] = []
            result["review_gaps"] = []
            result["termination_reason"] = ""
            result["final_result"] = ""
            result["revision_contexts"] = {}
            result["manager_decision"] = "KEEP"
            result["telemetry"] = {"__RESET_TELEMETRY__": True}
            return result

        if mission.get("pending_confirmation"):
            manager.set_phase("confirming")
        elif not mission:
            manager.set_phase("planning")
        else:
            return state

        return manager.run(state)

    def intent_checkpoint_node(state: Dict[str, Any]) -> Dict[str, Any]:
        return manager.run_checkpoint(state)

    manager_node._telemetry_agent = manager
    intent_checkpoint_node._telemetry_agent = manager
    return manager_node, intent_checkpoint_node, manager


def create_assess_node(manager_instance: ManagerAgent):
    """创建 Manager Assess 节点函数（P2）。

    使用已有的 ManagerAgent 实例，确保 phase 和状态一致性。

    Args:
        manager_instance: 已创建的 ManagerAgent 实例。

    Returns:
        assess_node 函数。
    """
    def assess_node(state: Dict[str, Any]) -> Dict[str, Any]:
        return manager_instance.run_assess(state)

    assess_node._telemetry_agent = manager_instance
    return assess_node


def create_manager_loop_nodes(manager_instance: ManagerAgent):
    """Create explicit REVISION and REPLANNING nodes on the same Manager."""
    def revision_node(state: Dict[str, Any]) -> Dict[str, Any]:
        return manager_instance.run_revision(state)

    def replan_node(state: Dict[str, Any]) -> Dict[str, Any]:
        return manager_instance.run_replan(state)

    revision_node._telemetry_agent = manager_instance
    replan_node._telemetry_agent = manager_instance
    return revision_node, replan_node
