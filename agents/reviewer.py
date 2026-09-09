"""
FootballAI Career Agent - Reviewer Agent（信息审查员）

P3 新增：在 Document 生成报告之前审查各领域输出质量。
职责：发现并结构化报告问题；不修改 Plan，也不调用任何 Agent。
"""

import json
from typing import Dict, Any, List
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage

from agents.base import BaseAgent
from registry import SUB_AGENT_NAMES, FINAL_AGENT
from loop_contracts import migrate_hypothesis, migrate_subtask, normalise_review_result

REVIEWER_SYSTEM_PROMPT = """你是信息审查员（Reviewer），负责在最终报告生成前审核 Subtask 结果。

## 审查维度
1. Completeness（完成度）
2. Consistency（一致性，包括跨领域冲突）
3. Evidence / Provenance（关键结论的证据与来源）
4. Validity（数值、计算与推理有效性）
5. Constraint Compliance（Mission/用户约束合规性）
6. Risk / Uncertainty（过度自信、未证实强结论与风险表达）

## 审查阈值
- 只报告会影响 Mission 正确性、可执行性、安全性或内部一致性的实质问题。
- 低价值措辞、个人偏好和可接受的不确定性不得触发 Revision。
- INFO / LOW → KEEP；MEDIUM → REVISION；HIGH → REVISION 或有核心变化证据时 REPLAN；CRITICAL → REPLAN 或缺关键输入时 BLOCKED。
- BLOCKED 只表示缺少当前无法替代的外部/用户输入；不得建议 Agent 猜测或重试。
- REPLAN 必须指出被证伪的核心 Hypothesis、多个 Subtask 共享的错误假设，或 Mission 解释变化；局部冲突只能 REVISION。
- 输入会提供带版本和正反证据的结构化 Hypothesis。若选择 REPLAN，finding 必须点名相关 Hypothesis id，并引用其反证、状态变化或跨 Subtask 冲突；不得自行改写 Hypothesis。
- Reviewer 只报告 Finding 和整体 decision；不得重写 Plan、扩大执行范围或调用 Agent。

## 输出格式（严格 JSON）
{
  "decision": "PASS | REVISE | REPLAN | BLOCKED",
  "findings": [{
    "id": "finding_01",
    "subtask_ids": ["subtask_03"],
    "severity": "INFO | LOW | MEDIUM | HIGH | CRITICAL",
    "category": "completeness | consistency | evidence | validity | constraint | risk | conflict",
    "description": "发现了什么",
    "evidence": [],
    "action": "KEEP | REVISION | REPLAN | BLOCKED",
    "reason": "为什么影响 Mission"
  }],
  "reviewed_subtasks": ["subtask_01"],
  "blocking_information": [],
  "summary": "供最终合成使用的审查摘要"
}
只输出 JSON，不要其他文本。"""


class ReviewerAgent(BaseAgent):
    """信息审查员 — 在 Reporter 之前把关。"""

    def __init__(self, llm: BaseChatModel):
        super().__init__(llm=llm)

    @property
    def name(self) -> str:
        return "reviewer"

    @property
    def role(self) -> str:
        return "信息审查员（Reviewer）"

    @property
    def system_prompt(self) -> str:
        return REVIEWER_SYSTEM_PROMPT

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        mission = state.get("mission", {})
        domain_outputs = state.get("domain_outputs", {})
        plan = state.get("plan") or state.get("plan_v2") or {}
        contributions = mission.get("domain_contributions", {})
        subtasks = [migrate_subtask(item) for item in plan.get("subtasks", [])
                    if isinstance(item, dict)]
        known_ids = {item.get("id") for item in subtasks}

        # Contract failures are deterministic findings.  Reviewer reports them
        # but deliberately does not mark or rewrite the Plan.
        observation_by_id = {item.get("subtask_id"): item for item in state.get("observations", [])}
        result_by_id = state.get("subtask_results", {}) or {}
        static_findings = []
        blocking_information = []
        if plan.get("subtasks"):
            for task in plan.get("subtasks", []):
                task_id = task.get("id")
                status = str(task.get("status", "pending")).lower()
                if status == "skipped":
                    continue
                if status == "blocked":
                    reason = str(task.get("blocked_reason") or "缺少继续该 Subtask 所需的关键输入")
                    blocking_information.append(reason)
                    static_findings.append({
                        "id": f"finding_{len(static_findings) + 1:02d}",
                        "subtask_ids": [task_id],
                        "severity": "CRITICAL",
                        "category": "completeness",
                        "description": f"{task_id} 当前被关键输入阻塞",
                        "evidence": [reason],
                        "action": "BLOCKED",
                        "reason": reason,
                    })
                    continue

                result = result_by_id.get(task_id, {})
                v2_observation = result.get("observation") if isinstance(result, dict) else None
                legacy_observation = observation_by_id.get(task_id)
                has_v2_result = bool(
                    isinstance(result, dict)
                    and (v2_observation or str(result.get("recommendation", "")).strip())
                )
                has_legacy_result = bool(
                    isinstance(legacy_observation, dict)
                    and str(legacy_observation.get("result", "")).strip()
                )
                if status != "completed" or not (has_v2_result or has_legacy_result):
                    static_findings.append({
                        "id": f"finding_{len(static_findings) + 1:02d}",
                        "subtask_ids": [task_id],
                        "severity": "HIGH" if status != "completed" else "MEDIUM",
                        "category": "completeness",
                        "description": f"子任务未形成可审查的结构化结果: {task_id}",
                        "evidence": [f"status={status}"],
                        "action": "REVISION",
                        "reason": "缺少 SubtaskResult.status / observation / result 契约所需内容",
                    })
        else:
            # Compatibility path for checkpoints created before Plan existed.
            for display_name in SUB_AGENT_NAMES:
                if display_name != FINAL_AGENT and contributions.get(display_name, {}).get("needed"):
                    if display_name not in domain_outputs:
                        static_findings.append({
                            "id": f"finding_{len(static_findings) + 1:02d}",
                            "subtask_ids": [], "severity": "HIGH", "category": "completeness",
                            "description": f"旧版领域结果缺失: {display_name}", "evidence": [],
                            "action": "KEEP", "reason": "旧状态没有 Subtask scope，不能安全扩大重跑范围",
                        })

        if static_findings:
            raw_review = {
                "decision": "BLOCKED" if blocking_information else "REVISE",
                "findings": static_findings,
                "reviewed_subtasks": [item.get("id") for item in subtasks
                                      if item.get("status") == "COMPLETED"],
                "blocking_information": blocking_information,
                "summary": "结构化结果契约检查发现实质问题。",
            }
            review_v2 = normalise_review_result(raw_review, subtasks)
            return self._review_state_patch(review_v2, contributions, domain_outputs)

        # ---- LLM 深度审查：冲突检测和相关性 ----
        mission_context = mission.get("context", {})
        if not isinstance(mission_context, dict):
            mission_context = {}
        current_information_gaps = self._unique_values(
            mission.get("information_gaps", []),
            mission_context.get("information_gaps", []),
            plan.get("information_gaps", []),
        )
        resolved_information_gaps = self._unique_values(
            mission.get("resolved_information_gaps", []),
            mission_context.get("resolved_information_gaps", []),
            plan.get("resolved_information_gaps", []),
        )
        mission_brief = json.dumps({
            "primary_goal": mission.get("primary_goal", ""),
            "output_type": mission.get("output_type", ""),
            "audience": mission.get("audience", ""),
            "success_criteria": mission.get("success_criteria", []),
            "constraints": mission.get("constraints", mission.get("global_constraints", [])),
            "information_gaps": {
                "current": self._bounded_evidence(current_information_gaps),
                "resolved": self._bounded_mapping(resolved_information_gaps),
            },
        }, ensure_ascii=False, indent=2)

        try:
            plan_version = max(1, int(plan.get("version", state.get("plan_version", 1)) or 1))
        except (TypeError, ValueError):
            plan_version = 1
        raw_hypotheses = state.get("hypotheses_v2") or state.get("hypotheses", [])
        hypotheses_summary = []
        for item in raw_hypotheses if isinstance(raw_hypotheses, list) else []:
            if not isinstance(item, dict):
                continue
            hypothesis = migrate_hypothesis(item, plan_version)
            hypotheses_summary.append({
                "id": hypothesis.get("id", ""),
                "statement": hypothesis.get("statement", ""),
                "confidence": hypothesis.get("confidence", 0.5),
                "status": hypothesis.get("status", "OPEN"),
                "supporting_evidence": self._bounded_evidence(
                    hypothesis.get("supporting_evidence", [])
                ),
                "contradicting_evidence": self._bounded_evidence(
                    hypothesis.get("contradicting_evidence", [])
                ),
                "version": {
                    "created_in_plan_version": hypothesis.get("created_in_plan_version", plan_version),
                    "updated_in_plan_version": hypothesis.get("updated_in_plan_version", plan_version),
                },
            })

        outputs_summary = []
        for task in plan.get("subtasks", []):
            task_id = task.get("id")
            result = result_by_id.get(task_id)
            if isinstance(result, dict):
                raw_observation = result.get("observation", {})
                output_item = {
                    "subtask_id": task_id,
                    "status": result.get("status"),
                    "observation": self._bounded_mapping(raw_observation),
                    "evidence": self._bounded_evidence(result.get("evidence", [])),
                    "assumptions": self._bounded_evidence(result.get("assumptions", [])),
                    "uncertainties": self._bounded_evidence(result.get("uncertainties", [])),
                    "source_version": result.get("source_version"),
                }
                # Agent output is commonly stored in observation.result and
                # recommendation simultaneously. Avoid injecting it twice.
                observation_has_result = (
                    isinstance(raw_observation, dict)
                    and self._has_meaningful_value(raw_observation.get("result"))
                )
                if not observation_has_result:
                    output_item["recommendation"] = self._bounded_value(
                        result.get("recommendation", ""), max_string=1500
                    )
                outputs_summary.append(output_item)
                continue
            observation = observation_by_id.get(task_id, {})
            outputs_summary.append({
                "subtask_id": task_id,
                "status": task.get("status"),
                "observation": self._bounded_mapping({"result_preview": observation.get("result", "")}),
                "evidence": self._bounded_evidence(observation.get("evidence", [])),
                "uncertainties": self._bounded_evidence(observation.get("uncertainty", [])),
            })

        review_prompt = f"""## Mission
{mission_brief}

## Hypotheses（结构化 V2 快照，只读）
{json.dumps(self._bounded_mapping(hypotheses_summary), ensure_ascii=False, indent=2)}

## Plan
{json.dumps(self._bounded_mapping(plan), ensure_ascii=False, indent=2)}

## 按子任务组织的 Observation
{json.dumps(outputs_summary, ensure_ascii=False, indent=2)}

## 审查任务
只报告影响 Mission 正确性、可执行性、安全性或内部一致性的实质问题。
每个局部 finding 必须用 subtask_ids 指向最小影响范围；不要修改 Plan，也不要提出完整执行计划。
判断 REPLAN 前必须对照 Hypotheses：只有核心假设被反证、共享假设导致多个 Subtask 失效，或 Mission 解释变化才可选择 REPLAN，并在 finding 中点名 Hypothesis id 及证据。
若只有 INFO/LOW 或可接受不确定性，decision 必须为 PASS 且 action 为 KEEP。

只输出 JSON。"""

        try:
            response = self.llm.invoke([
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=review_prompt),
            ])
            content = response.content.strip()
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            llm_result = json.loads(content)
        except Exception:
            # A Reviewer transport/schema failure is not missing user input.
            # Finish with an explicit uncertainty instead of retrying Agents or
            # pretending that Human-in-the-loop can repair an internal failure.
            llm_result = {
                "decision": "PASS",
                "findings": [{
                    "id": "finding_review_unavailable", "subtask_ids": [],
                    "severity": "HIGH", "category": "review_unavailable",
                    "description": "Reviewer 模型未返回有效的结构化审查结果",
                    "evidence": [], "action": "KEEP",
                    "reason": "保留当前结果，但最终内容必须标注未经完整审查的不确定性",
                }],
                "reviewed_subtasks": list(known_ids),
                "blocking_information": [],
                "summary": "审查未完成，最终结果必须明确保留不确定性。",
            }

        review_v2 = normalise_review_result(llm_result, subtasks)
        return self._review_state_patch(review_v2, contributions, domain_outputs)

    @staticmethod
    def _sort_by_priority(contributions: Dict, outputs: Dict) -> List[str]:
        """按 priority 排序领域输出，供 Reporter 使用。"""
        order = []
        for priority in ("primary", "secondary", "supplementary"):
            for name in SUB_AGENT_NAMES:
                if name == FINAL_AGENT:
                    continue
                if name in outputs and contributions.get(name, {}).get("priority") == priority:
                    order.append(name)
        return order

    @staticmethod
    def _bounded_evidence(value: Any) -> Any:
        return ReviewerAgent._bounded_value(
            value, max_depth=3, max_keys=8, max_items=5, max_string=1000
        )

    @staticmethod
    def _bounded_mapping(value: Any) -> Any:
        return ReviewerAgent._bounded_value(
            value, max_depth=4, max_keys=12, max_items=8, max_string=1500
        )

    @staticmethod
    def _bounded_value(value: Any, *, depth: int = 0, max_depth: int = 3,
                       max_keys: int = 12, max_items: int = 8,
                       max_string: int = 1500) -> Any:
        """Recursively bound untrusted Agent data before adding it to a prompt."""
        if isinstance(value, str):
            return value if len(value) <= max_string else f"{value[:max_string]}…"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if depth >= max_depth:
            if isinstance(value, dict):
                return {"_truncated": f"nested mapping ({len(value)} keys)"}
            if isinstance(value, (list, tuple, set)):
                return [f"_truncated nested sequence ({len(value)} items)"]
            text = str(value)
            return text if len(text) <= max_string else f"{text[:max_string]}…"
        if isinstance(value, dict):
            items = list(value.items())
            bounded = {
                str(key)[:120]: ReviewerAgent._bounded_value(
                    item,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_keys=max_keys,
                    max_items=max_items,
                    max_string=max_string,
                )
                for key, item in items[:max_keys]
            }
            if len(items) > max_keys:
                bounded["_truncated_keys"] = len(items) - max_keys
            return bounded
        if isinstance(value, (list, tuple, set)):
            items = list(value)
            bounded = [
                ReviewerAgent._bounded_value(
                    item,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_keys=max_keys,
                    max_items=max_items,
                    max_string=max_string,
                )
                for item in items[:max_items]
            ]
            if len(items) > max_items:
                bounded.append(f"_truncated_items={len(items) - max_items}")
            return bounded
        text = str(value)
        return text if len(text) <= max_string else f"{text[:max_string]}…"

    @staticmethod
    def _has_meaningful_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (dict, list, tuple, set)):
            return bool(value)
        return True

    @staticmethod
    def _unique_values(*values: Any) -> List[Any]:
        """Merge gap lists without assuming that entries are hashable strings."""
        merged = []
        seen = set()
        for value in values:
            candidates = value if isinstance(value, list) else ([value] if value else [])
            for candidate in candidates:
                try:
                    marker = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
                except (TypeError, ValueError):
                    marker = str(candidate)
                if marker in seen:
                    continue
                seen.add(marker)
                merged.append(candidate)
        return merged

    @staticmethod
    def _legacy_review(review_v2: Dict[str, Any]) -> Dict[str, Any]:
        """One-way display adapter; never use this payload for V2 routing."""
        status_map = {
            "PASS": "passed", "REVISE": "needs_revision",
            "REPLAN": "needs_revision", "BLOCKED": "failed",
        }
        action_map = {
            "KEEP": "accept_with_uncertainty",
            "REVISION": "revise_subtask",
            "REPLAN": "revise_subtask",
            "BLOCKED": "request_more_evidence",
        }
        findings = []
        for finding in review_v2.get("findings", []):
            subtask_ids = list(finding.get("subtask_ids", []) or [])
            findings.append({
                "id": finding.get("id"),
                "subtask_id": subtask_ids[0] if subtask_ids else None,
                "subtask_ids": subtask_ids,
                "type": finding.get("category", "review_issue"),
                "severity": str(finding.get("severity", "MEDIUM")).lower(),
                "description": finding.get("description", ""),
                "evidence": finding.get("evidence", []),
                "action": action_map.get(finding.get("action"), "accept_with_uncertainty"),
                "reason": finding.get("reason", ""),
            })
        return {
            "status": status_map.get(review_v2.get("decision"), "passed"),
            "decision": review_v2.get("decision", "PASS"),
            "findings": findings,
            "blocking_information": list(review_v2.get("blocking_information", []) or []),
            "summary": review_v2.get("summary", ""),
        }

    def _review_state_patch(self, review_v2: Dict[str, Any], contributions: Dict[str, Any],
                            domain_outputs: Dict[str, Any]) -> Dict[str, Any]:
        """Return Reviewer-owned state only; Plan and results stay untouched."""
        legacy = self._legacy_review(review_v2)
        passed = review_v2.get("decision") == "PASS"
        reviewed_data = {}
        if passed:
            reviewed_data = {
                "summary": review_v2.get("summary", ""),
                "domain_order": self._sort_by_priority(contributions, domain_outputs),
            }
        conflicts = [item for item in legacy["findings"] if item.get("type") == "conflict"]
        return {
            "review_v2": review_v2,
            "review": legacy,
            "review_passed": passed,
            "review_findings": [item.get("description", "") for item in review_v2.get("findings", [])],
            "review_conflicts": conflicts,
            "review_gaps": list(review_v2.get("blocking_information", []) or []),
            "reviewed_data": reviewed_data,
        }


def create_reviewer_node(llm: BaseChatModel):
    agent = ReviewerAgent(llm)

    def node_fn(state: Dict[str, Any]) -> Dict[str, Any]:
        return agent.run(state)

    return node_fn
