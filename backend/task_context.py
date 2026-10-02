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


def validate_pause(mission, snapshot, *, reason=None):
    if not snapshot or not snapshot.values:
        raise ValueError('原 checkpoint 不存在，无法恢复。请新建任务。')
    expected = {'missing_user_input': 'human_input', 'report_approval': 'document'}.get(reason or mission.blocked.reason)
    if not expected or tuple(snapshot.next) != (expected,):
        raise ValueError('checkpoint 与当前等待原因不兼容，无法恢复。请新建任务。')
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
