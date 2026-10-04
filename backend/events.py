"""The only translation boundary between internal workflow and public UI events."""
from datetime import datetime, timezone
from uuid import uuid4
from backend.models import MissionEvent, PlanView, ReviewView, BlockedView, RequiredInput

def now():
    return datetime.now(timezone.utc).isoformat()


def result_summary(result):
    if result.get('validated') is not True or result.get('validity') != 'CURRENT':
        return None
    payload = (result.get('observation') or {}).get('result')
    if not isinstance(payload, dict):
        return None
    parts = [payload.get('summary'), payload.get('form_assessment')]
    for key in ('focus_areas', 'recommendations', 'notes'):
        value = payload.get(key)
        if isinstance(value, list):
            parts.extend(item for item in value if isinstance(item, str))
        elif isinstance(value, str):
            parts.append(value)
    return '\n'.join(part for part in parts if isinstance(part, str) and part.strip())[:2000] or '已形成有效专业结果，等待整体交付。'

class MissionEventEmitter:
    def __init__(self, store):
        self.store = store

    def emit(self, mission, event_type, **data):
        # Snapshot and sequence commit together; clients can always recover by HTTP.
        with self.store.lock:
            event = self.prepare(mission, event_type, **data)
            self.store.save(mission, event)
        return event

    def prepare(self, mission, event_type, **data):
        """Prepare for the Store's atomic creation transaction, or an ordinary emit."""
        mission.sequence += 1
        return MissionEvent(event_id=f'evt_{uuid4().hex}', type=event_type, mission_id=mission.id,
            timestamp=now(), sequence=mission.sequence,
            data={**data, 'snapshot': mission.model_dump(mode='json')})

    def status(self, mission, status):
        if mission.status != status:
            mission.status = status
            self.emit(mission, 'mission.status_changed', status=status)

    def started(self, mission, node, state):
        from registry import NODE_TO_DISPLAY
        statuses = {'manager': 'PLANNING', 'reviewer': 'REVIEWING', 'manager_revision': 'REVISING', 'manager_replan': 'REPLANNING'}
        if node == 'human_input':
            return
        self.status(mission, statuses.get(node, 'RUNNING'))
        agent_name = NODE_TO_DISPLAY.get(node, 'Reviewer' if node == 'reviewer' else 'Manager')
        activities = {'manager': '正在制定任务计划', 'reviewer': '正在审查方案', 'manager_revision': '正在局部修订', 'manager_replan': '正在重新规划', 'document': '正在整理完整报告'}
        task_id = state.get('current_subtask')
        task_title = next((task.title for task in mission.plan.subtasks if task.id == task_id), '') if mission.plan else ''
        activity = activities.get(node, task_title or '正在执行计划')
        for agent in mission.agents:
            if agent.name == agent_name:
                agent.status, agent.activity = 'RUNNING', activity
        event_type = {'reviewer': 'review.started', 'manager_revision': 'revision.started', 'manager_replan': 'replan.started'}.get(node, 'agent.started')
        self.emit(mission, event_type, agent=agent_name, activity=activity)
        if mission.plan and node in NODE_TO_DISPLAY and node != 'document':
            for task in mission.plan.subtasks:
                if task.id == task_id:
                    task.status = 'RUNNING'
                    self.emit(mission, 'subtask.started', subtask_id=task_id)

    def adapt(self, mission, state):
        raw_plan = state.get('plan_v2') or state.get('plan') or {}
        tasks = state.get('subtasks') or raw_plan.get('subtasks') or []
        if raw_plan:
            plan = PlanView(version=int(raw_plan.get('version') or state.get('plan_version') or 1),
                objective=str(raw_plan.get('objective') or mission.objective),
                reason=str(state.get('replan_reason') or ''), subtasks=[{
                    'id': task['id'], 'title': task.get('objective') or task.get('goal') or task['id'],
                    'status': {'NEEDS_REVISION': 'REVISION_REQUIRED', 'NO_RESULT': 'FAILED'}.get(str(task.get('status', 'PENDING')).upper(), str(task.get('status', 'PENDING')).upper()),
                    'assigned_agent': task.get('assigned_agent') or 'Manager',
                    'revision_count': task.get('revision_count', 0),
                    'result_summary': result_summary((state.get('subtask_results') or {}).get(task['id'], {})),
                    'result_version': (state.get('subtask_results') or {}).get(task['id'], {}).get('source_version'),
                    'reason': str(task.get('blocked_reason') or '')[:500],
                } for task in tasks])
            old = mission.plan
            if old != plan:
                mission.plan = plan
                self.emit(mission, 'plan.created' if old is None else 'plan.updated', **plan.model_dump())
                previous = {task.id: task.status for task in old.subtasks} if old else {}
                for task in plan.subtasks:
                    kind = {'COMPLETED': 'subtask.completed', 'REVISION_REQUIRED': 'subtask.revision_required', 'FAILED': 'subtask.failed', 'INVALIDATED': 'subtask.invalidated'}.get(task.status)
                    if kind and previous.get(task.id) != task.status:
                        self.emit(mission, kind, subtask_id=task.id)
        raw_review = state.get('review_v2') or {}
        if raw_review:
            review = ReviewView(availability=raw_review.get('availability', 'NOT_RUN'),
                decision=raw_review.get('decision') if raw_review.get('availability') == 'COMPLETED' else None,
                scope=raw_review.get('scope', 'specialist_inputs'), reviewed_versions=raw_review.get('reviewed_versions', {}), summary=raw_review.get('summary', ''),
                affected_subtasks=list(dict.fromkeys(t for f in raw_review.get('findings', []) if f.get('action') != 'KEEP' for t in f.get('subtask_ids', []))),
                severity=next((f.get('severity', 'INFO') for f in raw_review.get('findings', [])), 'INFO'))
            if review != mission.review:
                mission.review = review
                mission.review_history.append(review)
                kind = {'COMPLETED': 'review.completed', 'UNAVAILABLE': 'review.unavailable', 'NOT_RUN': 'review.invalidated'}[review.availability]
                self.emit(mission, kind, **review.model_dump())
        mission.delivery_status = state.get('delivery_status', 'NOT_GENERATED')
        mission.body_validation = state.get('body_validation') or {}
        if mission.delivery_status != 'PUBLISHABLE':
            mission.report = None
            mission.result = None
        raw_telemetry = state.get('telemetry') or {}
        mission.telemetry = {key: value for key, value in raw_telemetry.items() if isinstance(value, (int, float))}
        for agent in mission.agents:
            if agent.status == 'RUNNING':
                failed = (state.get('execution_outcome') in {'FAILED', 'NO_RESULT'}
                          or agent.name == 'Reviewer' and raw_review.get('availability') == 'UNAVAILABLE')
                agent.status, agent.activity = ('FAILED', '未输出结果') if failed else ('COMPLETED', '本轮工作完成')
                self.emit(mission, 'agent.failed' if failed else 'agent.completed', agent=agent.name)
        self.store.save(mission)

    def blocked(self, mission, information, *, approval=False):
        inputs = [RequiredInput(key='approved', label='我已查看计划，继续生成报告', input_type='boolean')] if approval else [RequiredInput(key='information', label='请补充以上信息', input_type='text')]
        mission.blocked = BlockedView(reason='report_approval' if approval else 'missing_user_input',
            message='计划已完成审查，请确认是否继续生成报告。' if approval else '\n'.join(str(item) for item in information) or '请补充任务所需的当前情况。', required_inputs=inputs)
        self.status(mission, 'BLOCKED')
        self.emit(mission, 'mission.blocked', **mission.blocked.model_dump())
