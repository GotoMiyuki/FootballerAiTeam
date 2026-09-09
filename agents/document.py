"""
FootballAI Career Agent - Reporter Agent（报告生成器）

P3 重构：原 Document Agent 拆分为 Reviewer + Reporter。
Reporter 职责：接收已审查的数据，按 mode 生成最终报告。
质量审查和冲突检测已移至 Reviewer Agent。
"""

import json
from typing import Dict, Any
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage

from agents.base import BaseAgent
from prompts.agent_prompts import (
    DOCUMENT_DOMAIN_IDENTITY,
    DOCUMENT_MODE_PROMPTS,
)
from registry import SUB_AGENT_NAMES, FINAL_AGENT
from graph import trim_messages_for_next_round


OUTPUT_TYPE_MODE_MAP = {
    "report": "comprehensive_report",
    "statement": "pr_statement",
    "advisory": "commercial_advisory",
    "response": "media_response",
    "plan": "comprehensive_report",
    "analysis": "comprehensive_report",
}


class DocumentAgent(BaseAgent):
    """报告生成器（Reporter）— 专注最终产出，质量审查由 Reviewer 负责。"""

    def __init__(self, llm: BaseChatModel):
        super().__init__(llm=llm)
        self._current_mode = "comprehensive_report"

    @property
    def name(self) -> str:
        return "document"

    @property
    def role(self) -> str:
        return "报告生成官（Reporter）"

    @property
    def system_prompt(self) -> str:
        guide = DOCUMENT_MODE_PROMPTS.get(self._current_mode, DOCUMENT_MODE_PROMPTS["comprehensive_report"])
        return f"{DOCUMENT_DOMAIN_IDENTITY}\n\n{guide}"

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        mission = state.get("mission", {})
        synthesis_guide = state.get("synthesis_guide", {})
        domain_outputs = state.get("domain_outputs", {})
        reviewed_data = state.get("reviewed_data", {})
        observations = state.get("observations", [])
        review = state.get("review", {})
        termination_reason = state.get("termination_reason", "")

        # Once a Plan exists, it is the authoritative scope for synthesis.
        # Replan/Revision deliberately keep historical results in state, so the
        # Reporter must project a clean, current view instead of consuming the
        # accumulated Observation log directly.
        plan_observations, has_current_plan = self._collect_current_plan_observations(state)

        # New Missions describe the deliverable without embedding an executor.
        # domain_contributions is retained only for old persisted states.
        doc_contrib = mission.get("domain_contributions", {}).get(FINAL_AGENT, {})
        mode = doc_contrib.get("mode") or OUTPUT_TYPE_MODE_MAP.get(mission.get("output_type"), "comprehensive_report")
        focus = doc_contrib.get("focus") or mission.get("required_deliverable", mission.get("primary_goal", "生成综合报告"))

        # mode 校验
        valid_modes = {"comprehensive_report", "pr_statement", "commercial_advisory", "media_response"}
        if mode not in valid_modes:
            print(f"[Reporter Warn] 非法 mode '{mode}'，fallback 到 comprehensive_report")
            mode = "comprehensive_report"

        self._current_mode = mode

        player = state.get("player_profile", {})

        # 按 priority 分层收集领域输出（优先使用 reviewed_data 中的排序）
        domain_order = reviewed_data.get("domain_order", []) if reviewed_data else []
        reviewer_summary = reviewed_data.get("summary", "") if reviewed_data else ""

        if has_current_plan:
            # An empty projection is meaningful: the current Plan has no
            # completed result that is safe to publish.  Do not fall through to
            # stale domain_outputs or Reviewer ordering in that case.
            primary_data = self._format_observations(plan_observations)
            supporting_data = ""
            supplementary_data = ""
        elif observations:
            # Observation is the source of truth because multiple subtasks may
            # legitimately use the same specialist/capability.
            primary_data = self._format_observations(observations)
            supporting_data = ""
            supplementary_data = ""
        elif domain_order:
            # 使用 Reviewer 排好的顺序
            primary_data = self._format_by_order(
                domain_order, domain_outputs, synthesis_guide, mode, detail="full"
            )
            supporting_data = ""
            supplementary_data = ""
        else:
            # 回退到 synthesis_guide 的分层
            primary_data = self._format_outputs_for_mode(
                synthesis_guide.get("primary_outputs", []), mode, detail="full"
            )
            supporting_data = self._format_outputs_for_mode(
                synthesis_guide.get("supporting_outputs", []), mode, detail="summary"
            )
            supplementary_data = self._format_outputs_for_mode(
                synthesis_guide.get("supplementary_outputs", []), mode, detail="minimal"
            )

        # 如果 synthesis_guide 为空（兜底），直接从 domain_outputs 构建
        if not has_current_plan and not synthesis_guide and not domain_order:
            primary_data = self._fallback_collect_outputs(domain_outputs, mission)

        # 如果有 Reviewer 摘要，前置注入
        if reviewer_summary:
            primary_data = f"## 审查摘要\n{reviewer_summary}\n\n{primary_data}"
        if review.get("status") in {"failed", "needs_revision"}:
            issues = "; ".join(item.get("description", "") for item in review.get("findings", []))
            primary_data = f"## 审查限制\n当前结果尚未完全通过审查：{issues}\n最终内容必须明确标注未经验证的信息。\n\n{primary_data}"
        elif termination_reason not in {"", "goal_satisfied", "evidence_sufficient", "no_meaningful_information_gap"}:
            primary_data = (f"## 闭环终止说明\n终止原因：{termination_reason}。"
                            "最终内容必须区分已验证结论与尚存不确定性。\n\n" + primary_data)

        # Mode 分派
        if mode == "pr_statement":
            result = self._generate_pr_statement(player, mission, focus, primary_data, supporting_data)
        elif mode == "commercial_advisory":
            result = self._generate_commercial_advisory(player, mission, focus, primary_data, supporting_data)
        elif mode == "media_response":
            result = self._generate_media_response(player, mission, focus, primary_data, supporting_data)
        else:
            result = self._generate_comprehensive_report(player, mission, focus, primary_data, supporting_data, supplementary_data)

        # 修剪消息：只保留用户原始意图 + 本轮完成摘要，防止上下文污染
        output_type = mission.get("output_type", "报告")
        primary_goal = mission.get("primary_goal", "")
        completion_summary = f"[本轮完成] 已生成{output_type}: {primary_goal[:100]}"
        trimmed_messages = trim_messages_for_next_round(
            state.get("messages", []), completion_summary
        )

        return {
            "domain_outputs": {"Document": result},
            "final_report": result,
            "final_result": result,
            "current_subtask": None,
            "iteration": state.get("iteration", 0) + 1,
            "messages": trimmed_messages,
        }

    # ================================================================
    # 数据分层收集
    # ================================================================
    @classmethod
    def _collect_current_plan_observations(cls, state: Dict[str, Any]) -> tuple:
        """Project current completed Plan tasks onto their newest result.

        Historical ``observations`` and ``subtask_results`` remain untouched in
        graph state.  This method only builds the ephemeral input used by the
        Reporter.  ``subtask_results`` is preferred; a legacy Observation is
        used when it is the only usable result (or carries a newer explicit
        source version).  With no Plan at all, callers retain the legacy path.
        """
        legacy_plan = state.get("plan")
        v2_plan = state.get("plan_v2")
        if isinstance(legacy_plan, dict) and isinstance(legacy_plan.get("subtasks"), list):
            current_plan = legacy_plan
        elif isinstance(v2_plan, dict) and isinstance(v2_plan.get("subtasks"), list):
            current_plan = v2_plan
        else:
            return [], False

        result_by_id = {
            str(subtask_id): result
            for subtask_id, result in (state.get("subtask_results") or {}).items()
            if isinstance(result, dict)
        }
        legacy_by_id: Dict[str, list] = {}
        for position, item in enumerate(state.get("observations") or []):
            if not isinstance(item, dict) or item.get("subtask_id") is None:
                continue
            legacy_by_id.setdefault(str(item["subtask_id"]), []).append((position, item))

        projected = []
        seen_ids = set()
        for task in current_plan.get("subtasks", []):
            if not isinstance(task, dict) or task.get("id") is None:
                continue
            task_id = str(task["id"])
            if task_id in seen_ids:
                continue
            seen_ids.add(task_id)

            # Only completed tasks remain in the publishable current scope.
            # This explicitly excludes pending/running/blocked/skipped and both
            # legacy and V2 revision-required spellings.
            if str(task.get("status", "")).strip().lower() != "completed":
                continue

            v2_item = cls._observation_from_subtask_result(task_id, result_by_id.get(task_id))
            legacy_item = cls._latest_legacy_observation(legacy_by_id.get(task_id, []))

            if v2_item and legacy_item:
                # Equal versions prefer the structured V2 contract.  A legacy
                # item only wins when it explicitly represents a newer run.
                selected = (legacy_item if cls._source_version(legacy_item) >
                            cls._source_version(v2_item) else v2_item)
            else:
                selected = v2_item or legacy_item
            if selected:
                projected.append(selected)

        return projected, True

    @classmethod
    def _observation_from_subtask_result(cls, subtask_id: str, result: Any) -> Dict[str, Any]:
        """Adapt a usable SubtaskResult to the Reporter's Observation shape."""
        if not isinstance(result, dict):
            return {}

        structured = result.get("observation")
        if isinstance(structured, dict):
            content = structured.get("result")
            facts = structured.get("facts", [])
            findings = structured.get("findings", [])
            evidence = result.get("evidence", structured.get("data_used", []))
        else:
            content = structured
            facts = []
            findings = []
            evidence = result.get("evidence", [])
        if not cls._has_result_content(content):
            content = result.get("recommendation", "")
        if not cls._has_result_content(content):
            return {}

        return {
            "subtask_id": subtask_id,
            "result": content,
            "facts": facts,
            "findings": findings,
            "evidence": evidence,
            "uncertainty": result.get("uncertainties", []),
            "source_version": cls._source_version(result),
        }

    @classmethod
    def _latest_legacy_observation(cls, entries: list) -> Dict[str, Any]:
        """Return one usable latest-version legacy Observation for a subtask."""
        usable = [
            (position, item) for position, item in entries
            if cls._has_result_content(item.get("result"))
        ]
        if not usable:
            return {}
        _, selected = max(
            usable,
            key=lambda entry: (cls._source_version(entry[1]), entry[0]),
        )
        return dict(selected)

    @staticmethod
    def _source_version(item: Dict[str, Any]) -> int:
        try:
            return max(0, int(item.get("source_version", 0) or 0))
        except (AttributeError, TypeError, ValueError):
            return 0

    @staticmethod
    def _has_result_content(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        return bool(value)

    @staticmethod
    def _format_outputs_for_mode(outputs: list, mode: str, detail: str = "full") -> str:
        """按 mode 选择性格式化领域输出。

        detail: "full" — 完整内容 / "summary" — 摘要 / "minimal" — 仅标题
        """
        if not outputs:
            return ""

        blocks = []
        for entry in outputs:
            domain = entry.get("domain", "未知")
            usage = entry.get("usage_hint", "")
            content = entry.get("content_summary", "")

            if detail == "minimal":
                blocks.append(f"- **{domain}**: {usage}")
            elif detail == "summary":
                blocks.append(f"### {domain}\n*用途: {usage}*\n\n{content[:500]}")
            else:
                blocks.append(f"### {domain}\n*用途: {usage}*\n\n{content}")

        return "\n\n".join(blocks)

    @staticmethod
    def _format_by_order(domain_order: list, domain_outputs: Dict, synthesis_guide: Dict,
                         mode: str, detail: str = "full") -> str:
        """按 Reviewer 指定的顺序格式化领域输出。"""
        blocks = []
        primary_outputs = {e["domain"]: e for e in synthesis_guide.get("primary_outputs", [])}
        supporting_outputs = {e["domain"]: e for e in synthesis_guide.get("supporting_outputs", [])}
        all_entries = {**primary_outputs, **supporting_outputs}

        for domain in domain_order:
            output = domain_outputs.get(domain, "")
            if not output:
                continue
            entry = all_entries.get(domain, {})
            usage = entry.get("usage_hint", "")
            content = entry.get("content_summary", str(output)[:500])

            if detail == "minimal":
                blocks.append(f"- **{domain}**: {usage}")
            elif detail == "summary":
                blocks.append(f"### {domain}\n*用途: {usage}*\n\n{content[:500]}")
            else:
                blocks.append(f"### {domain}\n*用途: {usage}*\n\n{content}")

        return "\n\n".join(blocks)

    @staticmethod
    def _fallback_collect_outputs(domain_outputs: Dict[str, str], mission: Dict) -> str:
        """兜底：直接从 domain_outputs 构建（synthesis_guide 为空时）。"""
        blocks = []
        for agent_name in SUB_AGENT_NAMES:
            if agent_name == FINAL_AGENT:
                continue
            contrib = mission.get("domain_contributions", {}).get(agent_name, {})
            if not contrib.get("needed"):
                continue
            output = domain_outputs.get(agent_name)
            if not output:
                continue
            blocks.append(f"### {agent_name}\n{output[:800]}")
        return "\n\n".join(blocks)

    @staticmethod
    def _format_observations(observations: list) -> str:
        """Format accepted execution results by problem-oriented subtask."""
        blocks = []
        for item in observations:
            raw_result = item.get("result", "")
            if isinstance(raw_result, (dict, list)):
                result = json.dumps(raw_result, ensure_ascii=False, default=str).strip()
            else:
                result = str(raw_result).strip()
            if not result:
                continue
            block = [f"### Subtask {item.get('subtask_id', 'unknown')}", result[:2500]]
            if item.get("uncertainty"):
                block.append(f"不确定性: {'; '.join(map(str, item['uncertainty']))}")
            if item.get("findings"):
                descriptions = [
                    finding.get("description", "") if isinstance(finding, dict) else str(finding)
                    for finding in item["findings"]
                ]
                block.append("审查发现: " + "; ".join(filter(None, descriptions)))
            blocks.append("\n".join(block))
        return "\n\n".join(blocks)

    # ================================================================
    # Mode: comprehensive_report
    # ================================================================
    def _generate_comprehensive_report(
        self, player: Dict, mission: Dict, focus: str,
        primary_data: str, supporting_data: str, supplementary_data: str,
    ) -> str:
        player_name = player.get("name", "球员")
        player_info = f"{player_name} | {player.get('position', 'N/A')} | 年龄 {player.get('age', 'N/A')} | 评分 {player.get('overall', 'N/A')}"

        mission_brief = self._build_mission_brief(mission)

        prompt = f"""{mission_brief}

## 球员信息
{player_info}

## 核心支撑数据（优先使用）
{primary_data if primary_data else '无'}

## 补充参考数据
{supporting_data if supporting_data else '无'}

## 其他参考
{supplementary_data if supplementary_data else '无'}

## 任务
{focus}

请以 Mission 核心目标为主线组织所有章节。报告前两章必须围绕核心目标展开。
如果支撑数据与目标不完全相关，以目标为准。
请直接输出完整报告。"""

        try:
            response = self.llm.invoke([
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=prompt),
            ])
            return response.content
        except Exception:
            return self._fallback_report(player, mission, primary_data, supporting_data)

    def _fallback_report(self, player, mission, primary_data, supporting_data) -> str:
        """LLM 失败时的回退报告，根据 output_type 生成不同格式。"""
        player_name = player.get("name", "球员")
        player_info = f"{player_name} | {player.get('position', 'N/A')} | 年龄 {player.get('age', 'N/A')} | 评分 {player.get('overall', 'N/A')}"
        output_type = mission.get("output_type", "report")

        if output_type == "statement":
            club = player.get("club", "当前俱乐部")
            return (
                f"【对外发布稿】\n\n"
                f"关于近期媒体有关{player_name}的转会传闻，{club}特此声明："
                f"球员目前专注于为俱乐部效力，俱乐部不对任何转会传闻予以评论。"
            )

        if output_type == "advisory":
            return (
                f"【商业评估报告】\n\n"
                f"# {player_name} - 商业价值评估\n\n"
                f"**基本信息**: {player_info}\n\n"
                f"## 评估\n基于球员当前数据，商业价值处于成长阶段。"
                f"建议优先建立社交媒体存在感。\n\n"
                f"## 风险提示\n竞技状态波动可能影响商业价值。"
            )

        if output_type == "response":
            club = player.get("club", "当前俱乐部")
            return (
                f"【媒体应答手册】\n\n"
                f"# {player_name} - 媒体采访应答指南\n\n"
                f"## 核心信息\n1. 专注于当前赛季目标\n"
                f"2. 感谢俱乐部和教练组的支持\n3. 持续提升自身能力\n\n"
                f"## 敏感话题回避策略\n"
                f"- 转会话题: '我目前专注于为{club}效力'\n"
                f"- 合同细节: '这是我和俱乐部之间的私事'\n"
                f"\n## 建议语气\n真诚、职业、不卑不亢"
            )

        # 默认：综合报告
        parts = [f"【报告】\n# {player_name} - 综合发展报告\n"]
        parts.append(f"**球员信息**: {player_info}\n")
        parts.append(f"**核心目标**: {mission.get('primary_goal', '未指定')}\n\n---\n")
        if primary_data:
            parts.append(f"## 核心分析\n{primary_data}\n")
        if supporting_data:
            parts.append(f"## 补充参考\n{supporting_data}\n")
        parts.append("## 行动建议\n- [ ] 根据上述分析制定具体执行计划\n")
        parts.append("\n---\n*本报告由 AI 运动科学团队自动生成*")
        return "\n".join(parts)

    # ================================================================
    # Mode: pr_statement
    # ================================================================
    def _generate_pr_statement(
        self, player: Dict, mission: Dict, focus: str,
        primary_data: str, supporting_data: str,
    ) -> str:
        player_name = player.get("name", "球员")
        club = player.get("club", "当前俱乐部")
        position = player.get("position", "N/A")

        mission_brief = self._build_mission_brief(mission)

        context = ""
        if primary_data:
            context += f"\n## 参考数据\n{primary_data[:600]}"
        if supporting_data:
            context += f"\n{supporting_data[:400]}"

        prompt = f"""{mission_brief}

## 背景
- 球员: {player_name}，{position}，效力于 {club}
{context}

## 任务
{focus}

根据 Mission 目标撰写官方声明。100-200 字，正式权威语气，不得确认未公开信息。
请直接输出声明文本，开头标注 **【对外发布稿】**。"""

        try:
            response = self.llm.invoke([
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=prompt),
            ])
            return response.content
        except Exception:
            return (
                f"【对外发布稿】\n\n"
                f"关于近期媒体有关{player_name}的转会传闻，{club}特此声明："
                f"球员目前专注于为俱乐部效力，俱乐部不对任何转会传闻予以评论。"
            )

    # ================================================================
    # Mode: commercial_advisory
    # ================================================================
    def _generate_commercial_advisory(
        self, player: Dict, mission: Dict, focus: str,
        primary_data: str, supporting_data: str,
    ) -> str:
        player_name = player.get("name", "球员")
        age = player.get("age", "N/A")
        position = player.get("position", "N/A")
        overall = player.get("overall", "N/A")

        mission_brief = self._build_mission_brief(mission)

        context = ""
        if primary_data:
            context += f"\n## 参考数据\n{primary_data[:600]}"
        if supporting_data:
            context += f"\n{supporting_data[:400]}"

        prompt = f"""{mission_brief}

## 球员数据
- 姓名: {player_name}
- 位置: {position} | 年龄: {age} | 综合评分: {overall}
{context}

## 任务
{focus}

评估球员的商业价值。Markdown 格式，开头标注 **【商业评估报告】**。
请直接输出完整商业评估报告。"""

        try:
            response = self.llm.invoke([
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=prompt),
            ])
            return response.content
        except Exception:
            return (
                f"【商业评估报告】\n\n"
                f"# {player_name} - 商业价值评估\n\n"
                f"**基本信息**: {age}岁，{position}，综合评分 {overall}\n\n"
                f"## 评估\n基于球员当前数据，商业价值处于成长阶段。"
                f"建议优先建立社交媒体存在感，与运动装备品牌建立初步合作。\n\n"
                f"## 风险提示\n竞技状态波动可能影响商业价值，建议与竞技表现挂钩的合作模式。"
            )

    # ================================================================
    # Mode: media_response
    # ================================================================
    def _generate_media_response(
        self, player: Dict, mission: Dict, focus: str,
        primary_data: str, supporting_data: str,
    ) -> str:
        player_name = player.get("name", "球员")
        club = player.get("club", "当前俱乐部")
        position = player.get("position", "N/A")

        mission_brief = self._build_mission_brief(mission)

        context = ""
        if primary_data:
            context += f"\n## 参考数据\n{primary_data[:600]}"
        if supporting_data:
            context += f"\n{supporting_data[:400]}"

        prompt = f"""{mission_brief}

## 背景
- 球员: {player_name}，{position}，效力于 {club}
{context}

## 任务
{focus}

为球员准备媒体采访应答策略。包含：3-5 个核心应答要点、敏感话题回避话术、建议语气。
Markdown 格式，开头标注 **【媒体应答手册】**。
请直接输出完整应答手册。"""

        try:
            response = self.llm.invoke([
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=prompt),
            ])
            return response.content
        except Exception:
            return (
                f"【媒体应答手册】\n\n"
                f"# {player_name} - 媒体采访应答指南\n\n"
                f"## 核心信息\n1. 专注于当前赛季目标\n2. 感谢俱乐部和教练组的支持\n"
                f"3. 持续提升自身能力\n\n"
                f"## 敏感话题回避策略\n- 转会话题: '我目前专注于为{club}效力'\n"
                f"- 合同细节: '这是我和俱乐部之间的私事'\n\n"
                f"## 建议语气\n真诚、职业、不卑不亢"
            )

    # ================================================================
    # 辅助：构建 Mission Brief（Prompt 头部）
    # ================================================================
    @staticmethod
    def _build_mission_brief(mission: Dict) -> str:
        """从 Mission 构建 prompt 头部（最高优先级）。"""
        if not mission:
            return ""

        parts = [
            "## Mission（最高优先级）",
            f"**核心目标**: {mission.get('primary_goal', '')}",
            f"**产出类型**: {mission.get('output_type', 'report')}",
            f"**目标受众**: {mission.get('audience', '综合')}",
            f"**语气**: {mission.get('tone', '专业咨询')}",
        ]

        criteria = mission.get("success_criteria", [])
        if criteria:
            parts.append(f"**成功标准**: {', '.join(criteria)}")

        constraints = mission.get("constraints", mission.get("global_constraints", []))
        if constraints:
            parts.append(f"**全局约束**: {'; '.join(constraints)}")

        return "\n".join(parts)


def create_document_node(llm: BaseChatModel):
    agent = DocumentAgent(llm)

    def node_fn(state: Dict[str, Any]) -> Dict[str, Any]:
        return agent.run(state)

    return node_fn
