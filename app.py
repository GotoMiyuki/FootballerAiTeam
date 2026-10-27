"""
FootballAI Career Agent - 主入口

基于 LangGraph 的足球运动员职业生涯多智能体系统。
Mission-driven v2: 以 Mission 为中心的 Intent Flow。

使用方式：
    python app.py                              # 新会话
    python app.py "帮我制定训练计划"             # 命令行直接输入
    python app.py --list                       # 列出历史会话
    python app.py --show <mission_id>           # 只读原报告与任务引用
    python app.py --continue <mission_id>       # 从持久化暂停恢复
    python app.py --reevaluate <mission_id> "新要求" --reason "原因"
"""

import sys
import json
from uuid import uuid4, uuid5, NAMESPACE_URL

# 修复 Windows GBK 控制台编码：避免打印含特殊字符（如 ™）的文件名时崩溃
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from dotenv import load_dotenv

load_dotenv()

from config import config
from utils.helpers import check_config
from utils.sessions import SessionRepository, list_sessions, get_session
from graph import create_initial_state
from langgraph.types import Command


def build_agent_graph(checkpoint_path, on_start=None):
    """复用持久化运行时工厂；调用方负责关闭返回的连接。"""
    from backend.runtime import create_runtime
    return create_runtime(checkpoint_path, on_start or (lambda *_: None))


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
                if report and node_output.get('delivery_status') == 'PUBLISHABLE':
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
                print(f"[Human-in-the-loop] 已收到补充信息，重新判定为 {next_decision or '未完成审查'}")

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
                    print(f"[{agent_name}] {'未输出结果' if node_output.get('execution_outcome') in {'FAILED', 'NO_RESULT'} else '本轮返回'}")

    state = graph.get_state(config_dict)
    interrupted = bool(getattr(state, 'next', ())) if state else False
    return final_report, interrupted


def _show_interrupt_summary(graph, config_dict):
    """在 interrupt 点展示当前状态摘要，等待用户确认。"""
    _show_snapshot_summary(graph.get_state(config_dict))


def _show_snapshot_summary(state):
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
    label = "补充信息" if "human_input" in next_nodes else "报告生成前确认"
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
                print("[中断] 本次运行停在 BLOCKED；恢复能力取决于调用方的持久化 checkpoint 配置。")
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
    from execution_contracts import publishable
    final_report = final_state.values.get("final_report", "") if final_state and publishable(final_state.values) else ""
    if final_report:
        print("\n" + "=" * 60)
        print("  最终报告")
        print("=" * 60 + "\n")
        print(final_report)
    else:
        if final_state and final_state.values:
            control = final_state.values.get("loop_control", {})
            if control.get("waiting_for_user"):
                print("\n[提示] 流程停在 BLOCKED；需从原 checkpoint 补充信息才能继续，"
                      "未触发 Agent 重试或 Replan。")
            else:
                print("\n[提示] " + (final_state.values.get("failure_reason") or "未形成可发布报告"))

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
                f"Node latency sum={telemetry.get('latency_ms', 0):.0f}ms | "
                f"Response tokens in/out={telemetry.get('input_tokens') if telemetry.get('usage_tracking_version') == 1 and telemetry.get('input_tokens') is not None else '未知'}/{telemetry.get('output_tokens') if telemetry.get('usage_tracking_version') == 1 and telemetry.get('output_tokens') is not None else '未知'}"
            )

    print("\n" + "=" * 60)
    waiting = bool(final_state and final_state.values
                   and final_state.values.get("loop_control", {}).get("waiting_for_user"))
    print("  本次多智能体流程停在 BLOCKED" if waiting else
          "  多智能体协作流程完成！" if final_report else "  本次运行未交付报告")
    print("=" * 60)


def cmd_list():
    """只读当前 CLI 任务索引及旧元数据，不构造运行时。"""
    records = SessionRepository().list()
    legacy = list_sessions()
    if not records and not legacy:
        print("暂无历史会话。")
        return
    for record in records:
        print(f"{record['thread_id']}  {record['status']}  {record['created_at']}  {record['first_input'][:60]}")
    for record in legacy:
        print(f"{record['thread_id']}  UNVERIFIED（旧元数据）  {record.get('first_input', '')[:60]}")


def _legacy_history(thread_id):
    session = get_session(thread_id)
    if not session:
        raise ValueError(f"任务 {thread_id} 不存在。使用 --list 查看可用任务。")
    print(f"[历史索引] {thread_id}")
    print(f"  首次: {session.get('first_input', '')[:80]}")
    print("  此旧索引不含持久化 checkpoint、原快照或完整报告，无法恢复原执行。")
    print('  如需分析当前状态，请使用 python app.py "新的任务需求" 创建独立任务。')


def _show_mission(repository, mission):
    print(f"[任务] {mission.id} | {mission.status}")
    print(f"  目标: {mission.objective}")
    print(f"  原输入引用: {mission.input_reference.model_dump_json()}")
    if mission.lineage:
        print(f"  来源: {mission.lineage.parent_mission_id} | {mission.lineage.operation} | {mission.lineage.reason}")
    for operation in repository.operations(mission.id):
        print(f"  操作: {operation.id} | {operation.kind.value} | {operation.status.value} | run={operation.run_id} | cleanup={operation.cleanup_status}")
        from backend.operation_metrics import meter_summary, time_summary
        usage, timing = meter_summary(operation), time_summary(operation)
        print(f"  已观测LLM={usage['logical_calls'] if usage['logical_calls'] is not None else '未知'} | HTTP尝试={usage['network_attempts'] if usage['network_attempts'] is not None else '未知'} | 已知响应token={usage['known_input_tokens']}/{usage['known_output_tokens']} | 完整用量={'已知' if usage['complete'] else '未知'}")
        print('  时间秒: ' + ' | '.join(f"{key}={timing[key]:.3f}" if timing[key] is not None else f"{key}=未知"
            for key in ('queue_seconds', 'execution_seconds', 'user_wait_seconds', 'wall_seconds')))
        if operation.pause_id:
            print(f"  pause_id: {operation.pause_id}")
    report = repository.report(mission)
    if report:
        print("\n最终报告（原任务、原数据版本）\n" + report)
    elif mission.error:
        print("  " + mission.error)
    elif mission.status == 'BLOCKED':
        print("  已保存暂停；使用 --continue 后按原等待原因补信息或审批。")
    else:
        print("  尚无可发布报告。运行中断的任务不能自动恢复，请明确创建关联重试。")


def cmd_show(thread_id):
    repository = SessionRepository()
    mission = repository.mission(thread_id)
    if mission:
        _show_mission(repository, mission)
    else:
        _legacy_history(thread_id)
    return mission


def _collect_input(blocked):
    """将明确的 CLI 输入转换为现有表单字段；q 不提交。"""
    if blocked.reason == 'report_approval':
        choice = input("\n继续生成最终报告？[回车=批准 / q=保存退出]: ").strip().lower()
        if choice == 'q':
            return None
        if choice not in {'', 'y', 'yes', '确认'}:
            raise ValueError('请输入回车批准或 q 保存退出')
        return {'approved': True}
    if blocked.reason != 'missing_user_input':
        raise ValueError('原任务等待原因不支持恢复')
    print(blocked.message)
    values = {}
    for field in blocked.required_inputs:
        hint = ', '.join(field.options)
        raw = input(f"{field.label}（q=保存退出）{(' [' + hint + ']') if hint else ''}: ").strip()
        if raw.lower() == 'q':
            return None
        if not raw and not field.required:
            continue
        if field.input_type in {'number', 'scale'}:
            import math
            try:
                value = float(raw)
            except ValueError as error:
                raise ValueError(f'{field.label}必须为数值') from error
            if not math.isfinite(value):
                raise ValueError(f'{field.label}必须为有限数值')
        elif field.input_type == 'boolean':
            if raw.lower() not in {'yes', 'no', 'y', 'n', '是', '否'}:
                raise ValueError(f'{field.label}请填写是或否')
            value = raw.lower() in {'yes', 'y', '是'}
        else:
            value = raw
        values[field.key] = value
    return values


def _drive_cli(service, mission_id, input_request_id=None):
    """等待同一服务任务，暂停退出不更改原任务或 checkpoint。"""
    from backend.runtime import inspect_checkpoint
    while True:
        service.wait()
        mission = service.view(service.store.get(mission_id))
        if mission.status != 'BLOCKED':
            break
        if mission.resume_error:
            print('[无法恢复] ' + mission.resume_error)
            break
        snapshot = inspect_checkpoint(service.checkpoint_path, mission_id)
        _show_snapshot_summary(snapshot)
        try:
            supplied = _collect_input(mission.blocked)
        except (EOFError, KeyboardInterrupt):
            supplied = None
        except ValueError as error:
            print('[输入无效] ' + str(error))
            continue
        if supplied is None:
            print(f'[暂停已保存] {mission.id}；稍后使用 --continue {mission.id} 恢复。')
            break
        try:
            from backend.models import InputRequest
            request_id = input_request_id or f'input_{uuid4().hex}'
            print(f'[输入请求] {request_id}', flush=True)
            service.input(mission.id, InputRequest(request_id=request_id, pause_id=mission.blocked.pause_id, values=supplied))
            input_request_id = None
        except ValueError as error:
            print('[输入无效] ' + str(error))
    # Read the same configured workspace; no latest player access during display.
    print(f"[任务] {mission.id} | {mission.status}")
    report = service.store.report(mission.id) if mission.status == 'COMPLETED' and mission.delivery_status == 'PUBLISHABLE' else None
    if report:
        print("\n最终报告\n" + report)
    elif mission.error:
        print('[执行失败] ' + mission.error)
    return mission


def cmd_continue(thread_id, request_id=None):
    """仅恢复支持的持久化暂停；完成历史只读，不重建初始状态。"""
    from backend.runtime import inspect_checkpoint
    from backend.task_context import validate_pause
    from backend.cli import CLIMissionService
    repository = SessionRepository()
    mission = repository.mission(thread_id)
    if not mission:
        _legacy_history(thread_id)
        return
    receipt = repository.receipt(request_id) if request_id else None
    if receipt:
        if receipt.get('kind') == 'cancellation':
            raise ValueError('该request_id已用于取消，不能作为暂停输入')
        if receipt['mission_id'] != thread_id:
            raise ValueError('该请求属于另一个任务')
        print(f'[已有请求回执] {request_id} | {receipt["message_id"]}')
        _show_mission(repository, mission)
        return mission
    if mission.status != 'BLOCKED':
        _show_mission(repository, mission)
        if mission.status != 'COMPLETED':
            print('[无法恢复] 任务没有停在支持的暂停位置。')
        return mission
    if not mission.blocked:
        raise ValueError('原任务暂停信息缺失，无法恢复')
    try:
        snapshot = inspect_checkpoint(repository.checkpoint_path, mission.id)
    except Exception as error:
        raise ValueError('原 checkpoint 无法读取或不兼容，无法恢复') from error
    validate_pause(mission, snapshot)
    print(f"[恢复原任务] {mission.id} | 数据版本 {mission.input_reference.state_version}")
    if not check_config():
        raise ValueError('请配置模型 API Key；原暂停仍保留')
    service = CLIMissionService(repository.root)
    try:
        return _drive_cli(service, mission.id, input_request_id=request_id)
    finally:
        service.close()


def cmd_new(user_input=None, request_id=None):
    from backend.cli import CLIMissionService
    from backend.models import MessageRequest
    from backend.service import message_fingerprint
    if not user_input:
        user_input = input('\n请输入任务需求: ').strip()
    if not user_input:
        raise ValueError('任务需求不能为空')
    service = CLIMissionService(SessionRepository().root)
    try:
        request_id = request_id or f'request_{uuid4().hex}'
        request = MessageRequest(request_id=request_id, conversation_id=f'cli_{uuid5(NAMESPACE_URL, request_id).hex}', content=user_input)
        print(f'[创建请求] {request_id}', flush=True)
        if not service.store.request_result(request_id, message_fingerprint(request)) and not check_config():
            raise ValueError('请配置模型 API Key；请求尚未接收')
        accepted = service.submit(request)
        identity = accepted.mission_id
        print(f'[新任务] {identity}')
        if accepted.replayed:
            print('[已有请求回执] 不自动继续原任务')
            mission = service.store.get(identity)
            _show_mission(SessionRepository(), mission)
            return mission
        return _drive_cli(service, identity)
    finally:
        service.close()


def cmd_explain(thread_id, question, request_id=None):
    from backend.cli import CLIMissionService
    from backend.models import MessageRequest
    from backend.service import message_fingerprint
    repository = SessionRepository()
    parent = repository.mission(thread_id)
    if not parent or not question.strip():
        raise ValueError('解释需要已保存的任务和问题')
    request_id = request_id or f'explain_{uuid4().hex}'
    request = MessageRequest(request_id=request_id, conversation_id=parent.conversation_id,
        mission_id=thread_id, intent='explain', content=question)
    service = CLIMissionService(repository.root)
    try:
        print(f'[解释请求] {request_id}', flush=True)
        if not service.store.request_result(request_id, message_fingerprint(request)) and not check_config():
            raise ValueError('请配置模型 API Key；请求尚未接收')
        accepted = service.submit(request)
        if not accepted.replayed:
            service.wait()
        operation = service.store.operation(accepted.operation_id)
        print(f'[操作] {accepted.operation_id} | {operation.status.value}')
        for message in service.store.messages(parent.conversation_id):
            if message.in_reply_to == accepted.message_id:
                print(message.content)
        if operation.status != 'COMPLETED':
            raise ValueError('解释操作未完成，原报告保留；重新提问请使用新的request_id')
        return service.store.get(thread_id)
    finally:
        service.close()


def cmd_related(thread_id, user_input, reason, request_id, *, operation):
    from backend.cli import CLIMissionService
    from backend.continuations import Continuation
    from uuid import uuid4
    repository = SessionRepository()
    parent = repository.mission(thread_id)
    if not parent:
        raise ValueError('来源不是可验证的 CLI 任务，请新建普通任务')
    if not user_input or not reason:
        raise ValueError('关联任务需要新的要求和 --reason')
    service = CLIMissionService(repository.root)
    try:
        request_id = request_id or f'request_{uuid4().hex}'
        print(f'[创建请求] {request_id}', flush=True)
        _, identity, created = service.create_continuation(Continuation(
            parent_mission_id=parent.id, conversation_id=parent.conversation_id,
            request_id=request_id, reason=reason, content=user_input, operation=operation,
            include_report=operation == 'reevaluate'))
        print(f"[{'新建关联任务' if created else '已有创建结果'}] {identity} | 来源 {parent.id}")
        if not created:
            # Repeating a creation request never implicitly resumes a previously paused task.
            mission = service.store.get(identity)
            _show_mission(repository, mission)
            return mission
        return _drive_cli(service, identity)
    finally:
        service.close()


def cmd_cancel(mission_id, operation_id, run_id, pause_id=None, request_id=None, api_url=None):
    from backend.models import CancelRequest
    request = CancelRequest(request_id=request_id or f'cancel_{uuid4().hex}',
        expected_run_id=run_id, expected_pause_id=pause_id)
    print(f'[取消请求] {request.request_id} | {operation_id} | run={run_id}', flush=True)
    if api_url:
        import httpx
        response = httpx.post(api_url.rstrip('/') + f'/api/missions/{mission_id}/operations/{operation_id}/cancel',
            json=request.model_dump(), timeout=10)
        if response.status_code >= 400:
            raise ValueError(f'取消未接受（HTTP {response.status_code}）：{response.json().get("detail", "请求失败")}')
        result = response.json()
    else:
        from backend.cli import CLIMissionService
        service = CLIMissionService(SessionRepository().root)
        try:
            result = service.cancel(mission_id, operation_id, request).model_dump(mode='json')
        finally:
            service.close()
    print(f'[取消回执] {result["outcome"]} | {result["status"]} | cleanup={result["cleanup_status"]}'
        + (' | 原回执重放' if result['replayed'] else ''))
    return 0


def main():
    import argparse
    print_header()
    parser = argparse.ArgumentParser(description='持久化 CLI 任务与原暂停恢复')
    operations = parser.add_mutually_exclusive_group()
    operations.add_argument('--list', action='store_true')
    operations.add_argument('--show', metavar='MISSION_ID')
    operations.add_argument('--continue', dest='continue_id', metavar='MISSION_ID')
    operations.add_argument('--reevaluate', metavar='MISSION_ID')
    operations.add_argument('--retry', metavar='MISSION_ID')
    operations.add_argument('--explain', metavar='MISSION_ID')
    operations.add_argument('--cancel', metavar='MISSION_ID')
    parser.add_argument('--operation-id')
    parser.add_argument('--expected-run-id', type=int)
    parser.add_argument('--expected-pause-id')
    parser.add_argument('--api-url')
    parser.add_argument('--reason')
    parser.add_argument('--request-id')
    parser.add_argument('content', nargs='*')
    args = parser.parse_args()
    if (args.list or args.show) and (args.content or args.reason or args.request_id):
        parser.error('历史/恢复命令不接受新要求；请使用 --reevaluate 创建关联评估')
    if args.continue_id and (args.content or args.reason):
        parser.error('恢复命令不接受新要求；请使用 --reevaluate')
    if args.reason and not (args.reevaluate or args.retry):
        parser.error('--reason 只用于 --reevaluate/--retry')
    if args.cancel:
        if not args.operation_id or args.expected_run_id is None or args.content or args.reason:
            parser.error('--cancel需要--operation-id和--expected-run-id，不接受新要求或reason')
    elif args.operation_id or args.expected_run_id is not None or args.expected_pause_id or args.api_url:
        parser.error('取消身份和--api-url只用于--cancel')
    try:
        if args.cancel:
            return cmd_cancel(args.cancel, args.operation_id, args.expected_run_id, args.expected_pause_id,
                args.request_id, args.api_url)
        if args.list:
            cmd_list()
            return 0
        if args.show:
            cmd_show(args.show)
            return 0
        if args.continue_id:
            mission = cmd_continue(args.continue_id, args.request_id)
        elif args.explain:
            mission = cmd_explain(args.explain, ' '.join(args.content), args.request_id)
        else:
            user_input = ' '.join(args.content)
            if args.reevaluate or args.retry:
                if not check_config():
                    return 1
                mission = cmd_related(args.reevaluate or args.retry, user_input, args.reason,
                    args.request_id, operation='reevaluate' if args.reevaluate else 'retry')
            else:
                mission = cmd_new(user_input, args.request_id)
        return 1 if mission and mission.status in {'FAILED', 'CANCELLED'} else 0
    except (EOFError, KeyboardInterrupt):
        print('\n[中断] 可恢复暂停保留；运行中的中断不承诺自动恢复。')
        return 1
    except Exception as error:
        print(f'[Error] {error}')
        if config.DEBUG:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
