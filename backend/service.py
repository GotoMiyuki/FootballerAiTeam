import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from backend.events import MissionEventEmitter, now
from backend.models import AgentActivity, MissionView, MessageView, ReportSummary
from backend.store import Store

AGENTS = ['Manager', 'Analyst', 'Coach', 'Nutrition', 'Career', 'Reviewer', 'Document']

class MissionService:
    def __init__(self, data_dir: Path, *, demo=False, demo_delay=0.5):
        self.store = Store(data_dir / 'workspace.sqlite3')
        self.checkpoint_path = data_dir / 'checkpoints.sqlite3'
        self.emitter = MissionEventEmitter(self.store)
        self.demo, self.demo_delay = demo, demo_delay
        # Agent tools write player memory; serialize execution in this single-player V0.1.
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='mission')
        self.active: set[str] = set()
        self.lock = threading.RLock()
        for mission in self.store.missions():
            if mission.status not in {'COMPLETED', 'FAILED', 'BLOCKED'}:
                self.fail(mission, '服务重启中断了执行。已保留任务和计划，请创建新任务重试。')

    def message(self, mission, role, content):
        message = MessageView(id=f'msg_{uuid4().hex}', role=role, content=content,
            created_at=now(), mission_id=mission.id)
        self.store.add_message(mission.conversation_id, message)
        return message.id

    def submit(self, request):
        with self.lock:
            if request.intent == 'followup':
                if not request.mission_id:
                    raise ValueError('追问需要选择已有任务')
                mission = self.store.get(request.mission_id)
                if mission.conversation_id != request.conversation_id:
                    raise ValueError('会话与任务不匹配')
                if mission.id in self.active:
                    raise ValueError('任务正在执行，请等待完成或补充信息提示')
                if mission.status == 'BLOCKED':
                    if mission.blocked.reason != 'missing_user_input':
                        raise ValueError('请使用确认表单继续生成报告')
                    message_id = self.message(mission, 'user', request.content)
                    self._resume(mission, {'information': request.content})
                elif mission.status == 'COMPLETED':
                    message_id = self.message(mission, 'user', request.content)
                    self._enqueue(mission.id, self._followup, request.content)
                else:
                    raise ValueError('当前任务暂时无法追问，请创建新任务')
                return message_id, mission.id
            mission = MissionView(id=f'mission_{uuid4().hex}', conversation_id=request.conversation_id,
                title=request.content[:48], objective=request.content, created_at=now(),
                agents=[AgentActivity(name=name) for name in AGENTS])
            message_id = self.message(mission, 'user', request.content)
            self.emitter.emit(mission, 'mission.created')
            self._enqueue(mission.id, self._execute, None, request.demo_scenario)
            return message_id, mission.id

    def _enqueue(self, mission_id, fn, *args):
        self.active.add(mission_id)
        def work():
            try:
                fn(mission_id, *args)
            except Exception:
                logging.exception('Mission execution failed: %s', mission_id)
                mission = self.store.get(mission_id)
                if fn == self._followup:
                    self.message(mission, 'system', '追问暂时失败，请稍后重试。已有报告仍可查看。')
                    self.emitter.emit(mission, 'result.created')
                else:
                    self.fail(mission, '任务执行失败，请检查后端日志和模型配置后创建新任务重试。')
            finally:
                with self.lock:
                    self.active.discard(mission_id)
        self.executor.submit(work)

    def input(self, mission_id, values):
        with self.lock:
            mission = self.store.get(mission_id)
            if mission_id in self.active or mission.status != 'BLOCKED' or not mission.blocked:
                raise ValueError('任务当前没有等待输入，或已在恢复执行')
            fields = mission.blocked.required_inputs
            allowed = {field.key for field in fields}
            if set(values) - allowed:
                raise ValueError('输入包含未知字段')
            for field in fields:
                value = values.get(field.key)
                if field.required and (value is None or value == ''):
                    raise ValueError(f'请填写{field.label}')
                if value is None or value == '':
                    continue
                if field.input_type in {'number', 'scale'}:
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        raise ValueError(f'{field.label}必须为数值')
                    if (field.min is not None and value < field.min) or (field.max is not None and value > field.max):
                        raise ValueError(f'{field.label}超出允许范围')
                if field.input_type == 'boolean' and not isinstance(value, bool):
                    raise ValueError(f'{field.label}必须为是或否')
                if field.input_type == 'single_select' and value not in field.options:
                    raise ValueError(f'{field.label}不是有效选项')
                if field.input_type == 'text' and (not isinstance(value, str) or not value.strip() or len(value) > 8000):
                    raise ValueError(f'请填写有效的{field.label}')
            if mission.blocked.reason == 'report_approval' and values.get('approved') is not True:
                raise ValueError('确认后才能继续生成报告')
            def readable(value):
                if isinstance(value, bool):
                    return '是' if value else '否'
                if isinstance(value, float):
                    return f'{value:g}'
                return str(value)
            content = '\n'.join(f'{field.label}：{readable(values[field.key])}' for field in fields if field.key in values and values[field.key] != '')
            self.message(mission, 'user', content)
            self._resume(mission, {'information': content, 'values': values})
            return mission.id

    def _resume(self, mission, supplied):
        approval = mission.blocked.reason == 'report_approval'
        mission.blocked = None
        self.emitter.status(mission, 'RUNNING')
        self._enqueue(mission.id, self._execute, None if approval else supplied, 'resume', True)

    def _execute(self, mission_id, supplied=None, scenario='pass', resume=False):
        mission = self.store.get(mission_id)
        if self.demo:
            from backend.demo import run_demo
            run_demo(self, mission, scenario, resume)
            return
        from backend.runtime import create_runtime
        from graph import create_initial_state
        from langgraph.types import Command
        last_node = [None]
        def on_start(node, state):
            last_node[0] = node
            self.emitter.started(mission, node, state)
        graph, connection = create_runtime(self.checkpoint_path, on_start)
        config = {'configurable': {'thread_id': mission.id}, 'recursion_limit': 100}
        try:
            if not resume:
                self.emitter.emit(mission, 'mission.started')
            graph_input = Command(resume=supplied) if supplied else None if resume else create_initial_state(mission.objective)
            for state in graph.stream(graph_input, config, stream_mode='values'):
                # The first values chunk is the input/checkpoint, not node completion.
                if last_node[0]:
                    self.emitter.adapt(mission, state)
                    kind = {'manager_revision': 'revision.completed', 'manager_replan': 'replan.completed'}.get(last_node[0])
                    if kind:
                        self.emitter.emit(mission, kind)
                    last_node[0] = None
            snapshot = graph.get_state(config)
            state = snapshot.values
            self.emitter.adapt(mission, state)
            if snapshot.next:
                if 'human_input' in snapshot.next:
                    information = (state.get('review_v2') or {}).get('blocking_information', [])
                    self.emitter.blocked(mission, information)
                elif 'document' in snapshot.next:
                    self.emitter.blocked(mission, [], approval=True)
                else:
                    self.fail(mission, '任务暂停在无法恢复的状态，请检查后端日志。')
            elif state.get('final_report'):
                self.complete(mission, state['final_report'])
            else:
                self.fail(mission, '工作流结束但没有生成报告。请检查模型输出和任务约束。')
        finally:
            connection.close()

    def complete(self, mission, report):
        self.store.set_report(mission.id, report)
        # Extract only report prose, never internal agent messages or tool logs.
        prose = [line.strip() for line in report.splitlines() if line.strip() and not line.lstrip().startswith(('#', '>', '|', '```', '---', '任务编号'))]
        summary = re.sub(r'[*`]', '', '\n'.join(prose[:4]))[:450].strip()
        mission.result = f'已完成「{mission.title}」。\n{summary}\n\n完整结论与执行安排请查看报告。'
        mission.report = ReportSummary(title=mission.title, plan_version=mission.plan.version if mission.plan else 1)
        mission.blocked = None
        self.message(mission, 'assistant', mission.result)
        self.emitter.emit(mission, 'result.created', summary=mission.result)
        self.emitter.emit(mission, 'report.created', **mission.report.model_dump())
        self.emitter.status(mission, 'COMPLETED')
        self.emitter.emit(mission, 'mission.completed')

    def fail(self, mission, reason):
        mission.error = reason
        for agent in mission.agents:
            if agent.status == 'RUNNING':
                agent.status, agent.activity = 'FAILED', '执行中断'
        self.emitter.status(mission, 'FAILED')
        self.emitter.emit(mission, 'mission.failed', message=reason)

    def _followup(self, mission_id, question):
        mission = self.store.get(mission_id)
        if self.demo:
            answer = '这是演示模式的追问回复。追问保留在当前 Mission 中；真实模式会结合本任务报告与近期对话回答，不会把其他历史任务传给模型。'
        else:
            from utils.helpers import create_llm
            from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
            # No other Mission history, graph outputs, hidden reasoning or tool logs.
            report = self.store.report(mission_id) or ''
            messages = [SystemMessage(content=f'你是足球工作台助手。只回答当前任务的追问，不创建或重跑任务。根据以下报告回答，未知信息明确说明。报告：\n{report[:24000]}')]
            recent = [message for message in self.store.messages(mission.conversation_id) if message.mission_id == mission.id and message.role != 'system'][-6:]
            messages.extend((HumanMessage if message.role == 'user' else AIMessage)(content=message.content) for message in recent)
            answer = create_llm().invoke(messages).content
            if not isinstance(answer, str):
                raise ValueError('Unsupported model response')
        self.message(mission, 'assistant', answer)
        self.emitter.emit(mission, 'result.created')

    def close(self):
        self.executor.shutdown(wait=True)
        self.store.close()
