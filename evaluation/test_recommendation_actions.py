"""E1.2: real application/API/SQLite; deterministic model nodes, isolated facts."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sqlite3
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from career_actions.models import DecisionRequest
from evaluation import test_session_semantics as semantics
from evaluation.player_fixtures import write_fixture
from evaluation import test_recommendations as recommendations
from player_data.models import PlayerContext


class RecommendationActionTests(unittest.TestCase):
    setUp = recommendations.RecommendationTests.setUp
    tearDown = recommendations.RecommendationTests.tearDown
    complete = recommendations.RecommendationTests.complete
    read = recommendations.RecommendationTests.read

    def setup_record(self, client):
        identity = self.complete(client)
        record = self.read(client, identity)['items'][0]
        self.base = f'/api/missions/{identity}/recommendations/{record["recommendation_id"]}'
        self.body = dict(request_id='choice-1', recommendation_revision=record['revision'],
            context=record['context'], status='ADOPTED', reason='', previous_decision_id=None)
        return identity, record

    def feedback(self, **changes):
        return dict(request_id='feedback-1', recommendation_revision=self.body['recommendation_revision'],
            context=self.body['context'], status='UNKNOWN', actual_action='执行情况尚未确认',
            window={'time_domain': 'unknown', 'label': None}, **changes)

    def activity(self, client):
        response = client.get(self.base + '/activity', params=self.body['context'])
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_choice_execution_difference_withdrawal_correction_and_restart(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                identity, record = self.setup_record(client)
                before = self.harness.repository.read_snapshot().to_dict()
                decision = client.post(self.base + '/decisions', json=self.body).json()['record']
                self.assertEqual(self.activity(client)['executions'], [])
                feedback = self.feedback()
                execution = client.post(self.base + '/executions', json=feedback).json()['record']
                self.assertEqual(execution['status'], 'UNKNOWN')
                correction = {**feedback, 'request_id': 'correct-1', 'status': 'PARTIAL',
                    'actual_action': '自报尝试10分钟，速度99；未经游戏核验',
                    'window': {'time_domain': 'game', 'label': '2026年10月第一周'},
                    'corrects_execution_id': execution['execution_id'], 'correction_reason': '补充实际尝试'}
                corrected = client.post(self.base + '/executions', json=correction).json()['record']
                withdrawal = {**self.body, 'request_id': 'withdraw-choice', 'status': 'REJECTED',
                    'reason': '撤回采纳', 'previous_decision_id': decision['decision_id']}
                second = client.post(self.base + '/decisions', json=withdrawal).json()['record']
                history = self.activity(client)
                self.assertEqual(history['current_decision_id'], second['decision_id'])
                self.assertEqual(history['current_execution_ids'], [corrected['execution_id']])
                self.assertEqual([item['status'] for item in history['decisions']], ['ADOPTED', 'REJECTED'])
                self.assertEqual(corrected['revision'], 2)
                self.assertEqual(corrected['source_type'], 'user_reported')
                self.assertEqual(history['executions'][0], execution)
                self.assertEqual(before, self.harness.repository.read_snapshot().to_dict())
                events = client.get(f'/api/missions/{identity}/recommendation-events').json()
                self.assertEqual([item['type'] for item in events], ['recommendation.created',
                    'decision.recorded', 'execution.recorded', 'execution.corrected', 'decision.recorded'])
                self.assertEqual(events[3]['previous_record_id'], execution['execution_id'])
                self.assertEqual(events[3]['object_id'], corrected['execution_id'])
                self.assertEqual(events[3]['object_revision'], 2)
                self.assertEqual(events[3]['source'], record['source'])
                calls = list(self.harness.calls)
            with TestClient(self.harness.app()) as client:
                self.assertEqual(self.activity(client), history)
                self.assertEqual(client.get(f'/api/missions/{identity}/recommendation-events').json(), events)
                self.assertFalse(client.post(self.base + '/executions', json=correction).json()['created'])
                self.assertFalse(client.post(self.base + '/decisions', json=self.body).json()['created'])
                self.assertEqual(self.harness.calls, calls)

    def test_execution_without_adoption_and_distinct_windows_remain_independent(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                self.setup_record(client)
                for index, domain in enumerate(['unknown', 'real', 'game', 'match']):
                    response = client.post(self.base + '/executions', json={**self.feedback(),
                        'request_id': f'feedback-{index}', 'status': 'CONFIRMED', 'actual_action': '我明确尝试了',
                        'window': {'time_domain': domain, 'label': None if domain == 'unknown' else f'窗口 {index}'}})
                    self.assertEqual(response.status_code, 200, response.text)
                history = self.activity(client)
                self.assertIsNone(history['current_decision_id'])
                self.assertEqual(len(history['current_execution_ids']), 4)
                self.assertEqual(len(history['executions']), 4)

    def test_request_replay_conflict_concurrent_cas_and_no_duplicate_events(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                identity, record = self.setup_record(client)
                app = client.app.state.service.recommendations
                request = DecisionRequest(**self.body)
                with ThreadPoolExecutor(max_workers=4) as executor:
                    results = list(executor.map(lambda _: app.record_decision(identity,
                        record['recommendation_id'], request), range(4)))
                self.assertEqual(sum(created for _, created in results), 1)
                self.assertEqual(len({item.decision_id for item, _ in results}), 1)
                self.assertEqual(client.post(self.base + '/decisions', json={**self.body,
                    'reason': 'different'}).status_code, 409)
                self.assertEqual(client.post(self.base + '/decisions', json={**self.body,
                    'request_id': 'stale-click'}).status_code, 409)
                self.assertEqual(client.post(self.base + '/executions', json={**self.feedback(),
                    'request_id': self.body['request_id']}).status_code, 409)
                self.assertEqual(len(self.activity(client)['decisions']), 1)
                self.assertEqual(len(client.get(f'/api/missions/{identity}/recommendation-events').json()), 2)
                # Two changes based on the same head cannot silently overwrite one another.
                previous = results[0][0].decision_id
                def change(index):
                    try:
                        return app.record_decision(identity, record['recommendation_id'],
                            DecisionRequest(**{**self.body, 'request_id': f'change-{index}',
                                'status': 'DEFERRED', 'previous_decision_id': previous}))[1]
                    except ValueError:
                        return False
                with ThreadPoolExecutor(max_workers=2) as executor:
                    self.assertEqual(sum(executor.map(change, range(2))), 1)

    def test_expired_or_withdrawn_adoption_rejected_but_history_and_replay_survive(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                identity, _ = self.setup_record(client)
                first = client.post(self.base + '/decisions', json=self.body).json()['record']
                write_fixture(self.harness.fixture, speed=99)
                changed = {**self.body, 'request_id': 'new-choice', 'previous_decision_id': first['decision_id']}
                self.assertEqual(client.post(self.base + '/decisions', json=changed).status_code, 409)
                self.assertFalse(client.post(self.base + '/decisions', json=self.body).json()['created'])
                rejected = client.post(self.base + '/decisions', json={**changed, 'status': 'REJECTED'})
                self.assertEqual(rejected.status_code, 200, rejected.text)
                self.assertEqual(rejected.json()['record']['recommendation_validity'], 'needs_reassessment')
                service = client.app.state.service
                service.fail(service.store.get(identity), 'source withdrawn for test')
                self.assertEqual(client.post(self.base + '/decisions', json={**self.body,
                    'request_id': 'withdrawn-adoption', 'previous_decision_id': rejected.json()['record']['decision_id']}).status_code, 409)
                self.assertEqual(client.post(self.base + '/executions', json=self.feedback()).status_code, 200)
                self.assertEqual(len(self.activity(client)['decisions']), 2)

    def test_context_mission_revision_and_spoofed_source_rejected(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                self.setup_record(client)
                for field in ['career_id', 'branch_id', 'player_id']:
                    wrong = {**self.body['context'], field: 'other'}
                    self.assertEqual(client.post(self.base + '/decisions', json={**self.body, 'context': wrong}).status_code, 409)
                    self.assertEqual(client.post(self.base + '/executions', json={**self.feedback(), 'context': wrong}).status_code, 409)
                    self.assertEqual(client.get(self.base + '/activity', params=wrong).status_code, 409)
                for operation, body in [('decisions', self.body), ('executions', self.feedback())]:
                    self.assertEqual(client.post(self.base + '/' + operation,
                        json={**body, 'recommendation_revision': 2}).status_code, 409)
                    self.assertEqual(client.post(self.base + '/' + operation,
                        json={**body, 'source_type': 'game_observation'}).status_code, 422)
                other = self.complete(client)
                wrong_base = self.base.replace(self.base.split('/')[3], other)
                self.assertEqual(client.post(wrong_base + '/decisions', json=self.body).status_code, 409)
                self.assertEqual(client.get(wrong_base + '/activity', params=self.body['context']).status_code, 409)
                self.assertEqual(client.post(self.base.replace('rec_', 'missing_') + '/decisions', json=self.body).status_code, 404)
                self.assertEqual(self.activity(client)['decisions'], [])

    def test_correction_must_reference_same_recommendation_current_execution(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                self.setup_record(client)
                first_base, first_body = self.base, deepcopy(self.body)
                original = client.post(self.base + '/executions', json=self.feedback()).json()['record']
                correction = {**self.feedback(), 'request_id': 'correction',
                    'corrects_execution_id': original['execution_id'], 'correction_reason': '撤回未经确认反馈'}
                corrected = client.post(self.base + '/executions', json=correction)
                self.assertEqual(corrected.status_code, 200)
                self.assertEqual(client.post(self.base + '/executions', json={**correction,
                    'request_id': 'second-correction'}).status_code, 409)
                self.setup_record(client)
                self.assertEqual(client.post(self.base + '/executions', json={**correction,
                    'request_id': 'cross-record', 'context': self.body['context']}).status_code, 409)
                self.base, self.body = first_base, first_body
                self.assertEqual(len(self.activity(client)['executions']), 2)

    def test_transaction_failure_rolls_back_object_head_request_and_event(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                identity, record = self.setup_record(client)
                app = client.app.state.service.recommendations
                before = self.activity(client)
                events = app.repository.events(identity)
                with app.service.store.db:
                    app.service.store.db.execute("CREATE TRIGGER fail_action_event BEFORE INSERT ON recommendation_events BEGIN SELECT RAISE(ABORT,'action failure'); END")
                with self.assertRaises(sqlite3.IntegrityError):
                    app.record_decision(identity, record['recommendation_id'], DecisionRequest(**self.body))
                self.assertEqual(self.activity(client), before)
                self.assertEqual(app.repository.events(identity), events)
                self.assertEqual(app.service.store.db.execute('SELECT COUNT(*) FROM recommendation_action_requests').fetchone()[0], 0)
                with app.service.store.db:
                    app.service.store.db.execute('DROP TRIGGER fail_action_event')
                self.assertTrue(client.post(self.base + '/decisions', json=self.body).json()['created'])

    def test_invalid_windows_blank_feedback_and_incomplete_corrections_rejected(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                self.setup_record(client)
                for change in [{'actual_action': '  '}, {'window': {'time_domain': 'game'}},
                        {'window': {'time_domain': 'unknown', 'label': 'today'}},
                        {'corrects_execution_id': 'x'}, {'correction_reason': 'missing target'},
                        {'window': {'time_domain': 'match', 'label': '  '}}, {'observation_id': 'invented'}]:
                    response = client.post(self.base + '/executions', json={**self.feedback(), **change})
                    self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.activity(client)['executions'], [])

    def test_actual_fact_bytes_and_hash_unchanged_after_all_feedback_operations(self):
        from player_data.json_repository import JsonPlayerRepository, EMPTY_VERSION
        from player_data.imports import Observation, ObservationImporter
        context = PlayerContext('e12-actual', 'main', 'p')
        actual = JsonPlayerRepository(self.harness.root / 'actual', fixture_root=self.harness.fixture, context=context)
        ObservationImporter(actual, source_type='game_observation', provider='test').import_observation(
            Observation(context=context, observation_id='initial', source_type='game_observation', provider='test',
                collected_at='2026-10-24T10:00:00+08:00', effective_at='2026-10-01', time_domain='game',
                time_precision='day', kind='profile_patch', payload={'name': 'E12', 'attributes': {'physical': {'speed': 40}}},
                expected_state_version=EMPTY_VERSION, source_record_id='initial', game_version='test-v1',
                adapter_version='test-v1', raw_reference='isolated:initial'))
        before, snapshot = actual.state_path(context).read_bytes(), actual.read_snapshot(context).to_dict()
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(semantics.create_app(data_dir=self.harness.root / 'actual-tasks', demo=False,
                    player_repository=actual, player_context=context)) as client:
                self.setup_record(client)
                client.post(self.base + '/decisions', json=self.body).raise_for_status()
                first = client.post(self.base + '/executions', json={**self.feedback(), 'status': 'CONFIRMED',
                    'actual_action': '自报训练后速度99，进球10；未独立核验'}).json()['record']
                client.post(self.base + '/executions', json={**self.feedback(), 'request_id': 'correct',
                    'corrects_execution_id': first['execution_id'], 'correction_reason': '恢复未知'}).raise_for_status()
                self.activity(client)
        self.assertEqual(actual.state_path(context).read_bytes(), before)
        self.assertEqual(actual.read_snapshot(context).to_dict(), snapshot)


if __name__ == '__main__':
    unittest.main()
