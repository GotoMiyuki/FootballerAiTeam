import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.main import create_app
from config import config
from graph import create_initial_state, load_player_profile
from tools.database import read_player_profile, read_training_history, read_match_history, ReadPlayerProfileTool
from player_data import PlayerContext, PlayerDataError, snapshot_scope
from player_data.repository import FixtureRepository, get_repository
from evaluation.player_fixtures import write_fixture


class PlayerRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        write_fixture(self.root / 'fixture')
        self.repository = FixtureRepository(self.root / 'fixture')
    def tearDown(self):
        self.temp.cleanup()

    def test_all_entrypoints_use_one_source_and_keep_unknown_values(self):
        before = {p: p.read_bytes() for p in (self.root / 'fixture').iterdir()}
        with patch.multiple(config, PLAYER_DATA_ROOT=str(self.root / 'fixture'), PLAYER_DATA_MODE='demo'):
            snapshot = get_repository().read_snapshot()
            self.assertEqual(load_player_profile()['name'], 'Isolated Test Player')
            self.assertEqual(read_player_profile(), snapshot.profile)
            self.assertEqual(json.loads(ReadPlayerProfileTool.invoke({})), snapshot.profile)
            self.assertEqual(create_initial_state('test')['player_snapshot'], snapshot.to_dict())
        with TestClient(create_app(data_dir=self.root / 'web', demo=True, player_repository=self.repository)) as client:
            profile = client.get('/api/player').json()
            self.assertEqual(profile['name'], snapshot.profile['name'])
            self.assertEqual(profile['metadata']['state_version'], snapshot.metadata['state_version'])
            self.assertEqual(client.get('/api/player/training-history').json(), snapshot.training)
            self.assertEqual(client.get('/api/player/match-history').json(), snapshot.matches)
            self.assertIsNone(profile['attributes']['physical']['stamina'])
            self.assertEqual(profile['attributes']['offense']['shooting'], 0)
            self.assertEqual(profile['injury'], 'None')
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_snapshot_fixed_across_file_change_and_another_context(self):
        first = self.repository.read_snapshot()
        self.assertEqual(first.metadata, self.repository.read_snapshot().metadata)
        write_fixture(self.root / 'fixture', name='Changed', speed=99)
        second = self.repository.read_snapshot()
        self.assertNotEqual(first.metadata['state_version'], second.metadata['state_version'])
        with snapshot_scope(first):
            self.assertEqual(read_player_profile()['attributes']['physical']['speed'], 44)
            self.assertEqual(read_training_history(), first.training)
            self.assertEqual(read_match_history(), first.matches)
        copy = first.profile
        copy['name'] = 'caller mutation'
        self.assertEqual(first.profile['name'], 'Isolated Test Player')
        other = FixtureRepository(self.root / 'fixture', PlayerContext('other', 'branch', 'p'))
        self.assertNotEqual(other.read_snapshot().metadata['snapshot_id'], second.metadata['snapshot_id'])
        with self.assertRaises(PlayerDataError):
            self.repository.read_snapshot(other.context)

    def test_predictions_excluded_estimates_preserved_and_corruption_not_hidden(self):
        path = self.root / 'fixture' / 'player.json'
        raw = json.loads(path.read_text(encoding='utf-8'))
        raw['after_4_weeks'] = {'speed': 90}
        raw['attributes']['physical']['速度'] = 98
        path.write_text(json.dumps(raw), encoding='utf-8')
        career_path = self.root / 'fixture' / 'career_history.json'
        career = json.loads(career_path.read_text(encoding='utf-8'))
        career['expected_changes'] = {'overall': 99}
        career['market_value_history'][0]['predicted_next_value'] = 999999
        career_path.write_text(json.dumps(career), encoding='utf-8')
        snapshot = self.repository.read_snapshot()
        self.assertNotIn('after_4_weeks', snapshot.profile)
        self.assertNotIn('速度', snapshot.profile['attributes']['physical'])
        self.assertIn('ignored_noncanonical_fields', snapshot.metadata['quality_flags'])
        self.assertNotIn('observed_market_value', snapshot.career['market_value_history'][0])
        self.assertNotIn('expected_changes', snapshot.career)
        self.assertNotIn('predicted_next_value', snapshot.career['market_value_history'][0])
        path.write_text('{bad', encoding='utf-8')
        with self.assertRaises(PlayerDataError) as error:
            self.repository.read_snapshot()
        self.assertEqual(error.exception.code, 'corrupt_data')
        with TestClient(create_app(data_dir=self.root / 'web', demo=True, player_repository=self.repository)) as client:
            self.assertEqual(client.get('/api/player').status_code, 503)
        self.assertEqual(path.read_text(encoding='utf-8'), '{bad')

    def test_actual_mode_requires_configuration_and_no_demo_fallback(self):
        with patch.multiple(config, PLAYER_DATA_MODE='actual', PLAYER_DATA_ROOT=''):
            with self.assertRaises(PlayerDataError):
                get_repository()
        with patch.multiple(config, PLAYER_DATA_MODE='actual', PLAYER_DATA_ROOT=str(self.root / 'actual')):
            with self.assertRaises(PlayerDataError) as error:
                get_repository().read_snapshot()
            self.assertEqual(error.exception.code, 'missing_data')
            self.assertFalse((self.root / 'actual').exists())
        for identity in ('../escape', 'C:\\absolute', '', 'a/b', 'x' * 65):
            with self.assertRaises(PlayerDataError):
                PlayerContext(identity, 'main', 'p')


if __name__ == '__main__':
    unittest.main()
