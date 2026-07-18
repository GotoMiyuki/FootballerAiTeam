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
from prompts.agent_prompts import MANAGER_PROMPT, DOMAIN_IDENTITIES
from registry import (
    AGENT_REGISTRY,
    SUB_AGENT_NAMES,
    FINAL_AGENT,
    get_agent_descriptions,
)
from utils.helpers import describe_player_attributes
from graph import _DELETE_SENTINEL


CONFIDENCE_THRESHOLD = 6
MAX_DOMAINS_WITHOUT_CONFIRMATION = 3

VALID_MODES = {
    "Career": {"career_planning", "transfer_analysis"},
    "Coach": {"skill_training"},
    "Nutrition": {"nutrition_plan"},
    "Analyst": {"performance_analysis"},
    "Document": {"comprehensive_report", "pr_statement", "commercial_advisory", "media_response"},
}

MODE_TO_AGENT = {}
for _agent, _modes in VALID_MODES.items():
    for _mode in _modes:
        MODE_TO_AGENT[_mode] = _agent

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

            result = {
                "mission": mission,
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

            # 低置信度 → 二次确认
            confidence = mission.get("confidence", 10)
            needed_count = sum(
                1 for c in mission.get("domain_contributions", {}).values()
                if c.get("needed")
            )
            if confidence < CONFIDENCE_THRESHOLD and needed_count > MAX_DOMAINS_WITHOUT_CONFIRMATION:
                mission["pending_confirmation"] = True
                result["messages"].append({
                    "role": "assistant",
                    "content": (
                        f"[Manager] 置信度较低({confidence}/10)且涉及{needed_count}个领域，"
                        "进入二次确认..."
                    ),
                })

            return result

        return {"current_agent": "manager"}

    # ================================================================
    # Mission Creation（替代原 _analyze_intent_and_plan）
    # ================================================================
    def _create_mission(
        self, user_input: str, player_profile: Dict[str, Any]
    ) -> Dict[str, Any]:
        """核心：使用 LLM 分析意图并创建 Mission 对象。"""
        agent_descriptions = get_agent_descriptions()

        # 生成领域身份摘要供 LLM 参考
        domain_summary_lines = []
        for display_name, info in AGENT_REGISTRY.items():
            identity = DOMAIN_IDENTITIES.get(display_name, "")
            # 取第一段作为简短描述
            short_desc = identity.split("\n\n")[0] if identity else info["description"]
            domain_summary_lines.append(f"- **{display_name}**: {short_desc}")

        domain_summary = "\n".join(domain_summary_lines)

        mission_prompt = f"""你是一名足球俱乐部总经理。分析球员需求并创建 Mission 对象。

## 球员档案
{json.dumps(player_profile, ensure_ascii=False, indent=2) if player_profile else "暂无球员数据"}

## 球员能力概览
{describe_player_attributes(player_profile.get('attributes', {}), player_profile.get('other_features', {})) if player_profile else "暂无"}

## 用户需求
{user_input}

## 可调用的专业领域及其职责范围

{agent_descriptions}

## 意图分类决策树

分析用户需求时按以下顺序判断：

1. **用户问的是"怎么说"还是"怎么选"？**
   - "怎么说"（公关回应/媒体声明/辟谣/代言评估/采访话术）→ 核心领域是 Document
   - "怎么选"（去哪家俱乐部/什么联赛/发展路径选择）→ 核心领域是 Career

2. **用户提到"转会"时，具体在问什么？**
   - 转会传闻回应、辟谣、官方声明、媒体追问 → Document（pr_statement 或 media_response）
   - 转会可行性分析、战术适配、联赛对比、市场估值 → Career（transfer_analysis）

3. **用户提到"合同""身价""市场价值"时：**
   - 问"我的市场价值/身价/估值" → Career（career_planning）
   - 问"代言合同/赞助/商业合作/品牌" → Document（commercial_advisory）

4. **用户提到"训练""技术""体能"时 → Coach**
5. **用户提到"饮食""体重""营养"时 → Nutrition**
6. **用户提到"数据""趋势""伤病风险"时 → Analyst**

## 易混淆场景速查

| 用户输入（简化） | 核心领域 | 产出类型 |
|---|---|---|
| "最近转会传闻很多，帮我回应" | Document | pr_statement |
| "有俱乐部想签我，该去吗" | Career | transfer_analysis |
| "帮我看看我的市场价值" | Career | career_planning |
| "记者问我合同细节，怎么回答" | Document | media_response |
| "耐克想找我代言，该接吗" | Document | commercial_advisory |
| "我适合踢英超还是德甲" | Career | transfer_analysis |
| "俱乐部官宣我离队，帮我写声明" | Document | pr_statement |
| "帮我制定完整的赛前准备方案" | Career + Coach + Nutrition | comprehensive_report |

## Mission 输出格式（严格JSON）

```json
{{
  "intent_summary": "用户意图的一句话概括",
  "primary_goal": "本次 Mission 的核心目标（简洁明确，所有 Agent 以此为准）",
  "output_type": "report / statement / advisory / response / plan / analysis",
  "audience": "球员本人 / 媒体与公众 / 俱乐部管理层 / 商业伙伴 / 综合",
  "tone": "正式权威 / 专业咨询 / 亲和真诚 / 坚定克制 / 综合",
  "success_criteria": ["成功标准1", "成功标准2"],
  "domain_contributions": {{
    "Career": {{
      "needed": true/false,
      "mode": "career_planning / transfer_analysis",
      "priority": "primary / secondary / supplementary",
      "focus": "该领域应聚焦的具体方向",
      "output_usage": "该领域产出将如何被 Document 使用"
    }},
    "Coach": {{
      "needed": true/false,
      "mode": "skill_training",
      "priority": "primary / secondary / supplementary",
      "focus": "...",
      "output_usage": "..."
    }},
    "Nutrition": {{
      "needed": true/false,
      "mode": "nutrition_plan",
      "priority": "primary / secondary / supplementary",
      "focus": "...",
      "output_usage": "..."
    }},
    "Analyst": {{
      "needed": true/false,
      "mode": "performance_analysis",
      "priority": "primary / secondary / supplementary",
      "focus": "...",
      "output_usage": "..."
    }},
    "Document": {{
      "needed": true,
      "mode": "comprehensive_report / pr_statement / commercial_advisory / media_response",
      "priority": "primary",
      "focus": "最终产出的具体描述",
      "output_usage": "最终面向用户/受众的完整输出"
    }}
  }},
  "global_constraints": ["全局约束1", "全局约束2"],
  "confidence": 8
}}
```

## 决策规则
1. 先走意图分类决策树，再决定领域贡献，最后选 mode
2. 单一维度需求只设 1 个核心领域（priority=primary），其他不需要的设 needed=false
3. 复合需求设 2-3 个领域，明确标注优先级
4. Document 始终 needed=true（负责最终产出），其 mode 由 output_type 决定
5. mode 必须从枚举中选择，不可自创
6. 如果意图是"对外沟通/公关/媒体/商业"，Career 必须 needed=false

请只输出JSON，不要有任何其他文本。"""

        try:
            response = self.llm.invoke([
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
            mission.setdefault("primary_goal", user_input)
            mission.setdefault("audience", "综合")
            mission.setdefault("tone", "专业咨询")
            mission.setdefault("success_criteria", ["完成最终产出", "产出符合受众需求"])
            mission.setdefault("global_constraints", [])
            mission.setdefault("pending_confirmation", False)

            # 规范化 domain_contributions
            raw_contributions = mission.get("domain_contributions", {})
            normalized = {}
            for display_name in SUB_AGENT_NAMES:
                contrib = raw_contributions.get(display_name, {})
                if not isinstance(contrib, dict):
                    contrib = {}
                normalized[display_name] = {
                    "needed": contrib.get("needed", display_name == FINAL_AGENT),
                    "mode": contrib.get("mode", self._infer_default_mode(display_name)),
                    "priority": contrib.get("priority", "primary" if display_name == FINAL_AGENT else "secondary"),
                    "focus": contrib.get("focus", ""),
                    "output_usage": contrib.get("output_usage", ""),
                }

            # Document 始终 needed
            normalized[FINAL_AGENT]["needed"] = True
            if normalized[FINAL_AGENT]["priority"] != "primary":
                normalized[FINAL_AGENT]["priority"] = "primary"

            mission["domain_contributions"] = normalized

            # 校验 mode 合法性
            mission = self._validate_mission(mission)

            return mission

        except (json.JSONDecodeError, Exception) as e:
            try:
                print(f"[Manager] Mission 创建失败，使用保守策略: {type(e).__name__}")
            except Exception:
                pass
            return self._fallback_mission(user_input)

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
        """LLM 失败时的回退 Mission，对常见模式做基本推断。"""
        user_lower = user_input.lower()

        # 基本意图推断
        if any(w in user_lower for w in ["回应", "声明", "辟谣", "公关", "传闻", "媒体", "官宣"]):
            output_type = "statement"
            doc_mode = "pr_statement"
            focus = user_input
            audience = "媒体与公众"
            tone = "正式权威"
        elif any(w in user_lower for w in ["代言", "赞助", "品牌", "商业"]):
            output_type = "advisory"
            doc_mode = "commercial_advisory"
            focus = user_input
            audience = "商业伙伴"
            tone = "专业咨询"
        elif any(w in user_lower for w in ["采访", "记者", "应答", "话术"]):
            output_type = "response"
            doc_mode = "media_response"
            focus = user_input
            audience = "媒体"
            tone = "亲和真诚"
        elif any(w in user_lower for w in ["转会", "俱乐部", "联赛"]):
            output_type = "analysis"
            doc_mode = "comprehensive_report"
            focus = user_input
            audience = "球员本人"
            tone = "专业咨询"
        else:
            output_type = "report"
            doc_mode = "comprehensive_report"
            focus = user_input
            audience = "球员本人"
            tone = "专业咨询"

        return {
            "mission_id": str(uuid.uuid4())[:8],
            "intent_summary": user_input[:100],
            "primary_goal": user_input,
            "output_type": output_type,
            "audience": audience,
            "tone": tone,
            "success_criteria": ["完成最终产出", "产出符合受众需求"],
            "domain_contributions": {
                "Career": {"needed": False, "mode": "career_planning", "priority": "secondary", "focus": "", "output_usage": ""},
                "Coach": {"needed": False, "mode": "skill_training", "priority": "secondary", "focus": "", "output_usage": ""},
                "Nutrition": {"needed": False, "mode": "nutrition_plan", "priority": "secondary", "focus": "", "output_usage": ""},
                "Analyst": {"needed": False, "mode": "performance_analysis", "priority": "secondary", "focus": "", "output_usage": ""},
                "Document": {"needed": True, "mode": doc_mode, "priority": "primary", "focus": focus, "output_usage": "最终面向用户的完整输出"},
            },
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

        primary_list = []
        secondary_list = []
        supplementary_list = []

        for display_name in SUB_AGENT_NAMES:
            if display_name == FINAL_AGENT:
                continue
            contrib = contributions.get(display_name, {})
            if not contrib.get("needed"):
                continue
            output = domain_outputs.get(display_name)
            if not output:
                continue

            entry = {
                "domain": display_name,
                "content_summary": self._summarize_for_synthesis(output),
                "usage_hint": contrib.get("output_usage", f"{display_name}领域的专业分析"),
                "priority": contrib.get("priority", "secondary"),
            }

            priority = contrib.get("priority", "secondary")
            if priority == "primary":
                primary_list.append(entry)
            elif priority == "secondary":
                secondary_list.append(entry)
            else:
                supplementary_list.append(entry)

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

        doc_contrib = contributions.get(FINAL_AGENT, {})
        doc_mode = doc_contrib.get("mode", "comprehensive_report")
        doc_focus = doc_contrib.get("focus", mission.get("primary_goal", "生成最终输出"))

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
        """Manager Assess 节点：评估各领域输出质量，决定是否需要 Replan。

        评估逻辑：
        1. 检查所有 needed=True 的 Agent 是否都已产出 domain_output
        2. 检查已产出内容的质量（长度是否过短）
        3. 如果存在缺失或质量问题，且 plan_version < 3，触发 Replan
        4. Replan：重新激活未产出的 Agent，为低质量 Agent 调整 focus
        5. 如果无问题或已达最大 Replan 次数，放行至 Intent Checkpoint
        """
        mission = state.get("mission", {})
        domain_outputs = state.get("domain_outputs", {})
        plan_version = state.get("plan_version", 1)
        contributions = mission.get("domain_contributions", {})

        # ---- 检查缺失的 Agent ----
        missing = []
        for display_name in SUB_AGENT_NAMES:
            if display_name == FINAL_AGENT:
                continue
            contrib = contributions.get(display_name, {})
            if contrib.get("needed") and display_name not in domain_outputs:
                missing.append(display_name)

        # ---- 检查低质量输出（长度过短） ----
        low_quality = []
        for display_name, output in domain_outputs.items():
            if display_name == FINAL_AGENT:
                continue
            if not output or len(str(output)) < 200:
                low_quality.append(display_name)

        needs_replan = bool(missing or low_quality)
        replan_reason = ""

        if needs_replan and plan_version < 3:
            new_version = plan_version + 1

            if missing:
                replan_reason = f"以下领域未完成: {', '.join(missing)}"
                for name in missing:
                    if name in contributions:
                        contributions[name]["needed"] = True
                        contributions[name]["priority"] = "secondary"
                        contributions[name]["focus"] = (
                            f"[Replan v{new_version}] "
                            + contributions[name].get("focus", "")
                        )

            if low_quality:
                if replan_reason:
                    replan_reason += "; "
                replan_reason += f"产出质量不足: {', '.join(low_quality)}"
                for name in low_quality:
                    if name in contributions:
                        contributions[name]["needed"] = True
                        contributions[name]["priority"] = "secondary"
                        current_focus = contributions[name].get("focus", "")
                        if not current_focus.startswith("[Replan"):
                            contributions[name]["focus"] = (
                                f"[Replan v{new_version}] 请提供更详细的分析，"
                                f"当前产出过于简略。原方向: {current_focus}"
                            )
                        # 标记低质量输出为待删除（reducer 会识别此 sentinel）
                        domain_outputs[name] = _DELETE_SENTINEL

            mission["domain_contributions"] = contributions

            return {
                "mission": mission,
                "domain_outputs": domain_outputs,
                "plan_version": new_version,
                "replan_reason": replan_reason,
                "iteration": state.get("iteration", 0) + 1,
                "messages": [{
                    "role": "assistant",
                    "content": (
                        f"[Manager Assess v{new_version}] Replan: {replan_reason}"
                    ),
                }],
            }

        # ---- 无问题 或 已达最大 Replan 次数 ----
        if needs_replan:
            reason = f"Replan 已达上限(v{plan_version})，强制进入 Intent Checkpoint"
        else:
            reason = "所有领域已完成，质量合格"

        return {
            "plan_version": plan_version,
            "replan_reason": reason,
            "iteration": state.get("iteration", 0) + 1,
            "messages": [{
                "role": "assistant",
                "content": f"[Manager Assess] {reason}",
            }],
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

    return assess_node
