"""Trusted PES collector application. Single writer process; never an LLM tool.

Only read_package may supply native input. Preview is read-only; writes require
an explicit persisted binding and a token for the current repository version.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile

from game_adapters.pes2021_native_package import read_package, ENVIRONMENT
from player_data.imports import Observation, ObservationImporter
from player_data.json_repository import JsonPlayerRepository
from player_data.models import PlayerContext, PlayerDataError, content_hash
from player_data.schema import validate_record
from player_data.writer_lock import actual_writer

PROVIDER = 'pes2021_native'
MAPPING = 'pes2021-canonical-v1'
ATTRIBUTES = {
    'offensive_awareness': ('offense', 'attacking_awareness'),
    **{k: ('offense', k) for k in ('ball_control', 'dribbling', 'tight_possession', 'low_pass',
                                   'lofted_pass', 'finishing', 'heading', 'place_kicking', 'curl')},
    **{k: ('physical', k) for k in ('speed', 'acceleration', 'kicking_power', 'physical_contact', 'balance', 'stamina')},
    'jump': ('physical', 'jumping'),
    **{k: ('defense', k) for k in ('defensive_awareness', 'ball_winning', 'aggression')},
    **{k: ('goalkeeping', k) for k in ('gk_awareness', 'gk_catching', 'gk_clearing', 'gk_reflexes', 'gk_reach')},
}


def fail(code, message):
    raise PlayerDataError(code, message)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def stable_id(kind, identity, local_id):
    return kind + '-' + content_hash({'identity': identity, 'local_id': local_id})[:48]


def map_package(report, dataset):
    """Internal mapping only; public service always reparses verified originals."""
    if report['purpose'] != 'real_test' or report['environment'] != ENVIRONMENT:
        fail('unsupported_source', 'Actual imports require reviewed real native captures')
    records = []
    attrs = {}
    for name in report['reviewed_attribute_fields']:
        if name in ATTRIBUTES:
            category, field = ATTRIBUTES[name]
            # Identity on the PES display scale. Never clamp/rescale raw bits.
            attrs.setdefault(category, {})[field] = report['snapshot']['attributes'][name]['value']
    capture = report['snapshot_capture']
    snapshot_payload = {'attributes': attrs, 'name': report['snapshot']['identity']['name']}

    def add(local_id, kind, payload, captured_at, game_time, raw, omitted):
        validate_record(kind, payload)
        source_id = stable_id('source', report['identity'], local_id)
        records.append({'local_id': local_id, 'source_record_id': source_id,
                        'observation_id': stable_id('observation', report['identity'], local_id),
                        'kind': kind, 'payload': payload, 'collected_at': captured_at,
                        'effective_at': game_time['value'], 'time_precision': game_time['precision'],
                        'raw_reference': json.dumps({'dataset': str(Path(dataset).resolve()), 'local_record': local_id,
                                                    'mapping': MAPPING, 'sources': raw}, sort_keys=True),
                        'omitted': omitted})
    add('snapshot-' + capture['capture_id'], 'profile_patch', snapshot_payload, capture['captured_at'],
        capture['game_time'], [report['snapshot_source']], ['overall', 'unreviewed_profile_fields', 'features', 'cumulative'])
    for event in report['events']:
        mode = event['participation']
        note = {'mapping': MAPPING, 'participation': mode, 'native_fixture_anchor': event['research_anchor'],
                'measurement_source': 'normal_engine' if event['detailed_captures'] else 'summary_simulation',
                'identity': 'reviewed_local_event_ledger', 'minutes_not_mapped': 'native_candidate_not_general_elapsed_time'}
        raw = [event['native_sources']['after'], event['review']]
        omitted = ['minutes_played', 'position', 'competition', 'cumulative', 'native_dribbles',
                   'dribble_distance_m', 'average_speed_kmh', 'distance_covered_km']
        if mode in {'played_unknown_role', 'simulated_appearance_recorded'}:
            payload = {'date': event['game_time']['value'], 'notes': json.dumps(note, sort_keys=True)}
            if event['detailed_captures']:
                detail = event['detailed_captures'][0]
                target = detail['target']
                counters = target['counters']
                payload.update(goals=counters['goals'], assists=counters['assists'])
                if target['rating_candidate']['value'] is not None:
                    payload['rating'] = target['rating_candidate']['value']
                stats = {key: counters[key] for key in ('shots', 'shots_on_target', 'passes')}
                accuracy = target['rates_percent']['pass_success']['value']
                if accuracy is not None:
                    stats['pass_accuracy'] = accuracy
                payload['key_stats'] = stats
                raw.append(detail['source'])
            else:
                fields = event['personal_summary']['record']['stat_fields_candidate']
                payload.update(goals=fields['goals_candidate'], assists=fields['assists_candidate'])
                if fields['rating_candidate'] is not None:
                    payload['rating'] = fields['rating_candidate']
                omitted.extend(['shots', 'shots_on_target', 'passes', 'pass_accuracy'])
            kind = 'match_record'
        else:
            # A fixture involving the club is not evidence of a personal match.
            payload = {'date': event['game_time']['value'], 'event': mode,
                       'details': json.dumps(note, sort_keys=True)}
            omitted.extend(['personal_appearance', 'goals', 'assists', 'rating', 'key_stats'])
            kind = 'career_event'
        add(event['event_id'], kind, payload, event['captured_at'], event['game_time'], raw, omitted)
        records[-1]['research_anchor'] = event['research_anchor']
    return records


class Collector:
    """Use in the application's sole writer process; actual and fixture roots separate."""
    def __init__(self, repository, *, source_map=None):
        if not isinstance(repository, JsonPlayerRepository):
            fail('source_boundary', 'Actual repository required')
        self.repository = repository
        self.registry_path = repository.root / 'collectors' / PROVIDER / 'bindings.json'
        self.importer = ObservationImporter(repository, source_type='game_observation', provider=PROVIDER)
        from game_adapters.source_paths import SourceMap
        mapping = os.getenv('FAIT_NATIVE_SOURCE_MAP') if source_map is None else source_map
        self.source_map = SourceMap(mapping) if mapping else None

    def _read_source(self, dataset):
        if self.source_map:
            logical, physical, raw_root = self.source_map.dataset(dataset)
            return read_package(physical, raw_root=raw_root), logical
        return read_package(Path(dataset)), dataset

    def registry(self):
        if not self.registry_path.exists():
            return {'schema': 1, 'bindings': {}}
        value = json.loads(self.registry_path.read_text(encoding='utf-8'))
        digest = value.pop('sha256')
        if value.get('schema') != 1 or digest != content_hash(value):
            fail('corrupt_binding', 'Binding registry integrity mismatch')
        return value

    def bind(self, dataset, context, *, basis, parent_binding=None):
        if not isinstance(basis, str) or not basis.strip():
            fail('binding_review_required', 'Explicit confirmation basis required')
        report, dataset = self._read_source(dataset)
        map_package(report, dataset)  # validate field mapping before persisting trust
        with actual_writer(self.repository.root):
            return self._bind_locked(report, context, basis=basis, parent_binding=parent_binding)

    def _bind_locked(self, report, context, *, basis, parent_binding):
        registry = self.registry()
        key = content_hash(report['identity'])
        entry = {'source_identity': report['identity'], 'context': context.to_dict(),
                 'native': {'id': report['snapshot']['native_player_id'], 'name': report['snapshot']['identity']['name'],
                            'club': report['snapshot']['identity']['club']},
                 'environment': report['environment'], 'adapter_version': report['adapter_version'], 'mapping_version': MAPPING,
                 'basis': basis, 'parent_binding': parent_binding, 'source_baseline': report['snapshot_source']}
        if key in registry['bindings']:
            if registry['bindings'][key] != entry:
                fail('binding_conflict', 'Existing binding is immutable; review a new source branch')
            return {'status': 'DUPLICATE', 'binding_id': key}
        if any(b['context'] == entry['context'] for b in registry['bindings'].values()):
            fail('context_already_bound', 'A new source route requires an independent context')
        if self.repository.read_state(context, allow_empty=True)['state_version'] != 'EMPTY':
            fail('nonempty_target', 'First binding requires an empty actual context; do not adopt unrelated history')
        if parent_binding is not None:
            parent = registry['bindings'].get(parent_binding)
            if parent is None or any(parent['source_identity'][k] != report['identity'][k] for k in ('career_id', 'player_id')):
                fail('invalid_parent', 'Parent must be the same reviewed career/player')
            pc = parent['context']
            if any(pc[k] != entry['context'][k] for k in ('career_id', 'player_id')) or pc['branch_id'] == context.branch_id:
                fail('invalid_branch', 'New branch requires distinct branch and same career/player')
        registry['bindings'][key] = entry
        atomic_json(self.registry_path, {**registry, 'sha256': content_hash(registry)})
        return {'status': 'BOUND', 'binding_id': key, 'context': context.to_dict(), 'history_copied': False}

    def _plan(self, dataset, corrections=None):
        report, dataset = self._read_source(dataset)
        rows = map_package(report, dataset)
        key = content_hash(report['identity'])
        binding = self.registry()['bindings'].get(key)
        if binding is None:
            fail('binding_required', 'Confirm a target context before import')
        if binding['environment'] != report['environment'] or binding['adapter_version'] != report['adapter_version'] or binding['mapping_version'] != MAPPING:
            fail('version_review_required', 'Build/adapter/mapping changed')
        if binding['native'] != {'id': report['snapshot']['native_player_id'], 'name': report['snapshot']['identity']['name'],
                                 'club': report['snapshot']['identity']['club']}:
            fail('binding_mismatch', 'Native target changed')
        context = PlayerContext(**binding['context'])
        state = self.repository.read_state(context, allow_empty=True)
        previous = [r for r in state['observations'] if r['provider'] == PROVIDER]
        corrections = corrections or {}
        if set(corrections) - {r['local_id'] for r in rows}:
            fail('invalid_correction', 'Correction must identify a supplied local event')
        highwater = max((r['effective_at'] for r in previous if r['effective_at']), default=None)
        by_id = {r['observation_id']: r for r in previous}
        prior_anchors = {}
        for old in previous:
            if old['kind'] in {'match_record', 'career_event'}:
                detail = json.loads(old['payload'].get('notes', old['payload'].get('details', '{}')))
                anchor = detail.get('native_fixture_anchor')
                if anchor:
                    prior_anchors.setdefault(tuple(anchor), set()).add(old['source_record_id'])
        observations = []
        for row in rows:
            prior = [r for r in previous if r['source_record_id'] == row['source_record_id']]
            correction = corrections.get(row['local_id'])
            anchor = row.get('research_anchor')
            if anchor and set(prior_anchors.get(tuple(anchor), set())) - {row['source_record_id']}:
                fail('route_review_required', 'Previously observed fixture has another local event ID; review duplicate/replay branch')
            observation_id = row['observation_id']
            raw_reference = row['raw_reference']
            corrects = None
            if correction:
                if set(correction) != {'supersedes', 'reason'} or not isinstance(correction['reason'], str) or not correction['reason'].strip():
                    fail('invalid_correction', 'Explicit prior observation and reason required')
                corrects = correction['supersedes']
                old = by_id.get(corrects)
                if old is None or old['source_record_id'] != row['source_record_id'] or old['effective_at'] != row['effective_at']:
                    fail('invalid_correction', 'Correction must retain source event and date')
                raw_reference = json.dumps({'source': raw_reference, 'correction': correction}, sort_keys=True)
                observation_id = 'correction-' + content_hash({'source': row['source_record_id'], 'payload': row['payload'],
                                                               'reference': raw_reference})[:48]
            elif prior:
                # Re-reading a corrected event must not resurrect its old revision.
                latest = prior[-1]
                prior_reference = latest['raw_reference']
                if latest.get('corrects_observation_id'):
                    prior_reference = json.loads(prior_reference)['source']
                if latest['payload'] != row['payload'] or prior_reference != raw_reference:
                    fail('event_changed_review_required', 'Changed source event requires explicit correction or new reviewed branch')
                observation_id = latest['observation_id']
                corrects = latest.get('corrects_observation_id')
                raw_reference = latest['raw_reference']
            elif highwater is not None and row['effective_at'] is not None and row['effective_at'] < highwater:
                fail('rollback_review_required', 'Unseen earlier state requires history/new-branch review')
            observation = Observation(context=context, observation_id=observation_id, source_type='game_observation', provider=PROVIDER,
                                      collected_at=row['collected_at'], effective_at=row['effective_at'], time_domain='game',
                                      time_precision=row['time_precision'], kind=row['kind'], payload=row['payload'],
                                      expected_state_version=state['state_version'], source_record_id=row['source_record_id'],
                                      game_version='PES2021-1.07.01-DP7-SmokePatchV4', adapter_version=MAPPING,
                                      raw_reference=raw_reference, corrects_observation_id=corrects)
            self.importer._validate(observation)
            observations.append(observation)
        plan = {'status': 'PREVIEW', 'context': context.to_dict(), 'binding_id': key, 'mapping_version': MAPPING,
                'expected_state_version': state['state_version'], 'records': rows,
                'observations': [asdict(o) for o in observations], 'writes': 0,
                'unknown_policy': 'omit unsupported fields; preserve raw references; never replace with zero'}
        plan['preview_token'] = content_hash({'binding': binding, 'observations': plan['observations']})
        return plan, observations

    def preview(self, dataset, *, corrections=None):
        return self._plan(dataset, corrections)[0]

    def commit(self, dataset, preview_token, *, corrections=None):
        # Preserve no-write preflight for invalid/unbound input. Revalidate under
        # the root lock: only that second plan authorizes the entire batch/receipt.
        plan, _ = self._plan(dataset, corrections)
        if plan['preview_token'] != preview_token:
            return {'status': 'CONFLICT', 'reason': 'preview_or_state_changed', 'results': [], 'writes': 0}
        with actual_writer(self.repository.root):
            return self._commit_locked(dataset, preview_token, corrections=corrections)

    def _commit_locked(self, dataset, preview_token, *, corrections=None):
        plan, observations = self._plan(dataset, corrections)
        if plan['preview_token'] != preview_token:
            return {'status': 'CONFLICT', 'reason': 'preview_or_state_changed', 'results': [], 'writes': 0}
        version = plan['expected_state_version']
        results = []
        for observation in observations:
            # Only consume versions returned by A. Never refresh/retry a conflict.
            observation = Observation(**{**asdict(observation), 'context': observation.context, 'expected_state_version': version})
            try:
                result = self.importer.import_observation(observation)
            except OSError as error:
                results.append({'status': 'ERROR', 'observation_id': observation.observation_id,
                                'reason': str(error), 'source_record_id': observation.source_record_id,
                                'raw_reference': observation.raw_reference})
                break
            results.append({**asdict(result), 'source_record_id': observation.source_record_id,
                            'raw_reference': observation.raw_reference})
            if result.status in {'CONFLICT', 'REJECTED'}:
                break
            version = result.state_version
        complete = len(results) == len(observations) and all(r['status'] in {'APPLIED', 'DUPLICATE', 'RECORDED_ONLY'} for r in results)
        response = {'status': 'COMPLETED' if complete else 'PARTIAL', 'context': plan['context'],
                    'results': results, 'not_attempted': [o.observation_id for o in observations[len(results):]],
                    'state_version': version, 'mapping_version': MAPPING}
        # A observations are authoritative if a crash interrupts this receipt.
        receipt = self.repository.root / 'collectors' / PROVIDER / 'receipts' / (preview_token + '.json')
        atomic_json(receipt, response)
        return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['bind', 'preview', 'commit'])
    parser.add_argument('dataset', type=Path)
    parser.add_argument('--actual-root', required=True, type=Path)
    parser.add_argument('--source-map', type=Path, help='Explicit restored-source mapping; historical observation hashes stay unchanged')
    parser.add_argument('--sole-writer', action='store_true', help='Confirm no other process writes this actual root')
    parser.add_argument('--career')
    parser.add_argument('--branch')
    parser.add_argument('--player')
    parser.add_argument('--basis')
    parser.add_argument('--parent-binding')
    parser.add_argument('--preview-token')
    parser.add_argument('--corrections', type=Path, help='Explicit local event -> supersedes/reason JSON')
    args = parser.parse_args()
    try:
        repository = JsonPlayerRepository(args.actual_root, fixture_root=Path(__file__).resolve().parents[1] / 'memory')
        collector = Collector(repository, source_map=args.source_map)
        if args.command != 'preview' and not args.sole_writer:
            fail('single_writer_required', 'Run writes in the sole writer process; stop other writers for this root')
        corrections = json.loads(args.corrections.read_text(encoding='utf-8')) if args.corrections else None
        if args.command == 'bind':
            response = collector.bind(args.dataset, PlayerContext(args.career, args.branch, args.player),
                                      basis=args.basis, parent_binding=args.parent_binding)
        elif args.command == 'preview':
            response = collector.preview(args.dataset, corrections=corrections)
        else:
            if not args.preview_token:
                fail('preview_required', 'Pass the token from a current dry-run')
            response = collector.commit(args.dataset, args.preview_token, corrections=corrections)
        print(json.dumps(response, ensure_ascii=True, allow_nan=False))
        return 0 if response['status'] not in {'CONFLICT', 'PARTIAL'} else 1
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({'status': 'REJECTED', 'reason': getattr(error, 'code', str(error))}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
