from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Protocol
import json
from player_data.models import PlayerContext, PlayerSnapshot, PlayerDataError, SCHEMA_VERSION, content_hash
from player_data.schema import project_fixture, validate_record, validate_career

_active_snapshot = ContextVar('player_snapshot', default=None)


class PlayerRepository(Protocol):
    def read_snapshot(self, context: PlayerContext) -> PlayerSnapshot: ...


def contained_path(root, *parts):
    root = Path(root).resolve()
    path = root.joinpath(*parts).resolve()
    if not path.is_relative_to(root):
        raise PlayerDataError('invalid_path', 'Player path is outside its configured root')
    return path


def read_json(path, kind):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise PlayerDataError('missing_data', 'Player data has not been initialized') from exc
    except (ValueError, UnicodeError) as exc:
        raise PlayerDataError('corrupt_data', 'Player data is damaged') from exc
    if not isinstance(data, kind):
        raise PlayerDataError('corrupt_data', 'Player data has an incompatible structure')
    return data


class FixtureRepository:
    """Read-only adapter for explicitly designated flat JSON fixtures."""
    def __init__(self, root, context=None):
        self.root = Path(root).resolve()
        self.context = context or PlayerContext()

    def read_snapshot(self, context=None):
        context = context or self.context
        if context != self.context:
            raise PlayerDataError('missing_context', 'Fixture repository is bound to one explicit context')
        raw = self.read_raw(context)
        profile, ignored = project_fixture(raw['profile'])
        training = [validate_record('training_record', row) for row in raw['training']]
        matches = [validate_record('match_record', row) for row in raw['matches']]
        career, ignored_career = validate_career(raw['career'], fixture=True)
        ignored.extend(ignored_career)
        # Legacy estimated value remains under its estimate key, never observed_market_value.
        data = {'context': context.to_dict(), 'profile': profile, 'training': training, 'matches': matches, 'career': career}
        version = content_hash(data)
        origins = {}
        def map_fields(value, prefix):
            if isinstance(value, dict):
                for key, item in value.items():
                    map_fields(item, prefix + '/' + key)
            else:
                origins[prefix] = 'fixture:' + prefix
        map_fields(profile, 'profile')
        for kind, records in (('training', training), ('matches', matches)):
            for index in range(len(records)):
                origins[f'{kind}/{index}'] = f'fixture:{kind}/{index}'
        map_fields(career, 'career')
        return PlayerSnapshot(context, profile, training, matches, career,
            {'schema_version': SCHEMA_VERSION, 'state_version': version, 'snapshot_id': version,
             'source_type': 'demo_fixture', 'collected_at': None, 'effective_at': None, 'time_domain': None,
             'quality_flags': ['fixture_only'] + (['ignored_noncanonical_fields'] if ignored else []),
             'ignored_fields': ignored, 'origins': origins})

    def read_raw(self, context=None):
        if context is not None and context != self.context:
            raise PlayerDataError('missing_context', 'Unknown fixture context')
        return {kind: read_json(contained_path(self.root, name), shape) for kind, name, shape in (
            ('profile', 'player.json', dict), ('training', 'training_history.json', list),
            ('matches', 'match_history.json', list), ('career', 'career_history.json', dict))}


def get_repository():
    """One configuration boundary; no independent API/tool path caches."""
    from config import config
    root = config.PLAYER_DATA_ROOT
    mode = config.PLAYER_DATA_MODE
    context = PlayerContext(config.CAREER_ID, config.BRANCH_ID, config.PLAYER_ID)
    if mode == 'actual':
        if not root:
            raise PlayerDataError('configuration', 'Actual player mode requires FAIT_PLAYER_DATA_ROOT')
        from player_data.json_repository import JsonPlayerRepository
        return JsonPlayerRepository(root, fixture_root=config.MEMORY_DIR, context=context)
    if mode != 'demo':
        raise PlayerDataError('configuration', 'Unknown player data mode')
    legacy = [Path(getattr(config, key)).resolve().parent for key in
              ('PLAYER_FILE', 'TRAINING_HISTORY_FILE', 'MATCH_HISTORY_FILE', 'CAREER_HISTORY_FILE')]
    if len(set(legacy)) != 1:
        raise PlayerDataError('configuration', 'Legacy player paths must share one fixture root')
    if root and legacy[0] != Path(config.MEMORY_DIR).resolve() and Path(root).resolve() != legacy[0]:
        raise PlayerDataError('configuration', 'Contradictory player root and legacy paths')
    return FixtureRepository(root or legacy[0], context)


@contextmanager
def snapshot_scope(snapshot):
    if isinstance(snapshot, dict):
        snapshot = PlayerSnapshot.from_dict(snapshot)
    token = _active_snapshot.set(snapshot)
    try:
        yield
    finally:
        _active_snapshot.reset(token)


def read_snapshot():
    active = _active_snapshot.get()
    return active if active is not None else get_repository().read_snapshot()
