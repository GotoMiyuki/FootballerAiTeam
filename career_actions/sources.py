"""Privileged read-only specialist adapter; no report extraction or model/tool execution."""
from copy import deepcopy
from backend.task_context import input_reference
from execution_contracts import publishable
from output_validation import validate_specialist
from player_data.models import PlayerSnapshot, content_hash


class SourceUnavailable(ValueError):
    pass


class MissionResultReader:
    def __init__(self, service):
        self.service = service

    def read(self, mission_id):
        mission = self.service.store.get(mission_id)
        if self.service.demo or mission.status != 'COMPLETED':
            raise SourceUnavailable('演示或未完成任务不能提供已验证建议来源')
        try:
            from backend.runtime import inspect_checkpoint
            checkpoint = inspect_checkpoint(self.service.checkpoint_path, mission_id)
            return self.verify(mission, checkpoint.values if checkpoint else {})
        except SourceUnavailable:
            raise
        except Exception as error:
            raise SourceUnavailable('原专业成果 checkpoint 无法核验，历史报告仍可查看') from error

    def verify(self, mission, state, *, completing=False):
        try:
            if (self.service.demo or mission.input_reference.verification != 'VERIFIED'
                    or not mission.input_reference.context or mission.delivery_status != 'PUBLISHABLE'
                    or not mission.report or mission.status == 'FAILED'
                    or not mission.review or mission.review.availability != 'COMPLETED'
                    or mission.review.decision != 'PASS'
                    or not completing and mission.status != 'COMPLETED'
                    or not publishable(state)):
                raise SourceUnavailable('来源任务、有效成果或当前审查不满足建议发布条件')
            if (not mission.plan or mission.plan.version != state['plan']['version']
                    or mission.report.plan_version != state['plan']['version']
                    or mission.review.reviewed_versions != state['review_v2']['reviewed_versions']):
                raise SourceUnavailable('公开任务与专业成果审查版本不一致')
            if state.get('final_report') != self.service.store.report(mission.id):
                raise SourceUnavailable('checkpoint 与已发布任务报告不一致')
            inputs = self.service.store.inputs(mission.id)
            digest = inputs.pop('content_hash', None)
            if (inputs.get('schema_version') != 1 or inputs.get('mode') != 'live'
                    or not digest or content_hash(inputs) != digest
                    or inputs.get('player_snapshot') != state.get('player_snapshot')):
                raise SourceUnavailable('来源完整固定输入缺失或与专业成果不一致')
            fixed = PlayerSnapshot.from_dict(inputs['player_snapshot'])
            if input_reference(fixed) != mission.input_reference:
                raise SourceUnavailable('来源球员身份或快照版本不一致')
            tasks = []
            for task in state['plan']['subtasks']:
                if task['capability'] == 'skill_training':
                    result = state['subtask_results'][task['id']]
                    payload = result['observation']['result']
                    validate_specialist('skill_training', payload)
                    tasks.append({'task': deepcopy(task), 'result': deepcopy(result), 'payload': deepcopy(payload)})
            return {'mission': mission, 'state': state, 'snapshot': fixed, 'tasks': tasks}
        except SourceUnavailable:
            raise
        except Exception as error:
            raise SourceUnavailable('来源专业结构、固定输入或审查版本无法验证') from error
