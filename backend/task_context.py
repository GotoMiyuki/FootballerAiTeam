"""Public input identity and pause checks; never expose checkpoint contents or paths."""
from backend.models import PlayerInputReference


def input_reference(snapshot):
    metadata = snapshot.metadata
    kinds = {metadata.get('source_type')}
    kinds.update(source.get('source_type') for source in metadata.get('sources', {}).values())
    return PlayerInputReference(
        verification='VERIFIED', context=snapshot.to_dict()['context'],
        state_version=metadata.get('state_version'), snapshot_id=metadata.get('snapshot_id'),
        source_types=sorted(kinds & {'demo_fixture', 'game_observation', 'user_confirmed'}))


def validate_inputs(mission, inputs, *, mode='live'):
    from player_data.models import PlayerSnapshot, content_hash
    if not isinstance(inputs, dict):
        raise ValueError('任务固定输入结构不兼容，无法恢复')
    material = dict(inputs)
    digest = material.pop('content_hash', None)
    if not digest or content_hash(material) != digest:
        raise ValueError('任务固定输入完整性检查失败')
    if material.get('schema_version') != 1 or material.get('mode') != mode:
        raise ValueError('任务固定输入版本或模式不兼容，不能读取当前数据代替')
    if mode == 'live':
        try:
            reference = input_reference(PlayerSnapshot.from_dict(material['player_snapshot']))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError('任务固定球员快照缺失或不兼容') from error
        if reference != mission.input_reference:
            raise ValueError('任务固定输入与公开版本引用不一致')
    return material


def validate_pause(mission, snapshot, *, reason=None, inputs=None):
    if not snapshot or not snapshot.values:
        raise ValueError('原 checkpoint 不存在，无法恢复。请新建任务。')
    expected = {'missing_user_input': 'human_input', 'report_approval': 'document'}.get(reason or mission.blocked.reason)
    if not expected or tuple(snapshot.next) != (expected,):
        raise ValueError('checkpoint 与当前等待原因不兼容，无法恢复。请新建任务。')
    if inputs is not None:
        for key in ('player_snapshot', 'continuation_context', 'career_memory_context'):
            if (snapshot.values.get(key) or {}) != (inputs.get(key) or {}):
                raise ValueError('checkpoint 固定输入与原任务不一致，不能以最新材料补齐')
        user_context = snapshot.values.get('user_context')
        if not isinstance(user_context, dict) or user_context.get('request') != mission.objective:
            raise ValueError('checkpoint 与原任务目标不一致，无法恢复')
    reference = mission.input_reference
    original = snapshot.values.get('player_snapshot') or {}
    from player_data.models import PlayerSnapshot
    try:
        PlayerSnapshot.from_dict(original)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('原球员快照缺失或不兼容，无法恢复。请新建任务。') from error
    metadata = original.get('metadata') or {}
    if (reference.verification != 'VERIFIED' or not reference.state_version or not reference.snapshot_id
            or original.get('context') != reference.context
            or metadata.get('state_version') != reference.state_version
            or metadata.get('snapshot_id') != reference.snapshot_id):
        raise ValueError('原球员快照缺失或版本不兼容，无法恢复。请新建任务。')
    memory = snapshot.values.get('career_memory_context') or {}
    if memory or mission.memory_reference.verification != 'LEGACY_NONE':
        from career_memory.context import validate
        validate(memory, reference.context)
        if mission.memory_reference.verification == 'LEGACY_NONE' or not memory or memory.get('content_hash') != mission.memory_reference.content_hash:
            raise ValueError('原checkpoint固定记忆缺失或与原任务不一致，不能读取最新记忆恢复')
    feedback = (snapshot.values.get('continuation_context') or {}).get('career_feedback')
    public = mission.lineage.feedback_reference if mission.lineage else None
    if feedback or public:
        from career_actions.followups import validate, summary
        validate(feedback, reference.context, reference.snapshot_id)
        if not public or summary(feedback) != public:
            raise ValueError('原checkpoint固定关联反馈与原任务不一致')
