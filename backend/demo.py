"""Explicit deterministic mock scenarios. Never used by the live runtime."""
import time
from backend.models import PlanView, SubtaskView, ReviewView, BlockedView, RequiredInput

def run_demo(service, mission, scenario, resume):
    emit = service.emitter.emit
    status = service.emitter.status
    def pause():
        time.sleep(service.demo_delay)
    def agent_event(name, active, activity):
        agent = next(agent for agent in mission.agents if agent.name == name)
        agent.status, agent.activity = ('RUNNING' if active else 'COMPLETED'), activity
        emit(mission, 'agent.started' if active else 'agent.completed', agent=name)
    if not resume:
        emit(mission, 'mission.started')
        status(mission, 'PLANNING')
        mission.agents[0].status, mission.agents[0].activity = 'RUNNING', '制定执行计划'
        emit(mission, 'agent.started', agent='Manager')
        pause()
        mission.plan = PlanView(objective=mission.objective, subtasks=[
            SubtaskView(id='subtask_01', title='梳理球员档案与近期记录', assigned_agent='Analyst'),
            SubtaskView(id='subtask_02', title='评估当前状态与信息缺口', assigned_agent='Analyst'),
            SubtaskView(id='subtask_03', title='整理可执行方案', assigned_agent='Coach'),
        ])
        mission.agents[0].status, mission.agents[0].activity = 'COMPLETED', '计划已建立'
        emit(mission, 'plan.created', **mission.plan.model_dump())
        emit(mission, 'agent.completed', agent='Manager')
    if scenario == 'blocked':
        mission.blocked = BlockedView(reason='missing_user_input', message='演示：需要当前恢复反馈，任务会等待你的补充。', required_inputs=[
            RequiredInput(key='doms', label='肌肉酸痛程度', input_type='scale', min=0, max=10),
            RequiredInput(key='sleep_hours', label='昨晚睡眠（小时）', input_type='number', min=0, max=24),
            RequiredInput(key='fatigue', label='疲劳程度', input_type='scale', min=0, max=10),
            RequiredInput(key='pain', label='是否疼痛', input_type='boolean'),
            RequiredInput(key='availability', label='今天可用时间', input_type='single_select', options=['30 分钟', '60 分钟', '暂不训练']),
            RequiredInput(key='notes', label='其他补充', input_type='text', required=False),
        ])
        review = ReviewView(availability='COMPLETED', decision='BLOCKED', summary='当前恢复反馈缺失', affected_subtasks=['subtask_03'])
        mission.review = review
        mission.review_history.append(review)
        emit(mission, 'review.completed', **review.model_dump())
        status(mission, 'BLOCKED')
        emit(mission, 'mission.blocked', **mission.blocked.model_dump())
        return
    status(mission, 'RUNNING')
    for task in mission.plan.subtasks:
        if task.status == 'COMPLETED':
            continue
        task.status = 'RUNNING'
        agent = next(agent for agent in mission.agents if agent.name == task.assigned_agent)
        agent.status, agent.activity = 'RUNNING', task.title
        emit(mission, 'agent.started', agent=agent.name)
        emit(mission, 'subtask.started', subtask_id=task.id)
        pause()
        task.status = 'COMPLETED'
        agent.status, agent.activity = 'COMPLETED', task.title + ' · 完成'
        emit(mission, 'subtask.completed', subtask_id=task.id)
        emit(mission, 'agent.completed', agent=agent.name)
    if scenario in {'revision', 'replan'}:
        status(mission, 'REVIEWING')
        agent_event('Reviewer', True, '正在审查方案')
        emit(mission, 'review.started')
        decision = 'REVISE' if scenario == 'revision' else 'REPLAN'
        review = ReviewView(availability='COMPLETED', decision=decision, summary='演示：执行安排存在时间冲突' if decision == 'REVISE' else '演示：可用训练时间与原计划假设不一致', affected_subtasks=['subtask_03'], severity='MEDIUM')
        mission.review = review
        mission.review_history.append(review)
        emit(mission, 'review.completed', **review.model_dump())
        agent_event('Reviewer', False, '已提出审查意见')
        task = mission.plan.subtasks[-1]
        task.status = 'REVISION_REQUIRED'
        emit(mission, 'subtask.revision_required', subtask_id=task.id)
        pause()
        kind = 'revision' if decision == 'REVISE' else 'replan'
        status(mission, 'REVISING' if kind == 'revision' else 'REPLANNING')
        emit(mission, kind + '.started')
        pause()
        if kind == 'replan':
            mission.plan.version += 1
            mission.plan.reason = '可用时间改变，重新制定计划'
            task.title = '根据新时间约束重新整理方案'
        else:
            task.revision_count += 1
        task.status = 'RUNNING'
        emit(mission, 'plan.updated', **mission.plan.model_dump())
        emit(mission, 'subtask.started', subtask_id=task.id)
        pause()
        task.status = 'COMPLETED'
        emit(mission, 'subtask.completed', subtask_id=task.id)
        emit(mission, kind + '.completed')
    status(mission, 'REVIEWING')
    agent_event('Reviewer', True, '正在审查完整计划')
    emit(mission, 'review.started')
    pause()
    mission.review = ReviewView(availability='COMPLETED', decision='PASS', summary='演示方案已通过流程审查')
    mission.review_history.append(mission.review)
    emit(mission, 'review.completed', **mission.review.model_dump())
    agent_event('Reviewer', False, '方案已通过审查')
    status(mission, 'RUNNING')
    agent_event('Document', True, '正在整理完整报告')
    pause()
    agent_event('Document', False, '报告已准备就绪')
    service.complete(mission, f'''# {mission.title}

> 演示报告：用于验证工作台交互，不是模型生成的训练建议。

## 任务目标
{mission.objective}

## 工作过程
- Manager 建立计划 v{mission.plan.version}。
- Analyst 梳理球员状态和历史记录。
- Coach 整理执行方案。
- Reviewer 审查并通过。

## 后续行动
切换到真实后端运行任务，即可获得现有 Agent 团队生成的完整报告。

## 任务记录
任务编号：{mission.id}
''')
