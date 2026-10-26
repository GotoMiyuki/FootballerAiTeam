"""C2.1: authoritative E events, explicit confirmation, revisions, isolation and replay."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from career_memory.models import CandidateProposal, MemoryWriteRequest
from evaluation import test_recommendations as rec_tests
from evaluation import test_session_semantics as semantics
from evaluation.player_fixtures import write_fixture
from player_data.models import PlayerContext
from player_data.repository import FixtureRepository


class CareerMemoryTests(unittest.TestCase):
    setUp = rec_tests.RecommendationTests.setUp
    tearDown = rec_tests.RecommendationTests.tearDown
    complete = rec_tests.RecommendationTests.complete

    def start(self, client):
        identity = self.complete(client)
        self.base = f'/api/missions/{identity}/career-memory'
        self.rec = client.get(f'/api/missions/{identity}/recommendations').json()['items'][0]
        self.action = f'/api/missions/{identity}/recommendations/{self.rec["recommendation_id"]}'
        self.context = self.rec['context']
        return identity

    def read(self, client):
        response = client.get(self.base)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['items']

    def sync(self, client):
        response = client.post(self.base + '/sync')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def user(self, **changes):
        return {**dict(context=self.context, request_id='memory-1', confirmed=True,
            operation='create', kind='preference', content='每天只安排20分钟'), **changes}

    def choice(self, **changes):
        return {**dict(context=self.context, request_id='decision-1', recommendation_revision=1,
            status='ADOPTED', reason='', previous_decision_id=None), **changes}

    def execution(self, **changes):
        return {**dict(context=self.context, request_id='execution-1', recommendation_revision=1,
            status='CONFIRMED', actual_action='自报尝试20分钟，速度99',
            window={'time_domain': 'game', 'label': '第3周'}), **changes}

    def test_empty_chat_and_a_goals_do_not_create_preferences_or_actions(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                identity = self.start(client)
                service = client.app.state.service
                service.message(service.store.get(identity), 'user', '不错，我采纳了，也执行了，以后每天10小时')
                before = self.harness.repository.read_snapshot().to_dict()
                inputs = service.store.inputs(identity)
                self.assertEqual(self.read(client), [])
                result = self.sync(client)
                self.assertEqual(result['created_memories'], 1)
                items = self.read(client)
                self.assertEqual([item['kind'] for item in items], ['recommendation'])
                self.assertEqual(items[0]['source']['event_type'], 'recommendation.created')
                self.assertEqual(items[0]['source']['recommendation_source'], self.rec['source'])
                self.assertEqual(items[0]['projection']['baseline_reference'], self.rec['applicability']['input_reference'])
                self.assertEqual(before, self.harness.repository.read_snapshot().to_dict())
                self.assertEqual(inputs, service.store.inputs(identity))
                self.assertEqual(self.sync(client)['consumed_events'], 0)

    def test_decisions_executions_corrections_and_read_time_missing_sync(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                self.start(client)
                first = client.post(self.action + '/decisions', json=self.choice()).json()['record']
                execution = client.post(self.action + '/executions', json=self.execution()).json()['record']
                self.assertEqual(self.sync(client)['created_memories'], 3)
                old = self.read(client)
                self.assertEqual({item['kind'] for item in old}, {'recommendation', 'decision', 'execution'})
                ref = next(item for item in old if item['kind'] == 'execution')
                self.assertEqual(ref['effective_time'], {'time_domain': 'game', 'precision': 'unspecified',
                    'effective_at': None, 'valid_window': '第3周'})
                self.assertEqual(ref['projection']['source_type'], 'user_reported')
                client.post(self.action + '/decisions', json=self.choice(request_id='reject', status='REJECTED',
                    previous_decision_id=first['decision_id'])).raise_for_status()
                client.post(self.action + '/executions', json=self.execution(request_id='correction', status='UNKNOWN',
                    corrects_execution_id=execution['execution_id'], correction_reason='记忆不清' * 400)).raise_for_status()
                service = client.app.state.service
                changes = service.store.db.total_changes
                checked = self.read(client)
                self.assertEqual(service.store.db.total_changes, changes)
                self.assertEqual([item['status'] for item in checked if item['kind'] != 'recommendation'], ['superseded', 'superseded'])
                self.assertEqual(self.sync(client)['created_memories'], 2)
                current = self.read(client)
                self.assertEqual(len(current), 5)
                latest_execution = [item for item in current if item['kind'] == 'execution'][-1]
                self.assertTrue(latest_execution['projection']['truncated'])
                self.assertEqual(len(latest_execution['projection']['correction_reason']), 1200)
                for kind in ['decision', 'execution']:
                    pair = [item for item in current if item['kind'] == kind]
                    self.assertEqual(pair[1]['supersedes_memory_id'], pair[0]['memory_id'])
                    self.assertEqual(pair[0]['content'], next(item for item in old if item['kind'] == kind)['content'])
                self.assertEqual(self.sync(client)['created_memories'], 0)

    def test_source_failure_applicability_and_retraction_are_checked_on_read(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                identity = self.start(client)
                self.sync(client)
                original = self.read(client)[0]
                write_fixture(self.harness.fixture, speed=99)
                changed = self.read(client)[0]
                self.assertEqual(changed['usage'], 'historical_only')
                self.assertEqual(changed['recommendation_validity'], 'needs_reassessment')
                self.assertEqual(changed['content'], original['content'])
                service = client.app.state.service
                mission = service.store.get(identity)
                mission.review.availability = 'UNAVAILABLE'
                service.store.save(mission)
                self.assertEqual(self.read(client)[0]['status'], 'retracted')
                service.fail(mission, 'source failed')
                self.assertEqual(self.sync(client)['created_memories'], 0)
                self.assertEqual(self.read(client)[0]['status'], 'retracted')

    def test_explicit_user_revisions_cas_retraction_and_concurrent_requests(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                identity = self.start(client)
                app = client.app.state.service.career_memory
                body = self.user(effective_time={'time_domain': 'game', 'precision': 'week', 'effective_at': '2026-W43'})
                request = MemoryWriteRequest(**body)
                with ThreadPoolExecutor(max_workers=4) as executor:
                    results = list(executor.map(lambda _: app.write(identity, request), range(4)))
                self.assertEqual(sum(created for _, created in results), 1)
                first = results[0][0]
                corrected = self.user(request_id='correct', operation='correct', memory_id=first.memory_id,
                    expected_revision=1, content='改为每周三次', reason='工作时间变化')
                second = client.post(self.base + '/user-records', json=corrected).json()['record']
                self.assertEqual(second['revision'], 2)
                self.assertEqual(second['corrects_revision'], 1)
                self.assertEqual(client.post(self.base + '/user-records', json={**corrected, 'request_id': 'stale'}).status_code, 409)
                retraction = self.user(request_id='retract', operation='retract', memory_id=first.memory_id,
                    expected_revision=2, reason='撤回偏好')
                client.post(self.base + '/user-records', json=retraction).raise_for_status()
                self.assertEqual([item['status'] for item in self.read(client)], ['superseded', 'superseded', 'retracted'])
                self.assertFalse(client.post(self.base + '/user-records', json=body).json()['created'])
                self.assertFalse(client.post(self.base + '/user-records', json=retraction).json()['created'])
                self.assertEqual(client.post(self.base + '/user-records', json={**body, 'content': 'different'}).status_code, 409)

    def test_model_candidate_requires_explicit_confirmation_and_never_auto_extracts(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                identity = self.start(client)
                app = client.app.state.service.career_memory
                proposal = CandidateProposal(context=self.context, candidate_id='candidate-1', kind='goal', content='候选目标')
                candidate, created = app.propose_candidate(identity, proposal)
                self.assertTrue(created)
                view = self.read(client)[0]
                self.assertEqual((view['status'], view['usage']), ('candidate', 'unconfirmed'))
                self.assertEqual(view['source']['type'], 'model_candidate')
                self.assertFalse(app.propose_candidate(identity, proposal)[1])
                with self.assertRaises(ValueError):
                    app.propose_candidate(identity, proposal.model_copy(update={'content': 'silent replacement'}))
                body = self.user(kind='goal', operation='confirm_candidate', memory_id=candidate.memory_id,
                    expected_revision=1, content='我明确确认的目标')
                self.assertEqual(client.post(self.base + '/user-records', json={**body, 'confirmed': False}).status_code, 422)
                self.assertEqual(client.post(self.base + '/user-records', json={**body, 'source': {'type': 'user_confirmed'}}).status_code, 422)
                accepted = client.post(self.base + '/user-records', json=body)
                self.assertEqual(accepted.status_code, 200, accepted.text)
                view = self.read(client)[1]
                self.assertEqual((view['status'], view['usage']), ('active', 'current'))
                self.assertEqual(view['source']['confirmed_candidate_revision'], 1)
                self.assertEqual(view['source']['candidate_id'], 'candidate-1')
                self.assertFalse(app.propose_candidate(identity, proposal)[1])
                self.assertEqual(self.read(client)[0]['status'], 'superseded')

    def test_context_filtering_and_e_reference_write_protection(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                identity = self.start(client)
                self.sync(client)
                imported = self.read(client)[0]
                self.assertEqual(client.post(self.base + '/user-records', json=self.user(operation='correct',
                    memory_id=imported['memory_id'], expected_revision=1, reason='不能更改来源')).status_code, 409)
                for field in ['career_id', 'branch_id', 'player_id']:
                    wrong = {**self.context, field: 'other'}
                    self.assertEqual(client.post(self.base + '/user-records', json=self.user(context=wrong)).status_code, 409)
                first = client.post(self.base + '/user-records', json=self.user()).json()['record']
                service = client.app.state.service
                other = PlayerContext(**{**self.context, 'branch_id': 'other'})
                service.player_repository = FixtureRepository(self.harness.fixture, context=other)
                service.player_context = other
                self.start(client)
                self.assertEqual(self.read(client), [])
                self.assertEqual(self.sync(client)['created_memories'], 1)
                self.assertTrue(all(item['context'] == other.to_dict() for item in self.read(client)))
                self.assertEqual(client.post(self.base + '/user-records', json=self.user(operation='correct',
                    memory_id=first['memory_id'], expected_revision=1, reason='跨分支')).status_code, 409)
                mission = service.store.get(identity)
                mission.input_reference.verification = 'UNVERIFIED'
                service.store.save(mission)
                self.assertEqual(client.get(f'/api/missions/{identity}/career-memory').status_code, 409)

    def test_event_projection_and_user_transaction_failures_roll_back(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                identity = self.start(client)
                app = client.app.state.service.career_memory
                db = app.service.store.db
                with db:
                    db.execute("CREATE TRIGGER fail_memory BEFORE INSERT ON career_memories BEGIN SELECT RAISE(ABORT,'memory failure'); END")
                with self.assertRaises(sqlite3.IntegrityError):
                    app.sync(identity)
                with self.assertRaises(sqlite3.IntegrityError):
                    app.write(identity, MemoryWriteRequest(**self.user()))
                self.assertEqual(self.read(client), [])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM career_memory_events').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM career_memory_requests').fetchone()[0], 0)
                with db:
                    db.execute('DROP TRIGGER fail_memory')
                self.assertEqual(self.sync(client)['created_memories'], 1)
                self.assertTrue(client.post(self.base + '/user-records', json=self.user()).json()['created'])

    def test_missing_or_tampered_source_never_remains_current_and_get_is_read_only(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                self.start(client)
                self.sync(client)
                db = client.app.state.service.store.db
                event = db.execute('SELECT sequence,payload FROM recommendation_events LIMIT 1').fetchone()
                tampered = json.loads(event[1])
                tampered['timestamp'] = '2026-10-24T12:00:00+08:00'
                with db:
                    db.execute('UPDATE recommendation_events SET payload=? WHERE sequence=?', (json.dumps(tampered), event[0]))
                self.assertEqual(self.read(client)[0]['status'], 'retracted')
                self.assertEqual(client.post(self.base + '/sync').status_code, 409)
                with db:
                    db.execute('DELETE FROM recommendation_events')
                changes = db.total_changes
                view = self.read(client)[0]
                self.assertFalse(view['source_checked'])
                self.assertEqual(view['usage'], 'historical_only')
                self.assertEqual(db.total_changes, changes)

    def test_restart_and_replay_restore_records_without_model_or_latest_a_writes(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                self.start(client)
                self.sync(client)
                body = self.user()
                first = client.post(self.base + '/user-records', json=body).json()
                records = self.read(client)
                calls = list(self.harness.calls)
            with TestClient(self.harness.app()) as client:
                self.assertEqual(self.read(client), records)
                replay = client.post(self.base + '/user-records', json=body).json()
                self.assertEqual(replay['record'], first['record'])
                self.assertFalse(replay['created'])
                self.assertEqual(self.sync(client)['consumed_events'], 0)
                self.assertEqual(self.harness.calls, calls)

    def test_event_pagination_does_not_drop_or_duplicate_decisions(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                self.start(client)
                previous = None
                for index in range(201):
                    result = client.post(self.action + '/decisions', json=self.choice(request_id=f'choice-{index}',
                        status='DEFERRED', previous_decision_id=previous))
                    result.raise_for_status()
                    previous = result.json()['record']['decision_id']
                result = self.sync(client)
                self.assertEqual(result['consumed_events'], 202)
                self.assertEqual(result['created_memories'], 202)
                items = self.read(client)
                self.assertEqual(len(items), 202)
                self.assertEqual(sum(item['usage'] == 'current' for item in items), 2)
                self.assertEqual(self.sync(client)['created_memories'], 0)

    def test_invalid_times_and_sources_do_not_create_confirmed_memory(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                self.start(client)
                for extra in [{'source_type': 'e_record'}, {'confirmed': False}, {'content': '  '},
                        {'effective_time': {'time_domain': 'unknown', 'effective_at': '2026-10-24'}},
                        {'effective_time': {'time_domain': 'real', 'precision': 'instant', 'effective_at': '2026-10-24T10:00:00'}},
                        {'effective_time': {'time_domain': 'game', 'precision': 'week', 'effective_at': '2026-W99'}}]:
                    self.assertEqual(client.post(self.base + '/user-records', json=self.user(**extra)).status_code, 422)
                self.assertEqual(self.read(client), [])

    def test_actual_a_bytes_and_frozen_mission_inputs_are_unchanged(self):
        from player_data.json_repository import JsonPlayerRepository, EMPTY_VERSION
        from player_data.imports import Observation, ObservationImporter
        context = PlayerContext('c21-test', 'main', 'p')
        actual = JsonPlayerRepository(self.harness.root / 'actual', fixture_root=self.harness.fixture, context=context)
        ObservationImporter(actual, source_type='game_observation', provider='test').import_observation(Observation(
            context=context, observation_id='initial', source_type='game_observation', provider='test',
            collected_at='2026-10-24T10:00:00+08:00', effective_at='2026-10-01', time_domain='game', time_precision='day',
            kind='profile_patch', payload={'name': 'C21', 'attributes': {'physical': {'speed': 40}}, 'long_term_goals': ['A档案目标']},
            expected_state_version=EMPTY_VERSION, source_record_id='initial', game_version='test-v1', adapter_version='test-v1', raw_reference='test:initial'))
        before = actual.state_path(context).read_bytes()
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(semantics.create_app(data_dir=self.harness.root / 'actual-tasks', demo=False,
                    player_repository=actual, player_context=context)) as client:
                identity = self.start(client)
                inputs = deepcopy(client.app.state.service.store.inputs(identity))
                client.post(self.action + '/executions', json=self.execution()).raise_for_status()
                client.post(self.base + '/user-records', json=self.user(kind='goal', content='C当前用户目标')).raise_for_status()
                self.sync(client)
                self.read(client)
                self.assertEqual(client.app.state.service.store.inputs(identity), inputs)
        self.assertEqual(actual.state_path(context).read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
