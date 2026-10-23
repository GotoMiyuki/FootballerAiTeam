"""Synthetic mapping/control-flow tests, never real-game acceptance evidence."""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from game_adapters.pes2021_import import Collector, ATTRIBUTES, ENVIRONMENT, main
from player_data.models import PlayerContext, PlayerDataError
from player_data.json_repository import JsonPlayerRepository
from player_data.imports import ImportResult


def fixture_report():
    """Mock only at the already-verified package boundary; native parser tested separately."""
    report = {'purpose': 'real_test', 'environment': deepcopy(ENVIRONMENT), 'adapter_version': 'native-v1',
              'identity': {'career_id': 'test-career', 'branch_id': 'controlled', 'player_id': 'player'},
              'snapshot': {'native_player_id': 123, 'identity': {'name': 'Test', 'club': 'TestClub'},
                           'attributes': {name: {'value': 70} for name in ATTRIBUTES}},
              'reviewed_attribute_fields': list(ATTRIBUTES), 'snapshot_source': {'path': 'save.bin', 'sha256': 'a'*64},
              'snapshot_capture': {'capture_id': 'baseline', 'captured_at': '2026-10-23T00:00:00Z',
                                   'game_time': {'value': '2020-10-01', 'precision': 'day'}}, 'events': []}
    for i, participation in enumerate(['played_unknown_role', 'not_selected_reviewed',
                                       'bench_selection_only_actual_participation_unknown', 'simulated_appearance_recorded'], 2):
        event = {'event_id': f'event-{i}', 'participation': participation,
                 'research_anchor': [f'2020-10-{i:02}', 1, 2, i], 'game_time': {'value': f'2020-10-{i:02}', 'precision': 'day'},
                 'captured_at': '2026-10-23T00:01:00Z', 'native_sources': {'after': {'path': f'after-{i}.json', 'sha256': 'b'*64}},
                 'review': {'path': f'review-{i}.json', 'sha256': 'c'*64}, 'detailed_captures': [],
                 'personal_summary': {'record': {'stat_fields_candidate': {'goals_candidate': 1, 'assists_candidate': 0, 'rating_candidate': 6.5}}}}
        if i == 2:
            event['detailed_captures'] = [{'source': {'path': 'detail.json', 'sha256': 'd'*64}, 'target': {
                'counters': {'goals': 1, 'assists': 2, 'shots': 3, 'shots_on_target': 2, 'passes': 6},
                'rating_candidate': {'value': 7.5}, 'rates_percent': {'pass_success': {'value': 50.0}}}}]
        report['events'].append(event)
    return report


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset = self.root / 'native-captures'
        self.report = fixture_report()
        self.reader = patch('game_adapters.pes2021_import.read_package', side_effect=lambda _: deepcopy(self.report))
        self.reader.start()
        self.addCleanup(self.reader.stop)
        self.context = PlayerContext('career', 'main', 'player')
        self.repository = JsonPlayerRepository(self.root/'actual', fixture_root=self.root/'fixtures')
        self.collector = Collector(self.repository)

    def bind(self):
        return self.collector.bind(self.dataset, self.context, basis='Explicit synthetic target confirmation')

    def commit(self, **kwargs):
        plan = self.collector.preview(self.dataset, **kwargs)
        return self.collector.commit(self.dataset, plan['preview_token'], **kwargs)

    def state(self):
        return self.repository.read_state(self.context)

    def test_preview_requires_binding_and_never_creates_repository(self):
        with self.assertRaisesRegex(PlayerDataError, 'Confirm a target'):
            self.collector.preview(self.dataset)
        self.assertFalse(self.repository.root.exists())
        self.bind()
        before = self.collector.registry_path.read_bytes()
        plan = self.collector.preview(self.dataset)
        self.assertEqual(plan['writes'], 0)
        self.assertEqual(before, self.collector.registry_path.read_bytes())
        self.assertFalse(self.repository.state_path(self.context).exists())

    def test_native_fields_map_without_fabricating_absent_appearances(self):
        self.bind()
        result = self.commit()
        self.assertEqual(result['status'], 'COMPLETED')
        snapshot = self.repository.read_snapshot(self.context).to_dict()
        self.assertEqual(len(snapshot['matches']), 2)
        self.assertEqual(len(snapshot['career']['milestones']), 2)
        first, simulated = snapshot['matches']
        self.assertEqual(first['key_stats']['pass_accuracy'], 50.0)
        self.assertNotIn('key_stats', simulated)
        self.assertNotIn('minutes_played', first)
        self.assertNotIn('overall', snapshot['profile'])
        self.assertNotIn('dribbles_attempted', first['key_stats'])
        self.assertEqual(snapshot['profile']['attributes']['physical']['jumping'], 70)
        self.assertIn('summary_simulation', simulated['notes'])
        self.assertEqual(len(self.state()['observations']), 5)

    def test_repeat_and_new_process_no_duplicate_records_or_state_change(self):
        self.bind()
        self.commit()
        before = self.repository.state_path(self.context).read_bytes()
        self.collector = Collector(self.repository)
        result = self.commit()
        self.assertEqual({r['status'] for r in result['results']}, {'DUPLICATE'})
        self.assertEqual(before, self.repository.state_path(self.context).read_bytes())

    def test_repeat_detail_does_not_change_event_identity(self):
        self.bind()
        self.commit()
        self.report['events'][0]['detailed_captures'].append(deepcopy(self.report['events'][0]['detailed_captures'][0]))
        self.assertEqual({r['status'] for r in self.commit()['results']}, {'DUPLICATE'})

    def test_fixture_or_version_rejected_before_binding(self):
        self.report['purpose'] = 'fixture'
        with self.assertRaises(PlayerDataError):
            self.bind()
        self.assertFalse(self.repository.root.exists())

    def test_changed_binding_adapter_and_native_club_rejected(self):
        self.bind()
        self.report['adapter_version'] = 'new'
        with self.assertRaises(PlayerDataError):
            self.collector.preview(self.dataset)
        self.report['adapter_version'] = 'native-v1'
        self.report['snapshot']['identity']['club'] = 'Another'
        with self.assertRaises(PlayerDataError):
            self.collector.preview(self.dataset)

    def test_unknown_values_omitted_and_out_of_scale_not_clamped(self):
        self.bind()
        self.report['events'][0]['detailed_captures'][0]['target']['rates_percent']['pass_success']['value'] = None
        plan = self.collector.preview(self.dataset)
        self.assertNotIn('pass_accuracy', plan['records'][1]['payload']['key_stats'])
        self.report['snapshot']['attributes']['speed']['value'] = 120
        with self.assertRaises(PlayerDataError):
            self.collector.preview(self.dataset)

    def test_old_preview_cannot_write_over_changed_state(self):
        self.bind()
        plan = self.collector.preview(self.dataset)
        self.commit()
        result = self.collector.commit(self.dataset, plan['preview_token'])
        self.assertEqual(result['status'], 'CONFLICT')
        self.assertEqual(result['results'], [])

    def test_changed_same_event_requires_explicit_correction(self):
        self.bind()
        self.commit()
        self.report['events'][0]['detailed_captures'][0]['target']['counters']['goals'] = 2
        with self.assertRaises(PlayerDataError):
            self.collector.preview(self.dataset)
        prior = self.state()['observations'][1]['observation_id']
        corrections = {'event-2': {'supersedes': prior, 'reason': 'Reviewed correction of the same event'}}
        result = self.commit(corrections=corrections)
        self.assertEqual(result['results'][1]['status'], 'APPLIED')
        self.assertEqual(len(self.state()['matches']), 2)
        self.assertEqual(self.state()['matches'][0]['goals'], 2)
        self.assertEqual({r['status'] for r in self.commit()['results']}, {'DUPLICATE'})

    def test_correction_cannot_target_another_source_event(self):
        self.bind()
        self.commit()
        wrong = self.state()['observations'][-1]['observation_id']
        with self.assertRaises(PlayerDataError):
            self.collector.preview(self.dataset, corrections={'event-2': {'supersedes': wrong, 'reason': 'Wrong target'}})

    def test_rollback_unseen_old_record_requires_new_branch(self):
        self.bind()
        self.commit()
        self.report['events'][0]['event_id'] = 'unknown-old-event'
        with self.assertRaises(PlayerDataError):
            self.collector.preview(self.dataset)

    def test_renaming_latest_event_cannot_append_same_fixture_twice(self):
        self.bind()
        self.commit()
        self.report['events'][-1]['event_id'] = 'renamed-last-event'
        with self.assertRaises(PlayerDataError):
            self.collector.preview(self.dataset)
        self.assertEqual(len(self.state()['matches']), 2)

    def test_reviewed_new_branch_is_independent_and_existing_snapshot_frozen(self):
        parent = self.bind()['binding_id']
        self.commit()
        frozen = self.repository.read_snapshot(self.context)
        original = frozen.to_dict()
        self.report['identity']['branch_id'] = 'replay-reviewed'
        new_context = PlayerContext('career', 'replay', 'player')
        bound = self.collector.bind(self.dataset, new_context, basis='Explicit new reviewed replay route', parent_binding=parent)
        self.assertFalse(bound['history_copied'])
        self.assertFalse(self.repository.state_path(new_context).exists())
        self.commit()
        self.assertEqual(frozen.to_dict(), original)
        self.assertEqual(self.repository.read_snapshot(self.context).to_dict(), original)
        self.assertEqual(len(self.repository.read_state(new_context)['matches']), 2)

    def test_another_career_cannot_reuse_current_target_context(self):
        self.bind()
        self.report['identity']['career_id'] = 'another-career'
        with self.assertRaises(PlayerDataError):
            self.collector.bind(self.dataset, self.context, basis='Wrong route')

    def test_partial_response_loss_retries_original_ids(self):
        self.bind()
        real_import = self.collector.importer.import_observation
        calls = 0
        def lose_response(observation):
            nonlocal calls
            calls += 1
            result = real_import(observation)
            if calls == 2:
                raise OSError('Simulated lost response after successful A commit')
            return result
        with patch.object(self.collector.importer, 'import_observation', side_effect=lose_response):
            result = self.commit()
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual(len(result['not_attempted']), 3)
        resumed = self.commit()
        self.assertEqual([r['status'] for r in resumed['results']][:2], ['DUPLICATE', 'DUPLICATE'])
        self.assertEqual(len(self.state()['matches']), 2)

    def test_rejected_or_conflict_halts_without_retry(self):
        self.bind()
        for status in ('REJECTED', 'CONFLICT'):
            with patch.object(self.collector.importer, 'import_observation', return_value=ImportResult(status, 'EMPTY', 'id')) as call:
                result = self.commit()
            self.assertEqual(result['status'], 'PARTIAL')
            self.assertEqual(call.call_count, 1)
            self.assertEqual(len(result['not_attempted']), 4)

    def test_recorded_only_status_preserved(self):
        self.bind()
        self.report['snapshot_capture']['game_time'] = {'value': None, 'precision': 'unknown'}
        result = self.commit()
        self.assertEqual(result['results'][0]['status'], 'RECORDED_ONLY')
        self.assertEqual(self.state()['profile'], {})

    def test_registry_corruption_refuses_instead_of_rebinding(self):
        self.bind()
        raw = json.loads(self.collector.registry_path.read_text())
        raw['bindings'] = {}
        self.collector.registry_path.write_text(json.dumps(raw))
        with self.assertRaises(PlayerDataError):
            self.collector.preview(self.dataset)

    def test_binding_cannot_silently_adopt_unrelated_existing_history(self):
        self.bind()
        self.commit()
        self.collector.registry_path.unlink()  # simulate missing collector metadata
        with self.assertRaises(PlayerDataError):
            self.bind()
        self.assertEqual(len(self.state()['observations']), 5)

    def test_explicit_correction_retry_is_duplicate(self):
        self.bind()
        self.commit()
        prior = self.state()['observations'][1]['observation_id']
        self.report['events'][0]['detailed_captures'][0]['target']['counters']['assists'] = 3
        corrections = {'event-2': {'supersedes': prior, 'reason': 'Reviewed same event correction'}}
        self.commit(corrections=corrections)
        repeated = self.commit(corrections=corrections)
        self.assertEqual({r['status'] for r in repeated['results']}, {'DUPLICATE'})

    def test_cli_write_requires_explicit_single_writer_declaration(self):
        args = ['collector', 'bind', str(self.dataset), '--actual-root', str(self.repository.root),
                '--career', 'career', '--branch', 'main', '--player', 'player', '--basis', 'Explicit test']
        output = io.StringIO()
        with patch('sys.argv', args), contextlib.redirect_stdout(output):
            self.assertEqual(main(), 1)
        self.assertEqual(json.loads(output.getvalue())['reason'], 'single_writer_required')
        self.assertFalse(self.repository.root.exists())


if __name__ == '__main__':
    unittest.main()
