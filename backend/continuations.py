"""Application-level continuation requests and bounded, source-checked historical input."""
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import json

from backend.models import HistoryReference
from player_data.models import PlayerContext, content_hash


@dataclass(frozen=True)
class Continuation:
    parent_mission_id: str
    conversation_id: str
    request_id: str
    reason: str
    content: str
    operation: str = 'reevaluate'
    player_context: dict | None = None
    include_report: bool = True
    selected_results: list[dict] = field(default_factory=list)
    recommendation_id: str | None = None
    assessment_id: str | None = None

    def validated(self):
        from backend.models import ContinuationRequest
        return ContinuationRequest.model_validate(asdict(self))


def select_history(service, parent, request):
    reference = parent.input_reference
    expected = 'DEMO' if service.demo else 'VERIFIED'
    if reference.verification != expected or not reference.context:
        raise ValueError('原任务没有可验证的同模式球员上下文，请新建普通任务')
    context = PlayerContext(**reference.context)
    if request.player_context and request.player_context.model_dump() != context.to_dict():
        raise ValueError('关联评估不能切换生涯、分支或球员，请新建普通任务')
    if request.recommendation_id or request.assessment_id:
        raise ValueError('建议/评估记录解析尚未接通，不能把未验证引用作为历史材料')
    projection = {'parent_mission_id': parent.id, 'operation': request.operation, 'reason': request.reason.strip(),
        'original_objective': parent.objective, 'new_requirements': request.content.strip(),
        'parent_input_reference': reference.model_dump(mode='json'), 'results': []}
    references = []
    if request.include_report:
        report = service.store.report(parent.id)
        if parent.status != 'COMPLETED' or parent.delivery_status != 'PUBLISHABLE' or not parent.report or not report:
            raise ValueError('来源任务没有可引用的已发布报告')
        digest = content_hash(report)
        projection['report'] = {'text': report[:12000], 'truncated': len(report) > 12000,
            'source_version': parent.report.plan_version, 'content_hash': digest}
        references.append(HistoryReference(kind='report', mission_id=parent.id,
            version=parent.report.plan_version, content_hash=digest))
    seen = set()
    if request.selected_results:
        if service.demo:
            raise ValueError('演示专业成果不作为已验证结果导入，请仅选择演示报告')
        from backend.runtime import inspect_checkpoint
        from execution_context import valid_result, PAYLOAD_FIELDS
        try:
            checkpoint = inspect_checkpoint(service.checkpoint_path, parent.id)
        except Exception as error:
            raise ValueError('原专业成果无法核验，请取消该引用或选择原报告') from error
        state = checkpoint.values if checkpoint else {}
        original = state.get('player_snapshot') or {}
        if (original.get('context') != reference.context
                or (original.get('metadata') or {}).get('snapshot_id') != reference.snapshot_id
                or (original.get('metadata') or {}).get('state_version') != reference.state_version):
            raise ValueError('原专业成果与来源输入不一致，无法引用')
        tasks = {task['id']: task for task in (state.get('plan') or {}).get('subtasks', [])}
        for selected in request.selected_results:
            if selected.subtask_id in seen:
                raise ValueError('不能重复选择同一专业成果')
            seen.add(selected.subtask_id)
            if not valid_result(state, selected.subtask_id, check_fingerprint=True):
                raise ValueError('所选专业成果缺失、失败或已失效')
            result = state['subtask_results'][selected.subtask_id]
            if result['source_version'] != selected.version:
                raise ValueError('所选专业成果版本已变化，请刷新后重新选择')
            task = tasks[selected.subtask_id]
            payload = (result.get('observation') or {}).get('result')
            if not isinstance(payload, dict) or task['capability'] not in PAYLOAD_FIELDS:
                raise ValueError('所选专业成果没有可引用的有效内容')
            material = {'subtask_id': selected.subtask_id, 'capability': task['capability'], 'source_version': selected.version,
                'payload': {key: deepcopy(value) for key, value in payload.items() if key in PAYLOAD_FIELDS[task['capability']]},
                'evidence': deepcopy(result.get('evidence') or []), 'limitations': deepcopy(result.get('uncertainties') or []),
                'input_reference': deepcopy(result.get('input_reference') or {})}
            digest = content_hash(material)
            projection['results'].append({**material, 'content_hash': digest})
            references.append(HistoryReference(kind='result', mission_id=parent.id, subtask_id=selected.subtask_id,
                version=selected.version, content_hash=digest))
    if len(json.dumps(projection, ensure_ascii=False)) > 32000:
        raise ValueError('所选历史材料超过支持的输入预算，请减少引用')
    return context, projection, references
