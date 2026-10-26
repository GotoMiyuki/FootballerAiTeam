"""E2.2 read-only feedback adapter. C owns task creation and its idempotent transaction."""
from copy import deepcopy
import json
from backend.models import HistoryReference
from career_actions.models import ActionContext
from player_data.models import PlayerContext, content_hash
from player_data.observations import observation_bundle


def seal(packet):
    packet['content_hash'] = content_hash({key: value for key, value in packet.items() if key != 'content_hash'})
    return packet


def validate(packet, context, snapshot_id):
    if (not isinstance(packet, dict) or packet.get('schema_version') != 1
            or packet.get('context') != context or packet.get('submission_input_reference', {}).get('snapshot_id') != snapshot_id
            or packet.get('content_hash') != content_hash({key: value for key, value in packet.items() if key != 'content_hash'})):
        raise ValueError('原关联反馈缺失、版本或摘要不一致，不能读取最新来源替代')


def requested(request):
    return bool(request.recommendation_id or request.assessment_id or request.recommendation_revision is not None
        or request.assessment_revision is not None or request.execution_ids)


def freeze_feedback(service, parent, request, snapshot):
    if not requested(request):
        return None, []
    if service.demo or request.operation != 'reevaluate':
        raise ValueError('反馈关联需要已完成的真实任务，演示与失败重试不能冒充来源')
    if not request.recommendation_id or request.recommendation_revision is None:
        raise ValueError('反馈关联必须明确原建议id及版本')
    if bool(request.assessment_id) != (request.assessment_revision is not None):
        raise ValueError('评估id与版本必须一起指定')
    context = ActionContext(**parent.input_reference.context)
    rec = service.recommendations._action_recommendation(parent.id, request.recommendation_id,
        PlayerContext(**context.model_dump()), snapshot=snapshot)
    if rec.revision != request.recommendation_revision or rec.validity not in {'current', 'needs_reassessment'}:
        raise ValueError('建议版本已变化或来源失效，请刷新后重新选择')
    packet = dict(schema_version=1, context=context.model_dump(), parent_mission_id=parent.id,
        recommendation={'recommendation_id': rec.recommendation_id, 'revision': rec.revision,
            'source': rec.source.model_dump(), 'content': rec.content.model_dump(),
            'baseline_reference': rec.applicability.input_reference.model_dump(),
            'validity_at_submission': rec.validity, 'limitations': rec.content.limitations},
        assessment=None, decision=None, executions=[], limitations=[
            '这是提交时固定的历史反馈，不覆盖本次要求或授予工具/游戏操作权限',
            '选择与执行独立；自报反馈不是游戏核验，观测变化不证明建议效果',
            '新任务使用提交时最新A快照；不把原建议基线当作最新事实',
            '新建议从未选择/执行开始，不继承原采纳状态'])
    from backend.task_context import input_reference
    packet['submission_input_reference'] = input_reference(snapshot).model_dump()
    references = []
    original = next(record for record, _ in service.recommendations.repository.records(parent.id)
        if record.recommendation_id == rec.recommendation_id)
    references.append(HistoryReference(kind='recommendation', mission_id=parent.id,
        version=rec.revision, object_id=rec.recommendation_id, content_hash=content_hash(original.model_dump())))
    identities = list(request.execution_ids)
    if len(identities) != len(set(identities)) or any(not identity or len(identity) > 100 for identity in identities):
        raise ValueError('执行引用重复或身份不合法')
    if request.assessment_id:
        bundle = observation_bundle(service.player_repository, PlayerContext(**context.model_dump()))
        if bundle['snapshot'].metadata['snapshot_id'] != snapshot.metadata['snapshot_id']:
            raise ValueError('反馈核验期间A事实变化，请重新提交')
        comparison = service.assessments.view(parent.id, rec.recommendation_id, snapshot=snapshot, evidence_bundle=bundle)
        assessment = next((item for item in comparison.assessments if item.assessment_id == request.assessment_id
            and item.revision == request.assessment_revision), None)
        if not assessment or assessment.validity != 'current' or assessment.source != rec.source:
            raise ValueError('所选评估版本已失效或不属于原建议，请刷新比较后重新提交')
        raw = assessment.model_dump(exclude={'validity', 'validity_reason'})
        packet['assessment'] = {'assessment_id': assessment.assessment_id, 'revision': assessment.revision,
            'plan_id': assessment.plan_id, 'algorithm_version': assessment.algorithm_version,
            'baseline_reference': assessment.baseline_reference.model_dump(),
            'observed_snapshot_reference': deepcopy(assessment.observed_snapshot_reference),
            'result': assessment.result.model_dump(), 'source_content_hash': content_hash(raw),
            'validity_at_submission': 'current'}
        references.append(HistoryReference(kind='assessment', mission_id=parent.id, object_id=assessment.assessment_id,
            version=assessment.revision, content_hash=content_hash(raw)))
        resolved = assessment.result.execution.get('execution_id')
        if resolved and resolved not in identities:
            identities.append(resolved)
        if assessment.result.evidence_status != 'COMPLETE':
            packet['limitations'].append('比较证据不足或不可比，仅可按已知信息重新分析，不能宣称完成效果验证')
        packet['assessment']['latest_input_differs_from_assessment'] = (
            assessment.observed_snapshot_reference.get('snapshot_id') != snapshot.metadata['snapshot_id'])
    activity = service.recommendations.repository.activity(original)
    if activity.current_decision_id:
        decision = next(item for item in activity.decisions if item.decision_id == activity.current_decision_id)
        packet['decision'] = {**decision.model_dump(), 'reason': decision.reason[:1000],
            'source_content_hash': content_hash(decision.model_dump()), 'truncated': len(decision.reason) > 1000}
        references.append(HistoryReference(kind='decision', mission_id=parent.id, object_id=decision.decision_id,
            version=decision.revision, content_hash=content_hash(decision.model_dump())))
    by_id = {item.execution_id: item for item in activity.executions}
    if len(identities) > 8:
        raise ValueError('关联执行反馈超过8条预算，请减少所选引用')
    for identity in identities:
        item = by_id.get(identity)
        if (not item or identity not in activity.current_execution_ids or item.context != context
                or item.source != original.source or item.recommendation_revision != rec.revision):
            raise ValueError('执行反馈已被更正或不属于原建议，请刷新后重新选择')
        packet['executions'].append({**item.model_dump(), 'actual_action': item.actual_action[:1200],
            'correction_reason': item.correction_reason[:500], 'truncated': len(item.actual_action) > 1200 or len(item.correction_reason) > 500,
            'source_content_hash': content_hash(item.model_dump())})
        references.append(HistoryReference(kind='execution', mission_id=parent.id, object_id=identity,
            version=item.revision, content_hash=content_hash(item.model_dump())))
    if not packet['executions']:
        packet['limitations'].append('未关联执行反馈，执行保持未知')
    if len(json.dumps(packet, ensure_ascii=False)) > 28000:
        raise ValueError('所选反馈超过固定输入预算，请减少执行引用')
    return seal(packet), references


def summary(packet):
    assessment = packet['assessment']
    return dict(schema_version=1, content_hash=packet['content_hash'], context=packet['context'],
        recommendation_id=packet['recommendation']['recommendation_id'], recommendation_revision=packet['recommendation']['revision'],
        assessment_id=assessment['assessment_id'] if assessment else None,
        assessment_revision=assessment['revision'] if assessment else None,
        execution_ids=[item['execution_id'] for item in packet['executions']],
        decision_id=(packet['decision'] or {}).get('decision_id'),
        submission_input_reference=packet['submission_input_reference'])
