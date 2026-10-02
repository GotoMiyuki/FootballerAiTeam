"""One authoritative aggregate per context; single writer process only."""
from copy import deepcopy
from pathlib import Path
from player_data.models import PlayerContext, PlayerSnapshot, PlayerDataError, SCHEMA_VERSION, content_hash
from player_data.repository import contained_path, read_json
from player_data.schema import validate_profile, validate_record, validate_career

EMPTY_VERSION = 'EMPTY'


def state_digest(state):
    return content_hash({k: v for k, v in state.items() if k not in {'state_version', 'snapshot_id'}})


class JsonPlayerRepository:
    def __init__(self, root, *, fixture_root, context=None):
        self.root = Path(root).resolve()
        fixture = Path(fixture_root).resolve()
        if self.root.is_relative_to(fixture) or fixture.is_relative_to(self.root):
            raise PlayerDataError('configuration', 'Actual and fixture roots must be separate and non-overlapping')
        self.context = context or PlayerContext('actual-career', 'main', 'actual-player')

    def state_path(self, context):
        # IDs validate before path creation; resolve also blocks symlink escape.
        PlayerContext(**context.to_dict())
        return contained_path(self.root, 'contexts', context.career_id, context.branch_id, context.player_id, 'state.json')

    def read_state(self, context, *, allow_empty=False):
        path = self.state_path(context)
        if allow_empty and not path.exists():
            return {'schema_version': SCHEMA_VERSION, 'context': context.to_dict(), 'revision': 0,
                    'state_version': EMPTY_VERSION, 'snapshot_id': None, 'profile': {},
                    'training': [], 'matches': [], 'career': {}, 'origins': {}, 'observations': [], 'import_index': {}}
        state = read_json(path, dict)
        try:
            if state['schema_version'] != SCHEMA_VERSION or state['context'] != context.to_dict():
                raise ValueError('Incompatible state identity/schema')
            checksum = state_digest(state)
            if state['snapshot_id'] != checksum or state['state_version'] != f"{state['revision']}:{checksum}":
                raise ValueError('State integrity mismatch')
            validate_profile(state['profile'])
            for kind, name in (('training_record', 'training'), ('match_record', 'matches')):
                if not isinstance(state[name], list):
                    raise ValueError('Invalid history')
                for row in state[name]:
                    validate_record(kind, row)
            for key, shape in (('career', dict), ('origins', dict), ('observations', list), ('import_index', dict)):
                if not isinstance(state[key], shape):
                    raise ValueError('Invalid state container')
            if isinstance(state['revision'], bool) or not isinstance(state['revision'], int) or state['revision'] < 1:
                raise ValueError('Invalid committed revision')
            validate_career(state['career'])
            by_key = {}
            for row in state['observations']:
                if not isinstance(row, dict) or row['key'] in by_key or row['source_type'] not in {'game_observation', 'user_confirmed'}:
                    raise ValueError('Invalid observation provenance')
                by_key[row['key']] = row
                if state['import_index'].get(row['key']) != row['semantic_hash']:
                    raise ValueError('Observation index mismatch')
            if set(state['import_index']) != set(by_key) or any(origin not in by_key for origin in state['origins'].values()):
                raise ValueError('Dangling observation origin/index')
        except (KeyError, TypeError, ValueError) as exc:
            raise PlayerDataError('corrupt_data', 'Controlled player state is invalid; original file was preserved') from exc
        return deepcopy(state)

    def read_raw(self, context=None):
        return self.read_state(context or self.context)

    def read_snapshot(self, context=None):
        context = context or self.context
        state = self.read_state(context)
        sources = {row['key']: {k: deepcopy(row.get(k)) for k in ('observation_id', 'source_type', 'provider',
                   'source_record_id', 'collected_at', 'effective_at', 'time_domain', 'time_precision', 'source_reference',
                   'game_version', 'adapter_version')}
                   for row in state['observations']}
        return PlayerSnapshot(context, state['profile'], state['training'], state['matches'], state['career'],
            {'schema_version': SCHEMA_VERSION, 'state_version': state['state_version'], 'snapshot_id': state['snapshot_id'],
             'origins': state['origins'], 'sources': sources, 'quality_flags': []})
