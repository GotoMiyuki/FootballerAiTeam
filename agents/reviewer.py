"""
FootballAI Career Agent - Reviewer Agent（信息审查员）

P3 新增：在 Document 生成报告之前审查各领域输出质量。
职责：
- 检查领域覆盖度（所有 needed Agent 是否产出）
- 检测 Agent 之间的冲突建议
- 评估单领域输出的充分性
- 发现缺口时路由回 Manager Assess 触发 Replan
- 审查通过时将整理后的数据交给 Reporter
"""

import json
from typing import Dict, Any, List
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage

from agents.base import BaseAgent
from registry import SUB_AGENT_NAMES, FINAL_AGENT

REVIEWER_SYSTEM_PROMPT = """你是信息审查员（Reviewer），负责在最终报告生成前审核各领域专家的输出。

## 审查维度
1. **覆盖度**：所有需要的领域是否都已产出？
2. **冲突检测**：不同专家的建议是否有矛盾？
   - 例如：Coach 建议高强度训练 vs Analyst 警告伤病风险
   - 例如：Nutrition 建议增重 vs Coach 建议减重提升速度
3. **充分性**：每个领域的产出是否足够详细？
4. **相关性**：产出是否紧扣 Mission 核心目标？

## 输出原则
- 发现冲突时明确指出矛盾双方和具体内容
- 对低质量产出具名批评
- 审查通过时输出整理后的数据摘要供 Reporter 使用
- 始终以 Mission.primary_goal 为最高评判标准

## 输出格式
JSON，字段：
- passed: true/false — 是否通过审查
- findings: [str] — 审查发现（通过时为空列表）
- conflicts: [{agent_a, agent_b, detail}] — 冲突列表
- gaps: [str] — 信息缺口
- low_quality: [str] — 质量不足的领域
- reviewed_summary: str — 整理后的数据摘要（通过时必须提供）
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
        synthesis_guide = state.get("synthesis_guide", {})
        plan_version = state.get("plan_version", 1)

        # ---- 代码层快速检查（非 LLM 调用，零成本） ----
        contributions = mission.get("domain_contributions", {})

        # 检查缺失
        static_missing = []
        for display_name in SUB_AGENT_NAMES:
            if display_name == FINAL_AGENT:
                continue
            if contributions.get(display_name, {}).get("needed") and display_name not in domain_outputs:
                static_missing.append(display_name)

        # 检查空输出
        static_empty = []
        for display_name, output in domain_outputs.items():
            if display_name == FINAL_AGENT:
                continue
            if not output or len(str(output).strip()) < 100:
                static_empty.append(display_name)

        # 合并静态检查结果
        all_static_issues = static_missing + static_empty
        if all_static_issues:
            # 明确的工程级问题，不需要 LLM 判断
            return {
                "review_passed": False,
                "review_findings": [
                    *(f"缺失: {a}" for a in static_missing),
                    *(f"产出不足: {a}" for a in static_empty),
                ],
                "review_conflicts": [],
                "review_gaps": all_static_issues,
                "reviewed_data": {},
                "iteration": state.get("iteration", 0) + 1,
            }

        # ---- LLM 深度审查：冲突检测和相关性 ----
        mission_brief = json.dumps({
            "primary_goal": mission.get("primary_goal", ""),
            "output_type": mission.get("output_type", ""),
            "audience": mission.get("audience", ""),
            "success_criteria": mission.get("success_criteria", []),
        }, ensure_ascii=False, indent=2)

        outputs_summary = {}
        for display_name, output in domain_outputs.items():
            if display_name == FINAL_AGENT:
                continue
            outputs_summary[display_name] = str(output)[:500]

        review_prompt = f"""## Mission
{mission_brief}

## 领域产出摘要
{json.dumps(outputs_summary, ensure_ascii=False, indent=2)}

## 审查任务
检查以上领域产出是否存在矛盾或与 Mission 目标偏离的问题。
如果所有产出协调一致且充分，设置 passed=true。
如果有问题，设置 passed=false 并详细列出。

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
        except (json.JSONDecodeError, Exception):
            # LLM 审查失败 → 保守放行
            llm_result = {"passed": True, "findings": [], "conflicts": [], "gaps": [],
                          "low_quality": [], "reviewed_summary": ""}

        passed = llm_result.get("passed", True)
        findings = llm_result.get("findings", [])
        conflicts = llm_result.get("conflicts", [])
        gaps = llm_result.get("gaps", [])
        low_quality = llm_result.get("low_quality", [])

        # 构建 reviewed_data（审查通过时使用）
        reviewed_data = {}
        if passed:
            reviewed_data = {
                "summary": llm_result.get("reviewed_summary", ""),
                "domain_order": self._sort_by_priority(contributions, domain_outputs),
            }

        return {
            "review_passed": passed,
            "review_findings": findings,
            "review_conflicts": conflicts,
            "review_gaps": gaps + low_quality,
            "reviewed_data": reviewed_data,
            "iteration": state.get("iteration", 0) + 1,
        }

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


def create_reviewer_node(llm: BaseChatModel):
    agent = ReviewerAgent(llm)

    def node_fn(state: Dict[str, Any]) -> Dict[str, Any]:
        return agent.run(state)

    return node_fn
