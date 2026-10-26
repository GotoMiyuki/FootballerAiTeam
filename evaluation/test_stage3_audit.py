"""Synthetic rule tests for the read-only M3 audit, never milestone evidence."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from evaluation.audit_stage3_native import native_coverage, stored_feedback_chains
from player_data.models import PlayerContext, PlayerSnapshot


class Stage3AuditTests(unittest.TestCase):
    def data(self, count=10):
        context = PlayerContext('audit-test', 'main', 'p')
        snapshot = PlayerSnapshot(context, {}, [], [], {}, {})
        package = {'purpose': 'real_test', 'identity': context.to_dict(),
            'events': [{'event_id': f'e{i}'} for i in range(count)]}
        mapped, rows = [], []
        for index in range(count):
            planned = dict(local_id=f'e{index}', source_record_id=f's{index}', kind='match_record',
                effective_at=f'2026-09-{index + 1:02d}', payload={'rating': 5})
            mapped.append(planned)
            rows.append(dict(source_record_id=planned['source_record_id'], kind=planned['kind'],
                effective_at=planned['effective_at'], payload=deepcopy(planned['payload']), provider='pes2021_native',
                source_type='game_observation', import_status='APPLIED', current_paths=[f'matches/{index}'],
                game_version='single-build', time_domain='game', time_precision='day', key=f'key-{index}', semantic_hash=f'hash-{index}'))
        return package, mapped, {'snapshot': snapshot, 'observations': rows}

    def test_ten_is_a_contiguous_exact_current_run_not_just_a_record_count(self):
        package, mapped, bundle = self.data()
        self.assertTrue(native_coverage(package, mapped, bundle)['ten_verified_events'])
        bundle['observations'][5]['payload']['rating'] = 9
        checked = native_coverage(package, mapped, bundle)
        self.assertEqual(checked['longest_verified_event_run'], 5)
        self.assertFalse(checked['ten_verified_events'])
        self.assertEqual(checked['unmatched_events'], ['e5'])

    def test_non_personal_events_and_missing_values_are_not_invented_as_matches(self):
        package, mapped, bundle = self.data()
        mapped[4]['kind'] = 'career_event'
        bundle['observations'][4]['kind'] = 'career_event'
        bundle['observations'][7]['current_paths'] = []
        checked = native_coverage(package, mapped, bundle)
        self.assertEqual(checked['matched_personal_matches'], 8)
        self.assertEqual(checked['matched_fixture_records'], 9)
        self.assertEqual(checked['longest_verified_event_run'], 7)
        self.assertFalse(checked['ten_verified_events'])

    def test_fixture_context_or_mixed_versions_cannot_pass(self):
        package, mapped, bundle = self.data()
        package['purpose'] = 'fixtures'
        with self.assertRaises(ValueError):
            native_coverage(package, mapped, bundle)
        package['purpose'] = 'real_test'
        package['identity']['branch_id'] = 'another'
        with self.assertRaises(ValueError):
            native_coverage(package, mapped, bundle)
        package['identity']['branch_id'] = 'main'
        bundle['observations'][4]['game_version'] = 'another-build'
        self.assertFalse(native_coverage(package, mapped, bundle)['ten_verified_events'])

    def test_read_only_missing_database_does_not_initialize_a_task_root(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'missing'
            result = stored_feedback_chains(path, PlayerContext().to_dict())
            self.assertEqual(result['status'], 'NO_TASK_DATABASE')
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
