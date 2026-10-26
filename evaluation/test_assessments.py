"""E2.1 real A/E/API/SQLite, synthetic sourced observations and deterministic model nodes."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sqlite3
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from career_actions.assessment_models import AssessmentRequest
from evaluation import test_session_semantics as semantics
from player_data.models import PlayerContext
from player_data.json_repository import JsonPlayerRepository, EMPTY_VERSION
from player_data.imports import Observation, ObservationImporter


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.harness = semantics.SessionSemanticsTests()
        self.harness.setUp()
        self.context = PlayerContext('e21', 'main', 'p')
        self.repository = JsonPlayerRepository(self.harness.root / 'actual', fixture_root=self.harness.fixture, context=self.context)
        self.importer = ObservationImporter(self.repository, source_type='game_observation', provider='test')
        self.version = EMPTY_VERSION
        self.add('profile', '2026-09-03', {'name': 'E21', 'attributes': {'physical': {'speed': 40}}}, kind='profile_patch')
        self.add('b1', '2026-09-01', {'rating': 0, 'goals': 0})
        self.add('b2', '2026-09-02', {'rating': None})

    def tearDown(self):
        self.harness.tearDown()

    def add(self, identity, when, payload, *, kind='match_record', **changes):
        if kind == 'match_record':
            payload = {'date': when, **payload}
        args = dict(context=self.context, observation_id=identity, source_type='game_observation', provider='test',
            collected_at='2026-10-24T10:00:00+08:00', effective_at=when, time_domain='game', time_precision='day',
            kind=kind, payload=payload, expected_state_version=self.version, source_record_id=identity,
            game_version='test-game-v1', adapter_version='test-adapter-v1', raw_reference='private:path:' + identity)
        result = self.importer.import_observation(Observation(**{**args, **changes}))
        self.assertIn(result.status, ['APPLIED', 'RECORDED_ONLY'], result)
        self.version = result.state_version
        return result

    def app(self):
        return semantics.create_app(data_dir=self.harness.root / 'tasks', demo=False,
            player_repository=self.repository, player_context=self.context)

    def start(self, client):
        self.identity = self.harness.start(client)
        self.harness.complete(client, self.identity)
        self.rec = client.get(f'/api/missions/{self.identity}/recommendations').json()['items'][0]
        self.base = f'/api/missions/{self.identity}/recommendations/{self.rec["recommendation_id"]}'

    def plan_body(self, **changes):
        return {**dict(context=self.context.to_dict(), request_id='plan-1', confirmed=True,
            recommendation_revision=1, metric='rating', baseline_count=2, minimum_samples=1,
            window={'kind': 'next_matches', 'count': 2}, target_value=5, target_operator='gte'), **changes}

    def plan(self, client, **changes):
        result = client.post(self.base + '/comparison-plans', json=self.plan_body(**changes))
        self.assertEqual(result.status_code, 200, result.text)
        self.plan_id = result.json()['record']['plan_id']
        return result.json()['record']

    def evaluate(self, client, request_id='assess-1', revision=0, execution_id=None):
        result = client.post(self.base + f'/comparison-plans/{self.plan_id}/assessments', json={
            'context': self.context.to_dict(), 'request_id': request_id, 'expected_revision': revision, 'execution_id': execution_id})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def read(self, client):
        result = client.get(self.base + '/comparison')
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def test_frozen_baseline_zero_missing_pending_and_hand_checked_mean(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.app()) as client:
                self.start(client)
                plan = self.plan(client)
                baseline = self.repository.state_path(self.context).read_bytes()
                pending = self.evaluate(client)['record']
                self.assertEqual(pending['result']['evidence_status'], 'INSUFFICIENT')
                self.assertEqual(pending['result']['goal_status'], 'UNDETERMINED')
                self.assertEqual(pending['result']['before'], 0)
                self.assertEqual(self.repository.state_path(self.context).read_bytes(), baseline)
                self.add('f1', '2026-09-04', {'rating': None})
                first = self.evaluate(client, 'second', 1)['record']
                self.assertEqual(first['result']['window_matches'], 1)
                self.assertEqual(first['result']['goal_status'], 'UNDETERMINED')
                self.add('f2', '2026-09-05', {'rating': 6})
                final = self.evaluate(client, 'third', 2)['record']
                data = final['result']
                self.assertEqual((data['before'], data['after'], data['delta']), (0, 6, 6))
                self.assertEqual((data['baseline_samples'], data['followup_samples']), (1, 1))
                self.assertEqual((data['baseline_missing'], data['followup_missing']), (1, 1))
                self.assertEqual((data['evidence_status'], data['goal_status']), ('COMPLETE', 'MET'))
                self.assertEqual(data['execution']['status'], 'UNKNOWN')
                self.assertIn('不证明', data['limitations'][0])
                self.assertEqual(self.read(client)['plans'][0], plan)
                self.assertNotIn('private:path', str(final))
                self.assertEqual(client.post(self.base + '/comparison-plans', json=self.plan_body(request_id='late')).status_code, 409)
                self.assertFalse(client.post(self.base + '/comparison-plans', json=self.plan_body()).json()['created'])

    def test_followup_and_baseline_corrections_supersede_without_rewriting(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.app()) as client:
                self.start(client)
                plan = self.plan(client)
                self.add('f1', '2026-09-04', {'rating': 4})
                self.add('f2', '2026-09-05', {'rating': 6})
                original = self.evaluate(client)['record']
                self.add('f2-c', '2026-09-05', {'rating': 0}, corrects_observation_id='f2')
                db = client.app.state.service.store.db
                writes = db.total_changes
                self.assertEqual(self.read(client)['assessments'][0]['validity'], 'superseded')
                self.assertEqual(db.total_changes, writes)
                corrected = self.evaluate(client, 'correction', 1)['record']
                self.assertEqual(corrected['result']['after'], 2)
                self.assertEqual(corrected['result']['goal_status'], 'NOT_MET')
                self.assertEqual(corrected['corrects_revision'], 1)
                self.add('b1-c', '2026-09-01', {'rating': 1}, corrects_observation_id='b1')
                baseline_corrected = self.evaluate(client, 'baseline-correction', 2)['record']
                self.assertEqual(baseline_corrected['result']['before'], 1)
                self.assertEqual(baseline_corrected['result']['delta'], 1)
                self.assertEqual(self.read(client)['plans'][0], plan)
                stored = client.app.state.service.assessments.records(self.plan_id)
                self.assertEqual(stored[0][0].model_dump(), original)
                self.assertEqual([row[1] for row in stored], ['superseded', 'superseded', 'current'])
                events = client.get(f'/api/missions/{self.identity}/recommendation-events').json()
                self.assertEqual(sum(item['type'] == 'assessment.created' for item in events), 3)
                self.assertEqual(sum(item['type'] == 'assessment.superseded' for item in events), 2)
                # Existing C2.1 continues consuming its supported event types, not miscasting assessments as execution.
                self.assertEqual(client.post(f'/api/missions/{self.identity}/career-memory/sync').status_code, 200)

    def test_unrelated_or_recorded_only_observations_do_not_duplicate_assessment(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.app()) as client:
                self.start(client)
                self.plan(client)
                first = self.evaluate(client)['record']
                self.assertEqual(self.add('old', '2026-08-01', {'attributes': {'physical': {'speed': 99}}}, kind='profile_patch').status, 'RECORDED_ONLY')
                again = self.evaluate(client, 'same-inputs', 1)
                self.assertFalse(again['created'])
                self.assertEqual(again['record'], first)

    def test_version_mismatch_and_unknown_chronology_are_incomparable(self):
        for scenario in ['game-version', 'real-time', 'same-day']:
            with self.subTest(scenario=scenario), patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
                with TestClient(self.app()) as client:
                    self.start(client)
                    self.plan(client, request_id='plan-' + scenario)
                    if scenario == 'game-version':
                        self.add('mismatch', '2026-09-04', {'rating': 8}, game_version='other-game')
                    elif scenario == 'real-time':
                        self.add('real', '2026-09-04T10:00:00Z', {'rating': 8}, time_domain='real', time_precision='instant')
                    else:
                        self.add('same1', '2026-09-04', {'rating': 3})
                        self.add('same2', '2026-09-04', {'rating': 7})
                    result = self.evaluate(client, request_id='assess-' + scenario)['record']['result']
                    self.assertEqual(result['evidence_status'], 'INCOMPARABLE')
                    self.assertIsNone(result['delta'])
                    self.assertEqual(result['goal_status'], 'UNDETERMINED')
            # Restore initial fixture aggregate between subcases; never touch application data.
            if scenario != 'same-day':
                self.tearDown()
                self.setUp()

    def test_speed_game_date_window_waits_for_observed_end_and_no_invented_target(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.app()) as client:
                self.start(client)
                self.plan(client, metric='speed', baseline_count=1, window={'kind': 'game_dates', 'start': '2026-09-04', 'end': '2026-09-05'}, target_value=None)
                self.add('speed1', '2026-09-04', {'attributes': {'physical': {'speed': 42}}}, kind='profile_patch')
                pending = self.evaluate(client)['record']['result']
                self.assertEqual((pending['delta'], pending['evidence_status']), (2, 'INSUFFICIENT'))
                self.add('speed2', '2026-09-05', {'attributes': {'physical': {'speed': 45}}}, kind='profile_patch')
                complete = self.evaluate(client, 'complete', 1)['record']['result']
                self.assertEqual((complete['before'], complete['after'], complete['delta']), (40, 45, 5))
                self.assertEqual((complete['evidence_status'], complete['goal_status']), ('COMPLETE', 'UNDETERMINED'))

    def test_execution_is_independent_user_reported_and_correction_invalidates_old_version(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.app()) as client:
                self.start(client)
                self.plan(client)
                self.add('f1', '2026-09-04', {'rating': 6})
                self.add('f2', '2026-09-05', {'rating': 6})
                self.evaluate(client)
                body = dict(context=self.context.to_dict(), request_id='exec', recommendation_revision=1,
                    status='CONFIRMED', actual_action='自报尝试了')
                execution = client.post(self.base + '/executions', json=body).json()['record']
                record = self.evaluate(client, 'bind-exec', 1, execution['execution_id'])['record']
                self.assertEqual(record['result']['execution']['source_type'], 'user_reported')
                client.post(self.base + '/executions', json={**body, 'request_id': 'exec-c', 'status': 'UNKNOWN',
                    'corrects_execution_id': execution['execution_id'], 'correction_reason': '撤回确认'}).raise_for_status()
                self.assertEqual(self.read(client)['assessments'][-1]['validity'], 'superseded')
                latest = self.evaluate(client, 'exec-new-version', 2, execution['execution_id'])['record']
                self.assertEqual(latest['result']['execution']['status'], 'UNKNOWN')
                self.assertNotEqual(latest['result']['execution']['execution_id'], execution['execution_id'])

    def test_scope_revision_payload_and_post_result_target_rejected(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.app()) as client:
                self.start(client)
                for field in ['career_id', 'branch_id', 'player_id']:
                    response = client.post(self.base + '/comparison-plans', json=self.plan_body(context={**self.context.to_dict(), field: 'other'}))
                    self.assertEqual(response.status_code, 409)
                for changes in [{'source_type': 'game_observation'}, {'metric': 'invented_score'}, {'confirmed': False},
                        {'window': {'kind': 'game_dates', 'start': 'bad', 'end': '2026-10-01'}}, {'minimum_samples': 3}]:
                    self.assertEqual(client.post(self.base + '/comparison-plans', json=self.plan_body(**changes)).status_code, 422)
                self.assertEqual(client.post(self.base + '/comparison-plans', json=self.plan_body(recommendation_revision=2)).status_code, 409)
                self.plan(client)
                self.assertEqual(client.post(self.base + '/comparison-plans', json=self.plan_body(target_value=9)).status_code, 409)

    def test_concurrent_replay_cas_and_transaction_rollback(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.app()) as client:
                self.start(client)
                self.plan(client)
                app = client.app.state.service.assessments
                request = AssessmentRequest(context=self.context.to_dict(), request_id='parallel', expected_revision=0)
                db = app.store.db
                with db:
                    db.execute("CREATE TRIGGER fail_assessment_event BEFORE INSERT ON recommendation_events BEGIN SELECT RAISE(ABORT,'assessment failure'); END")
                with self.assertRaises(sqlite3.IntegrityError):
                    app.evaluate(self.identity, self.rec['recommendation_id'], self.plan_id, request)
                self.assertEqual(app.records(self.plan_id), [])
                self.assertEqual(db.execute("SELECT COUNT(*) FROM assessment_requests WHERE kind='assessment'").fetchone()[0], 0)
                with db:
                    db.execute('DROP TRIGGER fail_assessment_event')
                with ThreadPoolExecutor(max_workers=3) as executor:
                    results = list(executor.map(lambda _: app.evaluate(self.identity, self.rec['recommendation_id'], self.plan_id, request), range(3)))
                self.assertEqual(sum(created for _, created in results), 1)
                self.assertEqual(len(app.records(self.plan_id)), 1)

    def test_restart_fact_bytes_mission_inputs_and_read_only_evidence(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.app()) as client:
                self.start(client)
                self.plan(client)
                inputs = client.app.state.service.store.inputs(self.identity)
                facts = self.repository.state_path(self.context).read_bytes()
                record = self.evaluate(client)['record']
                self.assertEqual(self.repository.state_path(self.context).read_bytes(), facts)
                self.assertEqual(client.app.state.service.store.inputs(self.identity), inputs)
                bundle = self.repository.read_observations()
                self.assertNotIn('private:path', str(bundle))
                bundle['observations'][0]['payload']['name'] = 'mutated-copy'
                self.assertNotEqual(self.repository.read_snapshot().profile['name'], 'mutated-copy')
            with TestClient(self.app()) as client:
                self.assertFalse(self.evaluate(client)['created'])
                self.assertEqual(self.read(client)['assessments'][0]['revision'], 1)
                self.assertEqual(client.app.state.service.assessments.records(self.plan_id)[0][0].model_dump(), record)


if __name__ == '__main__':
    unittest.main()
