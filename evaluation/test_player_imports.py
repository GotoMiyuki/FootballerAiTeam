from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from player_data.models import PlayerContext, PlayerDataError
from player_data.json_repository import JsonPlayerRepository, EMPTY_VERSION
from player_data.imports import Observation, ObservationImporter
from tools.database import update_player_profile, UpdatePlayerAttributeTool, append_match_record


class PlayerImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.context = PlayerContext('career', 'main', 'p')
        self.repository = JsonPlayerRepository(self.root / 'actual', fixture_root=self.root / 'fixtures', context=self.context)
        self.importer = ObservationImporter(self.repository, source_type='game_observation', provider='collector')
    def tearDown(self):
        self.temp.cleanup()
    def observation(self, identity='o1', version=EMPTY_VERSION, **kwargs):
        values = dict(context=self.context, observation_id=identity, source_type='game_observation', provider='collector',
                      collected_at='2026-10-01T10:00:00+08:00', effective_at='2026-09-01', time_domain='game',
                      time_precision='day', kind='profile_patch', payload={'name': 'Actual Test Player', 'attributes': {'physical': {'speed': 80}}, 'injury': None},
                      expected_state_version=version, source_record_id=identity, game_version='test-v1', adapter_version='test-v1', raw_reference='isolated-record:' + identity)
        values.update(kwargs)
        return Observation(**values)
    def test_empty_actual_initialization_patch_null_and_source_map(self):
        self.assertFalse(self.repository.root.exists())
        first = self.importer.import_observation(self.observation())
        self.assertEqual(first.status, 'APPLIED')
        snapshot = self.repository.read_snapshot()
        self.assertNotIn('GotoMiyuki', str(snapshot.profile))
        self.assertNotIn('age', snapshot.profile)
        second = self.importer.import_observation(self.observation('o2', first.state_version, effective_at='2026-09-02', payload={'attributes': {'physical': {'speed': None}}, 'injury': 'None'}))
        self.assertEqual(second.status, 'APPLIED')
        snapshot = self.repository.read_snapshot()
        self.assertIsNone(snapshot.profile['attributes']['physical']['speed'])
        self.assertEqual(snapshot.profile['name'], 'Actual Test Player')
        self.assertEqual(snapshot.metadata['origins']['profile/attributes/physical/speed'], 'collector:o2')
        self.assertEqual(snapshot.metadata['origins']['profile/name'], 'collector:o1')
        self.assertEqual(snapshot.metadata['sources']['collector:o2']['time_domain'], 'game')

    def test_dedup_before_cas_conflict_and_restart(self):
        observation = self.observation()
        result = self.importer.import_observation(observation)
        before = self.repository.state_path(self.context).read_bytes()
        retry = replace(observation, collected_at='2026-10-02T09:00:00+08:00')
        self.assertEqual(self.importer.import_observation(retry).status, 'DUPLICATE')
        self.assertEqual(before, self.repository.state_path(self.context).read_bytes())
        self.assertEqual(self.importer.import_observation(replace(observation, payload={'name': 'Conflict'})).status, 'CONFLICT')
        self.assertEqual(self.importer.import_observation(self.observation('new', EMPTY_VERSION)).status, 'CONFLICT')
        recreated = JsonPlayerRepository(self.repository.root, fixture_root=self.root / 'fixtures', context=self.context)
        self.assertEqual(recreated.read_snapshot().metadata['state_version'], result.state_version)
        again = ObservationImporter(recreated, source_type='game_observation', provider='collector')
        self.assertEqual(again.import_observation(retry).status, 'DUPLICATE')

    def test_old_unknown_equal_cross_domain_times_never_override(self):
        result = self.importer.import_observation(self.observation())
        for index, kwargs in enumerate(({'effective_at': '2026-08-01'}, {'effective_at': None, 'time_precision': 'unknown'},
                                        {'effective_at': '2026-09-01'}, {'effective_at': '2026-09', 'time_precision': 'month'},
                                        {'effective_at': '2026-10-01T00:00:00Z', 'time_domain': 'real', 'time_precision': 'instant'})):
            result = self.importer.import_observation(self.observation('old' + str(index), result.state_version,
                                            payload={'attributes': {'physical': {'speed': 99}}}, **kwargs))
            self.assertEqual(result.status, 'RECORDED_ONLY')
            self.assertEqual(self.repository.read_snapshot().profile['attributes']['physical']['speed'], 80)

    def test_same_observation_id_isolated_by_career_branch_and_player(self):
        for context in (self.context, PlayerContext('another', 'main', 'p'), PlayerContext('career', 'rollback', 'p'), PlayerContext('career', 'main', 'q')):
            self.assertEqual(self.importer.import_observation(self.observation(context=context)).status, 'APPLIED')
        self.assertEqual(len(list(self.repository.root.rglob('state.json'))), 4)

    def test_concurrent_compare_and_swap(self):
        initial = self.importer.import_observation(self.observation())
        observations = [self.observation('c' + str(i), initial.state_version, effective_at='2026-09-02', payload={'weight': 70 + i}) for i in range(2)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda o: self.importer.import_observation(o).status, observations))
        self.assertCountEqual(statuses, ['APPLIED', 'CONFLICT'])
        self.assertEqual(len(self.repository.read_raw()['observations']), 2)

    def test_commit_faults_preserve_old_state_and_response_loss_is_idempotent(self):
        first = self.importer.import_observation(self.observation())
        observation = self.observation('o2', first.state_version, effective_at='2026-09-02', payload={'weight': 72})
        before = self.repository.state_path(self.context).read_bytes()
        for fault in ('player_data.imports.os.replace', 'player_data.imports.os.fsync', 'player_data.imports.json.dump'):
            with patch(fault, side_effect=OSError('injected')):
                with self.assertRaises(OSError):
                    self.importer.import_observation(observation)
            self.assertEqual(before, self.repository.state_path(self.context).read_bytes())
            self.assertEqual(self.repository.read_snapshot().metadata['state_version'], first.state_version)
        commit = self.importer._commit
        def response_lost(path, candidate):
            commit(path, candidate)
            raise OSError('response lost')
        with patch.object(self.importer, '_commit', side_effect=response_lost):
            with self.assertRaises(OSError):
                self.importer.import_observation(observation)
        self.assertEqual(self.importer.import_observation(observation).status, 'DUPLICATE')
        self.assertFalse(list(self.repository.root.rglob('*.tmp')))

    def test_invalid_values_sources_and_legacy_writers_rejected(self):
        for payload in ({'after_4_weeks': {'speed': 99}}, {'attributes': {'physical': {'speed': True}}},
                        {'weight': float('nan')}, {'height': float('inf')}, {'other_features': {'form_consistency': 9}},
                        {'other_features': {'weak_foot_accuracy': 0}}, {'name': 1}):
            self.assertEqual(self.importer.import_observation(self.observation(payload=payload)).status, 'REJECTED')
        self.assertEqual(self.importer.import_observation(self.observation(source_type='demo_fixture')).status, 'REJECTED')
        self.assertEqual(self.importer.import_observation(self.observation(provider='model')).status, 'REJECTED')
        self.assertEqual(self.importer.import_observation(self.observation(raw_reference=None)).status, 'REJECTED')
        self.assertFalse(self.repository.root.exists())
        for fn in (lambda: update_player_profile({'weight': 75}), lambda: append_match_record({'goals': 5}),
                   lambda: UpdatePlayerAttributeTool.invoke({'update_json': '{}'})):
            with self.assertRaises(PermissionError):
                fn()
        with self.assertRaises(PlayerDataError):
            JsonPlayerRepository(self.root / 'fixtures' / 'actual', fixture_root=self.root / 'fixtures')

    def test_histories_corrections_sparse_and_manual_confirmation(self):
        version = self.importer.import_observation(self.observation()).state_version
        for identity, kind, payload in [('m', 'match_record', {'date': '2026-09-01', 'goals': 0, 'rating': None}),
                                        ('t', 'training_record', {'week': '2026-W36', 'weekly_load': 0}),
                                        ('c', 'career_event', {'date': '2026-09-01', 'event': 'Debut'})]:
            precision = 'week' if kind == 'training_record' else 'day'
            effective = payload.get('date', payload.get('week'))
            result = self.importer.import_observation(self.observation(identity, version, kind=kind, payload=payload, effective_at=effective, time_precision=precision))
            self.assertEqual(result.status, 'APPLIED')
            version = result.state_version
        result = self.importer.import_observation(self.observation('mc', version, kind='match_record', payload={'date': '2026-09-01', 'goals': 1}, corrects_observation_id='m'))
        self.assertEqual(result.status, 'APPLIED')
        self.assertEqual(len(self.repository.read_snapshot().matches), 1)
        self.assertEqual(self.repository.read_snapshot().matches[0]['goals'], 1)
        sparse = self.importer.import_observation(self.observation('sparse', result.state_version, kind='match_record', payload={'goals': 1}))
        self.assertEqual(sparse.status, 'RECORDED_ONLY')
        manual = ObservationImporter(self.repository, source_type='user_confirmed', provider='manual')
        observation = self.observation('manual', sparse.state_version, provider='manual', source_type='user_confirmed', effective_at='2026-09-02', payload={'name': 'Confirmed'})
        self.assertEqual(manual.import_observation(observation).status, 'REJECTED')
        self.assertEqual(manual.import_observation(replace(observation, confirmation_reference='confirmation-1')).status, 'APPLIED')

    def test_corruption_does_not_reset_data(self):
        self.importer.import_observation(self.observation())
        path = self.repository.state_path(self.context)
        path.write_text('{corrupt', encoding='utf-8')
        with self.assertRaises(PlayerDataError):
            self.importer.import_observation(self.observation('new'))
        self.assertEqual(path.read_text(encoding='utf-8'), '{corrupt')


if __name__ == '__main__':
    unittest.main()
