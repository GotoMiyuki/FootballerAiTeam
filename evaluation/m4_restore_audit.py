"""Quiescent restore experiment, not a production backup/restore feature."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
from unittest.mock import patch

from evaluation.m4_audit import REPO, dump, digest, harness, settled, start, protected_hashes


def require(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def tables(tasks):
    with sqlite3.connect((tasks / 'workspace.sqlite3').resolve().as_uri() + '?mode=ro', uri=True) as db:
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {name: db.execute('SELECT count(*) FROM ' + name).fetchone()[0] for name in names}


def main():
    from fastapi.testclient import TestClient
    from player_data.json_repository import JsonPlayerRepository, EMPTY_VERSION
    from player_data.imports import ObservationImporter, Observation
    from player_data.models import PlayerContext, content_hash, PlayerDataError
    from backend.main import create_app
    from game_adapters.pes2021_import import Collector
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    assert root.is_relative_to(REPO / 'local_data/game_imports/m4-audit')
    root.mkdir(parents=True, exist_ok=False)
    (root / 'temp').mkdir()
    os.environ.update(TEMP=str(root / 'temp'), TMP=str(root / 'temp'), PYTHONUTF8='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    before_protected = protected_hashes(root)
    source = root / 'source'
    h = harness(source)
    context = PlayerContext('restore-audit', 'main', 'audit-player')
    actual = JsonPlayerRepository(source / 'actual', fixture_root=h.fixture, context=context)
    importer = ObservationImporter(actual, source_type='game_observation', provider='audit')
    dump(source / 'raw/observation.json', {'purpose': 'audit_synthetic', 'name': 'Restore Audit', 'speed': 44})
    result = importer.import_observation(Observation(context, 'profile', 'game_observation', 'audit',
        '2026-10-26T10:00:00+08:00', '2026-10-01', 'game', 'day', 'profile_patch',
        {'name': 'Restore Audit', 'attributes': {'physical': {'speed': 44}}}, EMPTY_VERSION,
        source_record_id='profile', game_version='synthetic-v1', adapter_version='audit-v1', raw_reference='raw/observation.json'))
    assert result.status == 'APPLIED', result
    # Synthetic binding storage exercises hashing/preservation only. No native package/bind command.
    registry = {'schema': 1, 'bindings': {'audit-synthetic-binding': {'context': context.to_dict(), 'purpose': 'audit_synthetic'}}}
    dump(actual.root / 'collectors/pes2021_native/bindings.json', {**registry, 'sha256': content_hash(registry)})
    h.repository = actual
    def app(repo=actual, tasks=source / 'tasks'):
        return create_app(data_dir=tasks, demo=False, player_repository=repo, player_context=context)
    expected = {}
    with patch('backend.runtime.create_runtime', side_effect=h.runtime):
        with TestClient(app()) as client:
            complete = start(client, 'complete restore audit')
            h.complete(client, complete)
            rec = require(client.get(f'/api/missions/{complete}/recommendations'))['items'][0]
            base = f'/api/missions/{complete}/recommendations/{rec["recommendation_id"]}'
            common = {'context': context.to_dict(), 'recommendation_revision': rec['revision']}
            choice = require(client.post(base + '/decisions', json={**common, 'request_id': 'restore-choice',
                'status': 'ADOPTED', 'previous_decision_id': None}))
            execution = require(client.post(base + '/executions', json={**common, 'request_id': 'restore-execution',
                'status': 'UNKNOWN', 'actual_action': 'isolated audit record', 'window': {'time_domain': 'unknown'}}))
            plan = require(client.post(base + '/comparison-plans', json={**common, 'request_id': 'restore-plan', 'confirmed': True,
                'metric': 'speed', 'window': {'kind': 'game_dates', 'start': '2026-10-02', 'end': '2026-10-03'}}))
            assessment = require(client.post(base + '/comparison-plans/' + plan['record']['plan_id'] + '/assessments',
                json={'context': context.to_dict(), 'request_id': 'restore-assess', 'expected_revision': 0}))
            memory = require(client.post(f'/api/missions/{complete}/career-memory/user-records', json={
                'context': context.to_dict(), 'request_id': 'restore-memory', 'operation': 'create',
                'confirmed': True, 'kind': 'preference', 'content': 'audit preference only'}))
            sync = require(client.post(f'/api/missions/{complete}/career-memory/sync'))
            h.require_input = True
            missing = start(client, 'missing input restore audit')
            missing_view = settled(client, missing, 'BLOCKED')
            assert missing_view['blocked']['reason'] == 'missing_user_input'
            h.require_input = False
            approval = start(client, 'report approval restore audit')
            approval_view = settled(client, approval, 'BLOCKED')
            assert approval_view['blocked']['reason'] == 'report_approval'
            paths = [f'/api/missions/{complete}/report', f'/api/missions/{complete}/recommendations',
                base + '/activity?' + '&'.join(k + '=' + v for k, v in context.to_dict().items()), base + '/comparison',
                f'/api/missions/{complete}/career-memory']
            expected = {p: require(client.get(p)) for p in paths}
            fixed = {i: client.app.state.service.store.inputs(i) for i in [complete, missing, approval]}
    # Both SQLite connections/worker are closed before copying. Audit does not advertise hot backup.
    expected_tables = tables(source / 'tasks')
    manifest = {'audit_schema': 1, 'strategy': 'quiescent_after_all_writers_closed', 'git_commit': 'dcf6e7bf740d41ab13bfc2e718e3251e0f6afb24',
        'context': context.to_dict(), 'state_version': result.state_version,
        'paths': {'actual': 'actual', 'tasks': 'tasks', 'checkpoint': 'tasks/checkpoints.sqlite3', 'raw': 'raw'},
        'binding_scope': 'synthetic registry hash/preservation only; no real PES identity validation',
        'files': {str(p.relative_to(source)): digest(p) for p in source.rglob('*') if p.is_file()}}
    dump(root / 'manifest.json', manifest)
    backup = root / 'backup'
    restored = root / 'restored'
    shutil.copytree(source, backup)
    shutil.copytree(backup, restored)
    assert all(digest(restored / p) == d for p, d in manifest['files'].items())
    restored_repo = JsonPlayerRepository(restored / 'actual', fixture_root=restored / 'fixture', context=context)
    assert restored_repo.read_snapshot().metadata['state_version'] == result.state_version
    assert Collector(restored_repo).registry() == registry
    output = {'completed': complete, 'missing': missing, 'approval': approval, 'manifest_file_count': len(manifest['files']),
        'table_counts_before': expected_tables, 'table_counts_restored_before_open': tables(restored / 'tasks'),
        'state_version': result.state_version, 'initial_public_equal': {}, 'fixed_inputs_equal': {}, 'pause_resumes': {}}
    assert output['table_counts_before'] == output['table_counts_restored_before_open']
    with patch('backend.runtime.create_runtime', side_effect=h.runtime):
        with TestClient(app(restored_repo, restored / 'tasks')) as client:
            for path, value in expected.items():
                output['initial_public_equal'][path] = require(client.get(path)) == value
            for i, value in fixed.items():
                output['fixed_inputs_equal'][i] = client.app.state.service.store.inputs(i) == value
            assert all(output['initial_public_equal'].values()) and all(output['fixed_inputs_equal'].values())
            # Mutate only restored latest input. Resume is required to retain original fixed A.
            changed = Observation(context, 'later', 'game_observation', 'audit', '2026-10-26T11:00:00+08:00',
                '2026-10-02', 'game', 'day', 'profile_patch', {'attributes': {'physical': {'speed': 99}}}, result.state_version,
                source_record_id='later', game_version='synthetic-v1', adapter_version='audit-v1', raw_reference='raw/observation.json')
            assert ObservationImporter(restored_repo, source_type='game_observation', provider='audit').import_observation(changed).status == 'APPLIED'
            for i in [missing, approval]:
                view = client.get('/api/missions/' + i).json()
                if view['blocked']['reason'] == 'missing_user_input':
                    fields = view['blocked']['required_inputs']
                    assert len(fields) == 1 and fields[0]['input_type'] == 'text', fields
                    require(client.post('/api/missions/' + i + '/input', json={'values': {fields[0]['key']: 'audit recovered feedback'}}), 202)
                    settled(client, i, 'BLOCKED')
                require(client.post('/api/missions/' + i + '/input', json={'values': {'approved': True}}), 202)
                current = settled(client, i, 'COMPLETED')
                report = require(client.get('/api/missions/' + i + '/report'))['markdown']
                output['pause_resumes'][i] = {'status': current['status'], 'fixed_version': current['input_reference']['state_version'],
                    'latest_version': restored_repo.read_snapshot().metadata['state_version'], 'retained_speed_44': '原球员速度：44' in report}
                assert output['pause_resumes'][i]['retained_speed_44']
    # Broken restore candidates are fresh copies, never overwrite source/restored/live.
    failures = {}
    for kind in ['missing-checkpoint', 'corrupt-checkpoint', 'corrupt-actual', 'wrong-context', 'future-schema']:
        folder = root / kind
        shutil.copytree(backup, folder)
        cp = folder / 'tasks/checkpoints.sqlite3'
        if kind == 'missing-checkpoint':
            cp.unlink()
        elif kind == 'corrupt-checkpoint':
            cp.write_bytes(b'audit corrupt checkpoint')
        repo = JsonPlayerRepository(folder / 'actual', fixture_root=folder / 'fixture', context=context)
        if kind == 'corrupt-actual':
            repo.state_path(context).write_bytes(b'{bad audit json')
        elif kind in ['wrong-context', 'future-schema']:
            p = repo.state_path(context)
            state = json.loads(p.read_text(encoding='utf-8'))
            if kind == 'wrong-context':
                state['context']['branch_id'] = 'other'
            else:
                state['schema_version'] = 'future-audit-version'
            dump(p, state)
        failure = {}
        try:
            with TestClient(app(repo, folder / 'tasks')) as client:
                failure['health'] = require(client.get('/api/health'))
                failure['player_http'] = client.get('/api/player').status_code
                view = client.get('/api/missions/' + missing).json()
                failure['paused_view'] = view
                # Only attempt input for checkpoint faults; actual faults stay read-only.
                if kind in ['missing-checkpoint', 'corrupt-checkpoint']:
                    fields = view['blocked']['required_inputs']
                    r = client.post('/api/missions/' + missing + '/input', json={'values': {fields[0]['key']: 'recovery test'}})
                    failure['resume_http'], failure['resume_body'] = r.status_code, r.json()
        except Exception as exc:
            failure['exception_type'], failure['exception'] = type(exc).__name__, str(exc)
        failures[kind] = failure
    output['broken_restore_candidates'] = failures
    output['scope'] = 'Synthetic actual + real HTTP/store/checkpoints; manual audit-only copying, no native bindings or real sources certified.'
    dump(root / 'restore.json', output)
    after = protected_hashes(root)
    dump(root / 'protected-check.json', {'files_checked': len(before_protected),
        'changed': sorted(k for k in set(after) | set(before_protected) if after.get(k) != before_protected.get(k))})
    print(json.dumps({'completed_restore': True, 'pause_resumes': output['pause_resumes'], 'faults': failures}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
