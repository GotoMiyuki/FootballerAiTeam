"""
FootballAI Career Agent - 主入口

基于 LangGraph 的足球运动员职业生涯多智能体系统。
Mission-driven v2: 以 Mission 为中心的 Intent Flow。

使用方式：
    python app.py                              # 新会话
    python app.py "帮我制定训练计划"             # 命令行直接输入
    python app.py --list                       # 列出历史会话
    python app.py --continue <thread_id>        # 恢复历史会话
"""

import sys
import json

# 修复 Windows GBK 控制台编码：避免打印含特殊字符（如 ™）的文件名时崩溃
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from dotenv import load_dotenv

load_dotenv()

from config import config
from utils.helpers import create_llm, check_config
from utils.sessions import create_session, update_session, list_sessions, get_session
from graph import build_graph, create_initial_state
from langgraph.types import Command


def build_agent_graph():
    """构建并返回编译后的 LangGraph 图。"""
    llm = create_llm()

    from agents.manager import create_manager_node, create_assess_node, create_manager_loop_nodes
    from agents.nutrition import create_nutrition_node
    from agents.coach import create_coach_node
    from agents.analyst import create_analyst_node
    from agents.career import create_career_node
    from agents.document import create_document_node
    from agents.reviewer import create_reviewer_node

    manager_node, intent_checkpoint_node, manager = create_manager_node(llm)
    assess_node = create_assess_node(manager)
    revision_node, replan_node = create_manager_loop_nodes(manager)

    agent_nodes = {
        "manager": manager_node,
        "manager_assess": assess_node,
        "manager_revision": revision_node,
        "manager_replan": replan_node,
        "intent_checkpoint": intent_checkpoint_node,
        "reviewer": create_reviewer_node(llm),
        "nutrition": create_nutrition_node(llm),
        "coach": create_coach_node(llm),
        "analyst": create_analyst_node(llm),
        "career": create_career_node(llm),
        "document": create_document_node(llm),
    }
    # P3: 在 Document 生成最终报告前暂停，允许用户审核中间结果
    return build_graph(agent_nodes, interrupt_before=["document"])


def print_header():
    print("=" * 60)
    print("  FootballAI Career Agent - 多智能体协作系统")
    print("  基于 LangGraph + DeepSeek | Mission-driven v1.1")
    print("=" * 60)
    print()


def _process_events(graph, input_data, config_dict):
    """执行一次图流式调用，处理事件并返回 (final_report, interrupted)。"""
    final_report = ""

    for event in graph.stream(input_data, config_dict):
        for node_name, node_output in event.items():
            if node_name == "manager":
                mission = node_output.get("mission", {})
                if mission:
                    print(f"[Manager] {mission.get('intent_summary', '')}")
                    print(f"[Manager] 核心目标: {mission.get('primary_goal', '')[:80]}...")
                    print(f"[Manager] 置信度: {mission.get('confidence', '?')}/10")
                    subtasks = node_output.get("plan", {}).get("subtasks", [])
                    print(f"[Manager] 动态子任务: {len(subtasks)} 个")
                    for task in subtasks:
                        print(f"  - {task.get('id')}: {task.get('goal')} [{task.get('capability')}]")

            elif node_name == "intent_checkpoint":
                pass  # run_checkpoint 内部已打印详情

            elif node_name == "document":
                report = node_output.get("final_report", "")
                if report:
                    final_report = report
                    print(f"\n[Document] 报告生成完成\n")

            elif node_name == "manager_confirm":
                mission = node_output.get("mission", {})
                if mission:
                    print(f"[Manager 确认] Mission: {mission.get('objective', '')}")

            elif node_name == "manager_assess":
                plan_ver = node_output.get("plan_version", 1)
                reason = node_output.get("replan_reason", "")
                if reason:
                    print(f"[Manager Assess v{plan_ver}] {reason}")

            elif node_name == "manager_revision":
                targets = node_output.get("loop_control", {}).get("revision_targets", [])
                if targets:
                    print(f"[Manager Revision] 仅重做: {', '.join(targets)}")

            elif node_name == "manager_replan":
                reason = node_output.get("replan_reason", "")
                print(f"[Manager Replan] {reason or '核心假设已变化，生成新 Plan'}")

            elif node_name == "human_input":
                next_decision = node_output.get("review_v2", {}).get("decision", "")
                print(f"[Human-in-the-loop] 已收到补充信息，重新判定为 {next_decision or 'PASS'}")

            elif node_name == "reviewer":
                decision = node_output.get("review_v2", {}).get("decision", "")
                findings = node_output.get("review_findings", [])
                if decision == "PASS":
                    print("[Reviewer] PASS")
                else:
                    print(f"[Reviewer] {decision or 'UNKNOWN'}: {'; '.join(findings[:3])}")

            elif node_name == "__interrupt__":
                pass  # LangGraph interrupt 事件，跳过

            else:
                domain_outputs = node_output.get("domain_outputs", {})
                for agent_name in domain_outputs:
                    print(f"[{agent_name}] 完成")

    state = graph.get_state(config_dict)
    interrupted = bool(getattr(state, 'next', ())) if state else False
    return final_report, interrupted


def _show_interrupt_summary(graph, config_dict):
    """在 interrupt 点展示当前状态摘要，等待用户确认。"""
    state = graph.get_state(config_dict)
    if not state or not state.values:
        return

    values = state.values
    mission = values.get("mission", {})
    review_passed = values.get("review_passed", False)
    review_findings = values.get("review_findings", [])
    review_v2 = values.get("review_v2", {})
    plan_version = values.get("plan_version", 1)
    next_nodes = tuple(getattr(state, "next", ()) or ())

    print("\n" + "-" * 40)
    label = "补充信息" if "human_input" in next_nodes else "最终报告审核点"
    print(f"  [Human-in-the-loop] {label}")
    print(f"  核心目标: {mission.get('primary_goal', '')[:60]}")

    completed = [task.get("id") for task in values.get("plan", {}).get("subtasks", [])
                 if task.get("status") == "completed"]
    print(f"  已完成子任务: {', '.join(completed) if completed else '无'}")

    if review_findings:
        print(f"  Reviewer 发现: {'; '.join(review_findings[:2])}")
    elif review_passed:
        print(f"  Reviewer: 审查通过")
    print(f"  计划版本: v{plan_version}")
    if "human_input" in next_nodes:
        blocking = review_v2.get("blocking_information", [])
        print(f"  需要补充: {'; '.join(blocking) if blocking else '受影响 Subtask 所需的关键事实'}")
    print("-" * 40)


def run_stream(graph, initial_state, thread_id):
    """执行图流式调用，支持 Human-in-the-loop interrupt。

    在 Document 节点前暂停，展示中间结果等待用户确认。
    返回 (final_report, final_state)。
    """
    config_dict = {"configurable": {"thread_id": thread_id}, "recursion_limit": 80}
    final_report = ""

    # 第一阶段：运行到 interrupt 点（Document 之前）
    final_report, interrupted = _process_events(graph, initial_state, config_dict)

    # 如果被中断，等待用户确认后继续
    while interrupted:
        _show_interrupt_summary(graph, config_dict)
        snapshot = graph.get_state(config_dict)
        next_nodes = tuple(getattr(snapshot, "next", ()) or ()) if snapshot else ()

        if "human_input" in next_nodes:
            supplied = input("\n请补充上述关键信息（q=退出）: ").strip()
            if supplied.lower() == "q":
                print("[中断] 本次运行已停在 BLOCKED；当前 checkpoint 仅在本进程内有效。")
                break
            if not supplied:
                print("[提示] 补充信息不能为空。")
                continue
            final_report, interrupted = _process_events(
                graph, Command(resume={"information": supplied}), config_dict,
            )
        else:
            choice = input("\n继续生成最终报告？[回车=继续 / q=退出]: ").strip().lower()
            if choice == "q":
                print("[中断] 用户取消，流程终止。")
                break
            # Static Document breakpoint: resume the fixed next node. Replan is
            # authorized only by a structured Reviewer decision.
            final_report, interrupted = _process_events(
                graph, None, config_dict,
            )

    final_state = graph.get_state(config_dict)
    return final_report, final_state


def print_result(final_report, final_state, config_dict, graph):
    """打印最终报告。"""
    if final_report:
        print("\n" + "=" * 60)
        print("  最终报告")
        print("=" * 60 + "\n")
        print(final_report)
    else:
        if final_state and final_state.values:
            report = final_state.values.get("final_report", "")
            if report:
                print("\n" + "=" * 60)
                print("  最终报告")
                print("=" * 60 + "\n")
                print(report)
            else:
                control = final_state.values.get("loop_control", {})
                if control.get("waiting_for_user"):
                    print("\n[提示] 流程停在 BLOCKED；需在本次运行的提示中补充信息才能继续，"
                          "未触发 Agent 重试或 Replan。")
                else:
                    print("\n[提示] 未生成最终报告，请检查 Mission 配置。")

    if final_state and final_state.values:
        telemetry = final_state.values.get("telemetry", {}) or {}
        if telemetry:
            print(
                "\n[Telemetry] "
                f"LLM={telemetry.get('llm_call_count', 0)} | "
                f"Reviewer={telemetry.get('review_count', 0)} | "
                f"Agent={telemetry.get('agent_call_count', 0)} | "
                f"Revision={telemetry.get('revision_count', 0)} | "
                f"Replan={telemetry.get('replan_count', 0)} | "
                f"Latency={telemetry.get('latency_ms', 0):.0f}ms | "
                f"Tokens in/out={telemetry.get('input_tokens', 0)}/{telemetry.get('output_tokens', 0)}"
            )

    print("\n" + "=" * 60)
    waiting = bool(final_state and final_state.values
                   and final_state.values.get("loop_control", {}).get("waiting_for_user"))
    print("  本次多智能体流程停在 BLOCKED" if waiting else "  多智能体协作流程完成！")
    print("=" * 60)


def cmd_list():
    """列出历史会话。"""
    sessions = list_sessions()
    if not sessions:
        print("暂无历史会话。")
        return
    print(f"\n{'Thread ID':<28} {'轮数':<6} {'时间':<22} 首次输入")
    print("-" * 90)
    for s in sessions:
        tid = s["thread_id"]
        rounds = s.get("rounds", 1)
        updated = s.get("updated_at", s.get("created_at", ""))
        first = s.get("first_input", "")[:40]
        print(f"{tid:<28} {rounds:<6} {updated:<22} {first}")


def cmd_continue(thread_id):
    """恢复历史会话。"""
    session = get_session(thread_id)
    if not session:
        print(f"会话 {thread_id} 不存在。使用 --list 查看可用会话。")
        return

    print(f"[恢复会话] {thread_id}")
    print(f"  首次: {session.get('first_input', '')[:80]}")
    print(f"  轮数: {session.get('rounds', 1)}")
    user_input = input("\n请输入追问内容: ").strip()
    if not user_input:
        print("输入为空，退出。")
        return

    update_session(thread_id, user_input)
    initial_state = create_initial_state(user_input)
    config_dict = {"configurable": {"thread_id": thread_id}}

    print(f"\n[User] {user_input}")
    print("\n" + "=" * 60)
    print("  继续多智能体协作流程...")
    print("=" * 60 + "\n")

    graph = build_agent_graph()
    final_report, final_state = run_stream(graph, initial_state, thread_id)
    print_result(final_report, final_state, config_dict, graph)


def cmd_new(user_input=None):
    """启动新会话。"""
    if not user_input:
        print("\n" + "-" * 40)
        print("示例需求：")
        print("  1. 三个月后参加大学联赛，想提升爆发力和减重")
        print("  2. 帮我分析最近的训练效果和比赛表现")
        print("  3. 我想规划一下未来的职业发展路径")
        print("  4. 为我制定一份完整的赛前准备方案")
        print("  5. 请帮我回应媒体关于加盟曼城的传闻")
        print("-" * 40)
        user_input = input("\n请输入您的需求: ").strip()
        if not user_input:
            user_input = "三个月后参加大学联赛，目前体重偏重，想提升爆发力和速度"

    thread_id = create_session(user_input)
    initial_state = create_initial_state(user_input)

    print(f"[Session] {thread_id}")
    print(f"\n[User] {user_input}")
    print("\n" + "=" * 60)
    print("  开始多智能体协作流程...")
    print("=" * 60 + "\n")

    graph = build_agent_graph()
    config_dict = {"configurable": {"thread_id": thread_id}}
    final_report, final_state = run_stream(graph, initial_state, thread_id)
    print_result(final_report, final_state, config_dict, graph)


def main():
    print_header()

    if not check_config():
        print("\n请先在 .env 文件中配置 API Key 后重试。")
        return

    # ---- CLI 参数解析 ----
    if len(sys.argv) > 1:
        if sys.argv[1] == "--list":
            cmd_list()
            return
        elif sys.argv[1] == "--continue" and len(sys.argv) > 2:
            cmd_continue(sys.argv[2])
            return
        else:
            # 命令行直接输入
            user_input = " ".join(sys.argv[1:])
            cmd_new(user_input)
            return

    # ---- 交互模式（默认：新会话） ----
    try:
        cmd_new()
    except KeyboardInterrupt:
        print("\n\n[Interrupted] 用户中断执行")
    except Exception as e:
        print(f"\n[Error] 执行出错: {e}")
        if config.DEBUG:
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
