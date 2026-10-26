"""M3 observed training focus uses certified native finishing points, not speed as a proxy."""
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from evaluation import test_assessments as helpers


class FinishingAssessmentTests(unittest.TestCase):
    _helpers = helpers.AssessmentTests
    setUp, tearDown = _helpers.setUp, _helpers.tearDown
    add, app, start = _helpers.add, _helpers.app, _helpers.start
    plan_body, plan, evaluate = _helpers.plan_body, _helpers.plan, _helpers.evaluate

    def test_finishing_uses_its_own_source_and_ignores_unrelated_profile_corrections(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.app()) as client:
                self.add('finishing-base', '2026-09-03', {'attributes': {'offense': {'finishing': 57}}}, kind='profile_patch')
                self.start(client)
                plan = self.plan(client, metric='finishing', baseline_count=1, window={'kind': 'game_dates', 'start': '2026-09-04', 'end': '2026-09-05'}, target_value=None)
                self.assertEqual(plan['baseline_observations'][0]['value'], 57)
                self.add('finishing-after', '2026-09-04', {'attributes': {'offense': {'finishing': 58}}}, kind='profile_patch')
                self.add('speed-after', '2026-09-05', {'attributes': {'physical': {'speed': 99}}}, kind='profile_patch')
                first = self.evaluate(client)['record']
                result = first['result']
                self.assertEqual((result['before'], result['after'], result['delta']), (57, 58, 1))
                self.assertEqual((result['evidence_status'], result['goal_status']), ('COMPLETE', 'UNDETERMINED'))
                self.add('speed-correction', '2026-09-03', {'attributes': {'physical': {'speed': 1}}}, kind='profile_patch', corrects_observation_id='finishing-base')
                second = self.evaluate(client, 'same-finishing', 1)['record']
                self.assertEqual(second['revision'], first['revision'])

    def test_finishing_explicit_applied_correction_preserves_old_numbers(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.app()) as client:
                self.add('finishing-base', '2026-09-03', {'attributes': {'offense': {'finishing': 57}}}, kind='profile_patch')
                self.start(client)
                self.plan(client, metric='finishing', baseline_count=1, window={'kind': 'game_dates', 'start': '2026-09-04', 'end': '2026-09-05'}, target_value=None)
                first = self.evaluate(client)['record']
                applied = self.add('finishing-correction', '2026-09-03', {'attributes': {'offense': {'finishing': 56}}}, kind='profile_patch', corrects_observation_id='finishing-base')
                self.assertEqual(applied.status, 'APPLIED')
                self.add('finishing-after', '2026-09-05', {'attributes': {'offense': {'finishing': 58}}}, kind='profile_patch')
                corrected = self.evaluate(client, 'correct-finishing', 1)['record']
                self.assertEqual((corrected['result']['before'], corrected['result']['delta']), (56, 2))
                self.assertEqual(first['result']['before'], 57)

    def test_finishing_requires_attribute_window_and_preserves_explicit_zero(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.app()) as client:
                self.add('finishing-base', '2026-09-03', {'attributes': {'offense': {'finishing': 0}}}, kind='profile_patch')
                self.start(client)
                self.assertEqual(client.post(self.base + '/comparison-plans', json=self.plan_body(metric='finishing')).status_code, 422)
                self.assertEqual(client.post(self.base + '/comparison-plans', json=self.plan_body(metric='finishing', window={'kind': 'game_dates', 'start': '2026-09-04', 'end': '2026-09-05'}, target_value=101)).status_code, 422)
                self.plan(client, metric='finishing', baseline_count=1, window={'kind': 'game_dates', 'start': '2026-09-04', 'end': '2026-09-05'}, target_value=None)
                self.add('finishing-after', '2026-09-05', {'attributes': {'offense': {'finishing': 0}}}, kind='profile_patch')
                result = self.evaluate(client)['record']['result']
                self.assertEqual((result['before'], result['after'], result['delta']), (0, 0, 0))
                self.assertEqual(result['evidence_status'], 'COMPLETE')


if __name__ == '__main__': unittest.main()
