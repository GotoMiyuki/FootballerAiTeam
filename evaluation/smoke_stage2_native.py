"""M2 acceptance: reviewed native package -> A -> API -> frozen task input.

Explicit isolated output only; no game control, model calls or current config edits.
An injected transport interruption and a runtime-boundary spy are clearly recorded.
Real source files must exist locally; this is not a synthetic fallback test.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from unittest.mock import patch


class BoundaryReached(Exception):
    """Stop before executing any model/graph nodes, after capturing real service input."""


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def run(dataset, output):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    from backend.task_context import input_reference
    from game_adapters.pes2021_import import Collector
    from game_adapters.pes2021_native_package import read_package
    from player_data.json_repository import JsonPlayerRepository
    from player_data.models import PlayerContext, PlayerDataError, content_hash
    from player_data.repository import snapshot_scope
    from tools.database import ReadPlayerProfileTool, read_match_history, read_player_profile

    root = Path(__file__).resolve().parents[1]
    dataset, output = Path(dataset).resolve(), Path(output).resolve()
    if not output.is_relative_to(root / 'local_data/game_imports'):
        raise ValueError('Output must be a new isolated directory under local_data/game_imports')
    if output.exists():
        raise ValueError('Output already exists; choose a new directory, never overwrite acceptance evidence')
    reviewed = read_package(dataset)  # Native SHA/identity validation, no fallback.
    if len(reviewed['events']) < 2:
        raise ValueError('Acceptance requires multiple reviewed real events')
    protected_paths = [root / 'memory/player.json', root / 'memory/sessions.json']
    source_audit = dataset / 'd2-20261023/delivery-acceptance.json'
    if source_audit.exists():
        protected_paths.extend(Path(row['path']) for row in json.loads(source_audit.read_text(encoding='utf-8'))['protected_files'])
    protected = {str(p): sha(p) for p in protected_paths}
    output.mkdir(parents=True, exist_ok=False)
    context = PlayerContext(**reviewed['identity'])
    repository = JsonPlayerRepository(output / 'actual', fixture_root=root / 'memory', context=context)
    collector = Collector(repository)
    binding = collector.bind(dataset, context, basis='M2 closure: existing explicitly reviewed dedicated BAL source identity and event ledger')
    plan = collector.preview(dataset)
    save(output / 'preview.json', plan)
    expected_count = len(plan['observations'])
    if expected_count < 3:
        raise ValueError('Acceptance requires snapshot plus multiple events')
    stop_after = min(3, expected_count - 1)
    original_import = collector.importer.import_observation
    calls = 0

    def interrupted_import(observation):
        nonlocal calls
        calls += 1
        if calls == stop_after + 1:
            raise OSError('M2 acceptance injected transport interruption BEFORE next A write')
        return original_import(observation)

    with patch.object(collector.importer, 'import_observation', side_effect=interrupted_import):
        partial = collector.commit(dataset, plan['preview_token'])
    assert partial['status'] == 'PARTIAL'
    assert [r['status'] for r in partial['results']] == ['APPLIED'] * stop_after + ['ERROR']
    save(output / 'partial-import.json', partial)
    first = repository.read_snapshot(context)
    assert len(repository.read_state(context)['observations']) == stop_after
    fixed = first.to_dict()
    probes = []

    def submit(client):
        with patch.object(client.app.state.service, '_enqueue') as enqueue:
            response = client.post('/api/messages', json={'conversation_id': 'm2-acceptance',
                'content': '阶段二输入一致性验收；不执行模型分析', 'intent': 'new_mission'})
            assert response.status_code == 202, response.text
            enqueue.assert_called_once()
        return response.json()['mission_id']

    def assert_api(client, snapshot):
        response = client.get('/api/player')
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['attributes'] == snapshot.profile['attributes']
        assert data['name'] == snapshot.profile['name']
        assert data['metadata']['state_version'] == snapshot.metadata['state_version']
        assert data['metadata']['context'] == context.to_dict()
        assert client.get('/api/player/match-history').json() == snapshot.matches
        assert client.get('/api/player/training-history').json() == snapshot.training
        return data['metadata']['state_version']

    app = create_app(data_dir=output / 'tasks', demo=False, player_repository=repository, player_context=context)
    with TestClient(app) as client:
        assert client.get('/api/health').json()['mode'] == 'live'
        assert_api(client, first)
        old_mission = submit(client)
        service = client.app.state.service
        old_inputs = service.store.inputs(old_mission)
        assert old_inputs['player_snapshot'] == fixed
        assert service.store.get(old_mission).input_reference == input_reference(first)

        # Resume using a newly instantiated collector: same source IDs, returned CAS versions.
        collector = Collector(repository)
        resumed_plan = collector.preview(dataset)
        resumed = collector.commit(dataset, resumed_plan['preview_token'])
        assert resumed['status'] == 'COMPLETED'
        assert [r['status'] for r in resumed['results']] == ['DUPLICATE'] * stop_after + ['APPLIED'] * (expected_count - stop_after)
        save(output / 'resumed-import.json', resumed)
        current = repository.read_snapshot(context)
        assert current.metadata['state_version'] != first.metadata['state_version']
        assert len(repository.read_state(context)['observations']) == expected_count
        assert_api(client, current)
        assert service.store.inputs(old_mission) == old_inputs
        new_mission = submit(client)
        assert service.store.inputs(new_mission)['player_snapshot'] == current.to_dict()
        assert service.store.get(new_mission).input_reference == input_reference(current)

        # Real service._execute restores its persisted input, then stops at stream boundary.
        # The spy supplies no professional result, report, approval or completed mission.
        class RuntimeBoundary:
            def __init__(self):
                self.closed = False

            def stream(self, graph_input, _config, **_kwargs):
                with snapshot_scope(graph_input['player_snapshot']):
                    profile = read_player_profile()
                    assert json.loads(ReadPlayerProfileTool.invoke({})) == profile
                    probes.append({'snapshot': deepcopy(graph_input['player_snapshot']),
                                   'profile': profile, 'matches': read_match_history()})
                raise BoundaryReached()

            def close(self):
                self.closed = True

        for mission_id, snapshot in ((old_mission, first), (new_mission, current)):
            boundary = RuntimeBoundary()
            with patch('backend.runtime.create_runtime', return_value=(boundary, boundary)) as runtime:
                with patch.object(repository, 'read_snapshot', side_effect=AssertionError('Execution must not read latest facts')):
                    try:
                        service._execute(mission_id)
                    except BoundaryReached:
                        pass
                    else:
                        raise AssertionError('Runtime boundary was not reached')
                runtime.assert_called_once()
            assert boundary.closed
            assert probes[-1]['snapshot'] == snapshot.to_dict()
            assert probes[-1]['profile'] == snapshot.profile
            assert probes[-1]['matches'] == snapshot.matches
            assert service.store.report(mission_id) is None

        isolated = []
        for other in (PlayerContext('m2-other-career', context.branch_id, context.player_id),
                      PlayerContext(context.career_id, 'm2-other-branch', context.player_id)):
            client.app.state.player_context = other
            response = client.get('/api/player')
            assert response.status_code == 404, response.text
            assert not repository.state_path(other).exists()
            registry_before = collector.registry_path.read_bytes()
            try:
                collector.bind(dataset, other, basis='M2 negative probe: do not silently remap existing source')
            except PlayerDataError as error:
                assert error.code == 'binding_conflict'
            else:
                raise AssertionError('Source binding changed silently')
            assert collector.registry_path.read_bytes() == registry_before
            isolated.append(other.to_dict())
        client.app.state.player_context = context

    before_retry = repository.state_path(context).read_bytes()
    retry_plan = Collector(repository).preview(dataset)
    duplicate = Collector(repository).commit(dataset, retry_plan['preview_token'])
    assert len(duplicate['results']) == expected_count
    assert {r['status'] for r in duplicate['results']} == {'DUPLICATE'}
    assert repository.state_path(context).read_bytes() == before_retry
    save(output / 'duplicate-import.json', duplicate)
    save(output / 'task-input-boundaries.json', {'old_input': old_inputs, 'runtime_probes': probes})
    for path, digest in protected.items():
        assert sha(path) == digest, path
    summary = {'validated_at': datetime.now(timezone.utc).isoformat(), 'm2_passed': True,
        'scope': 'specified PES2021 build and reviewed dedicated BAL ledger; real native bytes, isolated actual root',
        'source_identity': context.to_dict(), 'binding_id': binding['binding_id'],
        'source_mode': 'existing_real_native_package_reparsed_not_new_gameplay',
        'injected_transport_interruption_after_applied': stop_after,
        'observations': expected_count, 'personal_matches': len(current.matches),
        'career_events': sum(r['kind'] == 'career_event' for r in repository.read_state(context)['observations']),
        'first_state_version': first.metadata['state_version'], 'final_state_version': current.metadata['state_version'],
        'api_matches_repository': True, 'old_task_frozen_new_task_latest': True,
        'real_service_execution_input_and_snapshot_tools_checked': True,
        'runtime_boundary_spy_no_graph_nodes_or_models': True, 'model_calls': 0,
        'duplicate_retry_byte_identical': True, 'foreign_contexts_return_404_no_fixture_fallback': isolated,
        'binding_silent_retarget_rejected': True,
        'rollback_scope': 'covered by existing adapter tests; no new real game rollback experiment',
        'protected_files': [{'path': p, 'sha256': digest, 'unchanged': True} for p,digest in protected.items()],
        'game_writes': 0, 'current_application_profile_writes': 0,
        'sources': [{'path': str(p.relative_to(output)), 'sha256': sha(p)} for p in output.rglob('*.json')]}
    save(output / 'acceptance.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sole-writer', action='store_true', required=True)
    args = parser.parse_args()
    result = run(args.dataset, args.output)
    print(json.dumps({k: result[k] for k in ('m2_passed', 'observations', 'personal_matches', 'career_events', 'model_calls')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
