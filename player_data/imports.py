"""Trusted application boundary. Never register these capabilities as LLM tools.

An importer is configured by a collector/manual-confirmation application. A
payload's source label alone cannot change the authorized source/provider.
"""
from copy import deepcopy
from dataclasses import dataclass, asdict
from datetime import datetime, date
import json
import os
from pathlib import Path
import re
import tempfile
import threading

from player_data.models import PlayerContext, PlayerDataError, SCHEMA_VERSION, content_hash
from player_data.schema import validate_record
from player_data.json_repository import EMPTY_VERSION, state_digest

_locks_guard = threading.Lock()
_locks = {}


@dataclass(frozen=True)
class Observation:
    context: PlayerContext
    observation_id: str
    source_type: str
    provider: str
    collected_at: str
    effective_at: str | None
    time_domain: str
    time_precision: str
    kind: str
    payload: dict
    expected_state_version: str
    schema_version: str = SCHEMA_VERSION
    source_record_id: str | None = None
    game_version: str | None = None
    adapter_version: str | None = None
    confirmation_reference: str | None = None
    raw_reference: str | None = None
    corrects_observation_id: str | None = None


@dataclass(frozen=True)
class ImportResult:
    status: str
    state_version: str
    observation_id: str
    reason: str = ''


def identifier(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value))


def aware_time(value):
    if not isinstance(value, str):
        raise PlayerDataError('invalid_time', 'Collection time requires an explicit timezone')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('Missing timezone')
        return parsed
    except ValueError as exc:
        raise PlayerDataError('invalid_time', 'Invalid timezone-aware time') from exc


def effective_time(value, domain, precision):
    if domain not in {'game', 'real'}:
        raise PlayerDataError('invalid_time', 'Unknown time domain')
    if value is None:
        if precision != 'unknown':
            raise PlayerDataError('invalid_time', 'Unknown effective time requires unknown precision')
        return None
    if domain == 'real':
        if precision != 'instant':
            raise PlayerDataError('invalid_time', 'Real observations require instant precision')
        return aware_time(value)
    try:
        if precision == 'day' and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            date.fromisoformat(value)
        elif precision == 'month' and re.fullmatch(r'\d{4}-\d{2}', value):
            datetime.strptime(value, '%Y-%m')
        elif precision == 'week' and re.fullmatch(r'\d{4}-W\d{2}', value):
            year, week = value.split('-W')
            date.fromisocalendar(int(year), int(week), 1)
        else:
            raise ValueError('Unsupported game date precision')
    except (ValueError, TypeError) as exc:
        raise PlayerDataError('invalid_time', 'Invalid game date or precision') from exc
    return value  # retains original precision; never fabricates a day


def leaves(value, prefix='profile'):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from leaves(item, prefix + '/' + key)
    else:
        yield prefix, value


def merge_patch(current, patch):
    result = deepcopy(current)
    for key, value in patch.items():
        result[key] = merge_patch(result.get(key) or {}, value) if isinstance(value, dict) else deepcopy(value)
    return result


class ObservationImporter:
    def __init__(self, repository, *, source_type, provider):
        from player_data.json_repository import JsonPlayerRepository
        if not isinstance(repository, JsonPlayerRepository) or source_type not in {'game_observation', 'user_confirmed'} or not identifier(provider):
            raise PlayerDataError('source_boundary', 'Importer requires actual storage and an authorized observation source')
        self.repository = repository
        self.source_type, self.provider = source_type, provider

    def _validate(self, observation):
        if not isinstance(observation, Observation) or not isinstance(observation.context, PlayerContext):
            raise PlayerDataError('invalid_observation', 'Expected a domain observation')
        if observation.schema_version != SCHEMA_VERSION or observation.source_type != self.source_type or observation.provider != self.provider:
            raise PlayerDataError('source_boundary', 'Observation does not match the configured collector/confirmation entry')
        if not identifier(observation.observation_id) or observation.source_record_id is not None and not identifier(observation.source_record_id):
            raise PlayerDataError('invalid_identity', 'Invalid observation/source record id')
        if not isinstance(observation.expected_state_version, str) or not observation.expected_state_version:
            raise PlayerDataError('invalid_version', 'Expected state version is required')
        aware_time(observation.collected_at)
        effective_time(observation.effective_at, observation.time_domain, observation.time_precision)
        if self.source_type == 'game_observation' and not all(isinstance(v, str) and v.strip() for v in
                (observation.game_version, observation.adapter_version, observation.source_record_id, observation.raw_reference)):
            raise PlayerDataError('source_boundary', 'Game observation requires collector versions and raw record reference')
        if self.source_type == 'user_confirmed' and not (isinstance(observation.confirmation_reference, str) and observation.confirmation_reference.strip()):
            raise PlayerDataError('source_boundary', 'Independent manual confirmation reference is required')
        if observation.kind not in {'profile_patch', 'training_record', 'match_record', 'career_event'}:
            raise PlayerDataError('invalid_kind', 'Unsupported observation kind')
        validate_record(observation.kind, observation.payload)
        if observation.corrects_observation_id is not None and not identifier(observation.corrects_observation_id):
            raise PlayerDataError('invalid_identity', 'Invalid correction observation id')
        payload_time = observation.payload.get('date') if observation.kind in {'match_record', 'career_event'} else observation.payload.get('week') if observation.kind == 'training_record' else None
        if payload_time is not None and observation.effective_at is not None:
            if observation.time_domain == 'game' and observation.time_precision in {'day', 'month', 'week'}:
                if observation.kind != 'training_record' or observation.time_precision == 'week':
                    if payload_time != observation.effective_at:
                        raise PlayerDataError('invalid_time', 'Record date and effective time disagree')
        if not observation.payload:
            raise PlayerDataError('invalid_payload', 'Empty observation patch')

    def import_observation(self, observation):
        identity = getattr(observation, 'observation_id', '')
        try:
            self._validate(observation)
        except PlayerDataError as exc:
            return ImportResult('REJECTED', '', identity, exc.code)
        path = self.repository.state_path(observation.context)
        lock_key = os.path.normcase(str(path))
        with _locks_guard:
            lock = _locks.setdefault(lock_key, threading.RLock())
        with lock:
            state = self.repository.read_state(observation.context, allow_empty=True)
            # Semantic identity excludes pure retry collection time and CAS token.
            row = asdict(observation)
            semantic = {k: v for k, v in row.items() if k not in {'collected_at', 'expected_state_version'}}
            semantic_hash = content_hash(semantic)
            key = observation.provider + ':' + observation.observation_id
            index = state['import_index']
            if key in index:
                return ImportResult('DUPLICATE' if index[key] == semantic_hash else 'CONFLICT', state['state_version'], identity,
                                    '' if index[key] == semantic_hash else 'observation_content_changed')
            if observation.expected_state_version != state['state_version']:
                return ImportResult('CONFLICT', state['state_version'], identity, 'stale_state_version')
            by_key = {item['key']: item for item in state['observations']}
            correction_key = self.provider + ':' + observation.corrects_observation_id if observation.corrects_observation_id else None
            if correction_key and correction_key not in by_key:
                return ImportResult('REJECTED', state['state_version'], identity, 'unknown_correction_reference')
            row.update(key=key, semantic_hash=semantic_hash,
                       source_reference=observation.confirmation_reference or observation.raw_reference)
            status, reason = self._application_status(state, row, by_key, correction_key)
            candidate = deepcopy(state)
            if status == 'APPLIED':
                self._apply(candidate, row)
            row['import_status'] = status
            candidate['observations'].append(row)
            candidate['import_index'][key] = semantic_hash
            candidate['revision'] += 1
            checksum = state_digest(candidate)
            candidate['snapshot_id'] = checksum
            candidate['state_version'] = f"{candidate['revision']}:{checksum}"
            self._commit(path, candidate)
            return ImportResult(status, candidate['state_version'], identity, reason)

    def _application_status(self, state, row, by_key, correction_key):
        if row['effective_at'] is None:
            return 'RECORDED_ONLY', 'unknown_effective_time'
        if row['kind'] == 'profile_patch':
            paths = [path for path, value in leaves(row['payload'])]
            if not paths:
                return 'RECORDED_ONLY', 'no_projectable_fields'
            previous_keys = {state['origins'].get(path) for path in paths}
        else:
            if row['kind'] == 'match_record' and not row['payload'].get('date') or row['kind'] == 'training_record' and not row['payload'].get('week'):
                return 'RECORDED_ONLY', 'sparse_record'
            # Appended historical records may be older; they never overwrite a
            # current profile. Cross-domain history remains explicitly isolated.
            previous_keys = set()
            if row['kind'] == 'career_event' and not row['payload'].get('date'):
                return 'RECORDED_ONLY', 'sparse_record'
            if correction_key:
                previous = by_key[correction_key]
                if previous['kind'] != row['kind'] or correction_key not in state['origins'].values():
                    return 'RECORDED_ONLY', 'ambiguous_correction'
                previous_keys.add(correction_key)
        for previous_key in previous_keys - {None}:
            previous = by_key[previous_key]
            if previous['time_domain'] != row['time_domain'] or previous['time_precision'] != row['time_precision']:
                return 'RECORDED_ONLY', 'incomparable_time'
            old_time = effective_time(previous['effective_at'], previous['time_domain'], previous['time_precision'])
            new_time = effective_time(row['effective_at'], row['time_domain'], row['time_precision'])
            if correction_key:
                if previous_key != correction_key or new_time != old_time:
                    return 'RECORDED_ONLY', 'ambiguous_correction'
            elif new_time <= old_time:
                return 'RECORDED_ONLY', 'older_or_ambiguous_state'
        return 'APPLIED', ''

    def _apply(self, state, row):
        kind, payload, key = row['kind'], row['payload'], row['key']
        if kind == 'profile_patch':
            state['profile'] = merge_patch(state['profile'], payload)
            for path, value in leaves(payload):
                state['origins'][path] = key
        elif kind in {'training_record', 'match_record'}:
            name = 'training' if kind == 'training_record' else 'matches'
            correction = row.get('corrects_observation_id')
            if correction:
                target = row['provider'] + ':' + correction
                path = next(path for path, origin in state['origins'].items() if origin == target and path.startswith(name + '/'))
                state[name][int(path.rsplit('/', 1)[1])] = deepcopy(payload)
                state['origins'][path] = key
                return
            state['origins'][f'{name}/{len(state[name])}'] = key
            state[name].append(deepcopy(payload))
        else:
            milestones = state['career'].setdefault('milestones', [])
            correction = row.get('corrects_observation_id')
            if correction:
                target = row['provider'] + ':' + correction
                path = next(path for path, origin in state['origins'].items() if origin == target and path.startswith('career/milestones/'))
                milestones[int(path.rsplit('/', 1)[1])] = deepcopy(payload)
                state['origins'][path] = key
                return
            state['origins'][f'career/milestones/{len(milestones)}'] = key
            milestones.append(deepcopy(payload))

    def _commit(self, path, candidate):
        """Atomic same-directory replace. Never remove the previous target."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix='.observation-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(candidate, stream, ensure_ascii=False, allow_nan=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            # Re-check containment after directory creation (including symlinks).
            resolved = path.resolve()
            if not resolved.is_relative_to(self.repository.root):
                raise PlayerDataError('invalid_path', 'Player commit escaped configured root')
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
