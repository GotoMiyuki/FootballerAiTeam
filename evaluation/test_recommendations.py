"""E1.1 source mapping, idempotency, applicability, scoped history and event transactions."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from evaluation import test_session_semantics as semantics
from evaluation.player_fixtures import write_fixture
from player_data.models import PlayerContext, PlayerDataError


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.harness = semantics.SessionSemanticsTests()
        self.harness.setUp()

    def tearDown(self):
        self.harness.tearDown()

    def complete(self, client):
        identity = self.harness.start(client)
        self.harness.complete(client, identity)
        return identity

    def read(self, client, identity):
        response = client.get(f'/api/missions/{identity}/recommendations')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_delivery_projects_fixed_source_and_unknown_execution_without_touching_facts(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                snapshot = self.harness.repository.read_snapshot().to_dict()
                identity = self.harness.start(client)
                self.harness.wait(client, identity, 'BLOCKED')
                self.assertEqual(self.read(client, identity)['items'], [])
                self.harness.complete(client, identity)
                service = client.app.state.service
                mission = service.store.get(identity)
                result = self.read(client, identity)
                self.assertEqual(result['availability'], 'AVAILABLE')
                self.assertEqual(len(result['items']), 1)
                record = result['items'][0]
                self.assertEqual(record['content']['text'], 'training')
                self.assertEqual(record['source']['mission_id'], identity)
                self.assertEqual(record['source']['subtask_id'], 'training')
                self.assertEqual(record['source']['payload_position'], '/focus_areas/0')
                self.assertEqual(record['source']['result_version'], record['source']['reviewed_result_version'])
                self.assertEqual(record['context'], snapshot['context'])
                self.assertEqual(record['applicability']['input_reference'], mission.input_reference.model_dump())
                self.assertEqual(record['evaluation_spec']['baseline_reference'], mission.input_reference.model_dump())
                self.assertEqual(record['execution_support']['status'], 'pending_verification')
                self.assertEqual(record['evaluation_spec']['status'], 'undefined')
                self.assertIsNone(record['evaluation_spec']['observation_window'])
                self.assertEqual(record['evaluation_spec']['metrics'], [])
                self.assertEqual(record['validity'], 'current')
                self.assertEqual(snapshot, self.harness.repository.read_snapshot().to_dict())
                self.assertNotIn('player_snapshot', str(result))
                self.assertNotIn('tool_call_log', str(result))
                self.assertNotIn('checkpoint_path', str(result))
                events = client.get(f'/api/missions/{identity}/recommendation-events').json()
                self.assertEqual([event['type'] for event in events], ['recommendation.created'])
                self.assertEqual(events[0]['context'], record['context'])
                self.assertEqual(client.get(f'/api/missions/{identity}/recommendation-events?after={events[0]["sequence"]}').json(), [])
                self.assertEqual(client.get(f'/api/missions/{identity}/recommendation-events?after=-1').status_code, 422)

    def test_concurrent_reprojection_restart_and_get_are_idempotent_and_read_only(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                identity = self.complete(client)
                service = client.app.state.service
                before = self.read(client, identity)
                events = service.recommendations.repository.events(identity)
                calls = list(self.harness.calls)
                with ThreadPoolExecutor(max_workers=4) as executor:
                    list(executor.map(lambda _: service.recommendations.project_mission(identity), range(4)))
                public_events = service.store.events(identity, 0)
                private_records = service.recommendations.repository.records(identity)
                with patch('backend.runtime.create_runtime', side_effect=AssertionError('GET must not execute')):
                    for _ in range(3):
                        self.assertEqual(self.read(client, identity), before)
                self.assertEqual(service.store.events(identity, 0), public_events)
                self.assertEqual(service.recommendations.repository.records(identity), private_records)
                self.assertEqual(service.recommendations.repository.events(identity), events)
                self.assertEqual(self.harness.calls, calls)
            with TestClient(self.harness.app()) as client:
                self.assertEqual(self.read(client, identity), before)
                self.assertEqual(client.app.state.service.recommendations.repository.events(identity), events)
                self.assertEqual(self.harness.calls, calls)

    def test_current_applicability_changes_without_rewriting_historical_record_or_report(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                identity = self.complete(client)
                service = client.app.state.service
                original = self.read(client, identity)['items'][0]
                stored = service.recommendations.repository.records(identity)
                report = service.store.report(identity)
                write_fixture(self.harness.fixture, speed=99)
                updated = self.read(client, identity)['items'][0]
                self.assertEqual(updated['validity'], 'needs_reassessment')
                self.assertNotEqual(updated['checked_state_version'], original['checked_state_version'])
                self.assertEqual(updated['applicability'], original['applicability'])
                self.assertEqual(updated['content'], original['content'])
                self.assertEqual(service.recommendations.repository.records(identity), stored)
                self.assertEqual(service.store.report(identity), report)
                with patch.object(self.harness.repository, 'read_snapshot', side_effect=PlayerDataError('missing_data', 'unavailable')):
                    self.assertEqual(self.read(client, identity)['items'][0]['validity'], 'needs_reassessment')
                with patch.object(self.harness.repository, 'read_snapshot', return_value=SimpleNamespace(to_dict=lambda: {'context': PlayerContext('other', 'main', 'p').to_dict()})):
                    self.assertEqual(self.read(client, identity)['items'][0]['validity'], 'needs_reassessment')

    def test_failed_unreviewed_invalidated_and_tampered_sources_never_return_current(self):
        from backend.runtime import inspect_checkpoint
        from career_actions.sources import SourceUnavailable
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                identity = self.complete(client)
                service = client.app.state.service
                original = inspect_checkpoint(service.checkpoint_path, identity).values
                count = len(service.recommendations.repository.events(identity))
                for failure in ('FAILED', 'INVALIDATED', 'UNVALIDATED', 'UNAVAILABLE', 'wrong_review', 'tampered_payload'):
                    bad = deepcopy(original)
                    result = bad['subtask_results']['training']
                    if failure == 'FAILED':
                        result['status'] = 'FAILED'
                    elif failure == 'INVALIDATED':
                        result['validity'] = 'INVALIDATED'
                    elif failure == 'UNVALIDATED':
                        result['validated'] = False
                    elif failure == 'UNAVAILABLE':
                        bad['review_v2']['availability'] = 'UNAVAILABLE'
                    elif failure == 'wrong_review':
                        bad['review_v2']['reviewed_versions']['training'] = 7
                    else:
                        result['observation']['result']['focus_areas'] = ['different text in same source version']
                    with patch('backend.runtime.inspect_checkpoint', return_value=SimpleNamespace(values=bad)):
                        if failure != 'tampered_payload':
                            with self.assertRaises(SourceUnavailable):
                                service.recommendations.project_mission(identity)
                        self.assertNotEqual(self.read(client, identity)['items'][0]['validity'], 'current')
                mission = service.store.get(identity)
                mission.review.availability = 'UNAVAILABLE'
                service.store.save(mission)
                self.assertEqual(self.read(client, identity)['items'][0]['validity'], 'withdrawn')
                self.assertEqual(len(service.recommendations.repository.events(identity)), count)

    def test_corrupt_stored_content_baseline_or_review_never_passes_current_source_verification(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                identity = self.complete(client)
                service = client.app.state.service
                original, _ = service.recommendations.repository.records(identity)[0]
                for kind in ('content', 'baseline', 'review'):
                    corrupt = original.model_copy(deep=True)
                    if kind == 'content':
                        corrupt.content.text = 'not present in original focus_areas'
                    elif kind == 'baseline':
                        corrupt.applicability.input_reference.state_version = 'different-baseline'
                    else:
                        corrupt.source.reviewed_result_version += 1
                    with service.store.db:
                        service.store.db.execute('UPDATE recommendations SET payload=? WHERE id=?',
                            (corrupt.model_dump_json(), original.recommendation_id))
                    self.assertEqual(self.read(client, identity)['items'][0]['validity'], 'withdrawn')

    def test_projection_transaction_rollback_does_not_withdraw_valid_parent_report(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                identity = self.harness.start(client)
                self.harness.wait(client, identity, 'BLOCKED')
                service = client.app.state.service
                with service.store.db:
                    service.store.db.execute("CREATE TRIGGER fail_recommendation_event BEFORE INSERT ON recommendation_events BEGIN SELECT RAISE(ABORT,'fixture event failure'); END")
                with self.assertLogs(level='ERROR'):
                    self.harness.complete(client, identity)
                self.assertEqual(service.store.get(identity).status, 'COMPLETED')
                self.assertTrue(service.store.report(identity))
                self.assertEqual(service.recommendations.repository.records(identity), [])
                self.assertEqual(service.recommendations.repository.events(identity), [])
                self.assertEqual(self.read(client, identity)['availability'], 'UNAVAILABLE')

    def test_context_isolation_source_failure_retains_history_and_withdraws_once(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                identity = self.complete(client)
                service = client.app.state.service
                record = self.read(client, identity)['items'][0]
                for field in ('career_id', 'branch_id', 'player_id'):
                    bad = PlayerContext(**{**record['context'], field: 'other'})
                    with self.assertRaises(ValueError):
                        service.recommendations.get(record['recommendation_id'], bad)
                self.assertEqual(service.recommendations.get(record['recommendation_id'], PlayerContext(**record['context'])).recommendation_id, record['recommendation_id'])
                mission = service.store.get(identity)
                service.fail(mission, 'fixture failure')
                service.fail(mission, 'fixture failure')
                current = self.read(client, identity)['items'][0]
                self.assertEqual(current['validity'], 'withdrawn')
                self.assertEqual(current['content'], record['content'])
                self.assertEqual([event.type for event in service.recommendations.repository.events(identity)], ['recommendation.created', 'recommendation.withdrawn'])
                self.assertEqual(client.get('/api/missions/missing/recommendations').status_code, 404)

    def test_demo_does_not_project_specialist_or_report_text_as_verified_suggestions(self):
        with TestClient(semantics.create_app(data_dir=self.harness.root / 'demo', demo=True, demo_delay=0,
                player_repository=self.harness.repository)) as client:
            identity = self.harness.start(client)
            self.harness.wait(client, identity, 'COMPLETED')
            self.assertEqual(self.read(client, identity)['items'], [])
            self.assertEqual(self.read(client, identity)['availability'], 'UNAVAILABLE')

    def test_actual_repository_projection_preserves_fact_hash_and_does_not_certify_game_support(self):
        from player_data.json_repository import JsonPlayerRepository, EMPTY_VERSION
        from player_data.imports import Observation, ObservationImporter
        context = PlayerContext('isolated-actual', 'main', 'p')
        actual = JsonPlayerRepository(self.harness.root / 'actual', fixture_root=self.harness.fixture, context=context)
        importer = ObservationImporter(actual, source_type='game_observation', provider='isolated-collector')
        initial = importer.import_observation(Observation(context=context, observation_id='test-observation',
            source_type='game_observation', provider='isolated-collector',
            collected_at='2026-10-02T10:00:00+08:00', effective_at='2026-10-01',
            time_domain='game', time_precision='day', kind='profile_patch',
            payload={'name': 'Actual Repository Test', 'attributes': {'physical': {'speed': 44}}},
            expected_state_version=EMPTY_VERSION, source_record_id='test-observation',
            game_version='test-game-v1', adapter_version='test-v1', raw_reference='isolated-record:test'))
        unapplied = importer.import_observation(Observation(context=context, observation_id='older-unapplied',
            source_type='game_observation', provider='isolated-collector',
            collected_at='2026-10-02T10:01:00+08:00', effective_at='2026-09-01',
            time_domain='game', time_precision='day', kind='profile_patch', payload={'name': 'Must not apply'},
            expected_state_version=initial.state_version, source_record_id='older-unapplied',
            game_version='unapplied-game-version', adapter_version='test-v1', raw_reference='isolated-record:older'))
        self.assertEqual(unapplied.status, 'RECORDED_ONLY')
        before = actual.state_path(context).read_bytes()
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(semantics.create_app(data_dir=self.harness.root / 'actual-tasks', demo=False,
                    player_repository=actual, player_context=context)) as client:
                identity = self.complete(client)
                record = self.read(client, identity)['items'][0]
                self.assertEqual(record['context'], context.to_dict())
                self.assertEqual(record['applicability']['game_versions'], ['test-game-v1'])
                self.assertEqual(record['execution_support']['status'], 'pending_verification')
                self.assertEqual(record['evaluation_spec']['metrics'], [])
        self.assertEqual(actual.state_path(context).read_bytes(), before)

    def test_same_agent_multiple_tasks_are_distinct_and_new_versions_supersede_without_overwrite(self):
        from career_actions.models import Recommendation
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                identity = self.complete(client)
                application = client.app.state.service.recommendations
                source = application.reader.read(identity)
                second = deepcopy(source['tasks'][0])
                second['task']['id'] = 'training_second'
                second['result']['subtask_id'] = 'training_second'
                source['state']['review_v2']['reviewed_versions']['training_second'] = 1
                source['tasks'].append(second)
                application._project(source)
                stored = application.repository.records(identity)
                self.assertEqual(len(stored), 2)
                self.assertEqual({row[0].source.subtask_id for row in stored}, {'training', 'training_second'})
                self.assertEqual(len({row[0].recommendation_id for row in stored}), 2)
                old = stored[0][0]
                source['tasks'] = source['tasks'][:1]
                source['tasks'][0]['result']['source_version'] = 2
                source['state']['review_v2']['reviewed_versions']['training'] = 2
                source['tasks'][0]['payload']['focus_areas'] = ['new focus']
                application._project(source)
                stored = application.repository.records(identity)
                self.assertEqual(len(stored), 3)
                self.assertEqual(next(row[0] for row in stored if row[0].recommendation_id == old.recommendation_id), old)
                self.assertEqual([row[1] for row in stored].count('current'), 1)
                new = next(row[0] for row in stored if row[1] == 'current')
                self.assertEqual(new.source.result_version, 2)
                self.assertNotEqual(new.recommendation_id, old.recommendation_id)
                bad = Recommendation.model_validate(new.model_dump())
                bad.content.text = 'changed text in same version'
                with self.assertRaises(ValueError):
                    application.repository.save_projection(identity, [bad])
                self.assertEqual(application.repository.records(identity), stored)


if __name__ == '__main__':
    unittest.main()
