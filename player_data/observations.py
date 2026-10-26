"""Read-only, coherent observation evidence; raw paths and import tokens stay private."""
from copy import deepcopy
from player_data.models import PlayerContext, PlayerSnapshot, PlayerDataError
from player_data.models import content_hash


def observation_bundle(repository, context):
    reader = getattr(repository, 'read_observations', None)
    if not callable(reader):
        raise PlayerDataError('unsupported_evidence', '当前Repository没有可核验观察；样本不能充当实际比较证据')
    bundle = reader(context)
    if bundle['snapshot'].to_dict()['context'] != context.to_dict():
        raise PlayerDataError('invalid_identity', '观察与请求context不一致')
    return bundle


def from_state(state):
    context = PlayerContext(**state['context'])
    sources = {row['key']: {key: deepcopy(row.get(key)) for key in (
        'observation_id', 'provider', 'source_type', 'source_record_id', 'game_version',
        'adapter_version', 'effective_at', 'time_domain', 'time_precision')}
        for row in state['observations']}
    snapshot = PlayerSnapshot(context, state['profile'], state['training'], state['matches'], state['career'],
        {'schema_version': state['schema_version'], 'state_version': state['state_version'],
         'snapshot_id': state['snapshot_id'], 'origins': state['origins'], 'sources': sources})
    rows = []
    for row in state['observations']:
        from player_data.imports import Observation
        from player_data.schema import validate_record
        semantic = {key: row.get(key) for key in Observation.__dataclass_fields__ if key not in {'collected_at', 'expected_state_version'}}
        if (semantic['context'] != state['context'] or content_hash(semantic) != row['semantic_hash']
                or row.get('import_status') not in {'APPLIED', 'RECORDED_ONLY'}):
            raise PlayerDataError('corrupt_evidence', '观察身份、内容或应用状态无法核验')
        validate_record(row['kind'], row['payload'])
        item = {key: deepcopy(row.get(key)) for key in (
            'key', 'semantic_hash', 'observation_id', 'provider', 'source_type', 'source_record_id',
            'game_version', 'adapter_version', 'effective_at', 'time_domain', 'time_precision',
            'kind', 'payload', 'import_status')}
        item['corrects_key'] = f"{row['provider']}:{row['corrects_observation_id']}" if row.get('corrects_observation_id') else None
        item['current_paths'] = [path for path, key in state['origins'].items() if key == row['key']]
        rows.append(item)
    return {'snapshot': snapshot, 'observations': rows}
