"""C1.2 application/API persistence, provenance, idempotency and actual prompt tests."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from evaluation import test_session_semantics as semantics
from evaluation.player_fixtures import write_fixture
from player_data.models import PlayerContext, PlayerDataError


class ReevaluationTests(unittest.TestCase):
    def setUp(self):
        self.harness = semantics.SessionSemanticsTests()
        self.harness.setUp()

    def tearDown(self):
        self.harness.tearDown()

    def parent(self, client):
        identity = self.harness.start(client)
        return identity, self.harness.complete(client, identity)

    def request(self, parent, **changes):
        return {'parent_mission_id': parent, 'conversation_id': 'conv_session', 'request_id': 'request_one',
            'reason': '新增比赛，训练时间改变', 'content': 'UNIQUE_NEW_CONSTRAINT_20_MIN：每天只能训练20分钟',
            'include_report': True, 'selected_results': [{'subtask_id': 'training', 'version': 1}], **changes}

    def post(self, client, body):
        response = client.post('/api/mission-continuations', json=body)
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def test_new_identity_freezes_v2_even_if_v3_arrives_before_execution(self):
        from backend.store import Store
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                parent, old = self.parent(client)
                service = client.app.state.service
                old_report = service.store.report(parent)
                parent_events = service.store.events(parent, 0)
                write_fixture(self.harness.fixture, speed=73)
                v2 = self.harness.repository.read_snapshot().metadata['state_version']
                with patch.object(service, '_enqueue') as enqueue:
                    accepted = self.post(client, self.request(parent))
                    enqueue.assert_called_once()
                child = accepted['mission_id']
                self.assertNotEqual(parent, child)
                self.assertTrue(accepted['created'])
                child_view = service.store.get(child)
                self.assertEqual(child_view.input_reference.state_version, v2)
                self.assertEqual(child_view.lineage.parent_mission_id, parent)
                self.assertEqual([r.kind for r in child_view.lineage.history_references], ['report', 'result'])
                fixed = service.store.inputs(child)
                self.assertEqual(fixed['player_snapshot']['profile']['attributes']['physical']['speed'], 73)
                self.assertIn('原球员速度：44', fixed['continuation_context']['report']['text'])
                self.assertEqual(fixed['continuation_context']['parent_input_reference'], old['input_reference'])
                reopened = Store(self.harness.root / 'tasks' / 'workspace.sqlite3')
                try:
                    self.assertEqual(reopened.inputs(child), fixed)
                finally:
                    reopened.close()
                write_fixture(self.harness.fixture, speed=99)
                with patch.object(self.harness.repository, 'read_snapshot', side_effect=AssertionError('No fresh read during execute/retry')):
                    duplicate = self.post(client, self.request(parent))
                    self.assertFalse(duplicate['created'])
                    self.assertEqual(duplicate['mission_id'], child)
                    self.assertEqual(duplicate['message_id'], accepted['message_id'])
                    service._execute(child)
                    complete = self.harness.complete(client, child)
                self.assertEqual(complete['input_reference']['state_version'], v2)
                self.assertIn('原球员速度：73', service.store.report(child))
                self.assertEqual(service.store.report(parent), old_report)
                self.assertEqual(service.store.events(parent, 0), parent_events)
                self.assertEqual(service.store.get(parent).input_reference.model_dump(), old['input_reference'])
                self.assertEqual(len(service.store.missions()), 2)
                self.assertEqual(service.store.get(child).plan.subtasks[0].result_version, 1)
                self.assertEqual(client.get('/api/missions/' + child).json()['lineage']['parent_mission_id'], parent)
                self.assertNotIn('player_snapshot', str(client.get('/api/missions/' + child).json()))

    def test_creation_request_survives_restart_and_conflicting_content_is_rejected(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                parent, _ = self.parent(client)
                accepted = self.post(client, self.request(parent))
                self.harness.wait(client, accepted['mission_id'], 'BLOCKED')
                old_calls = list(self.harness.calls)
            with TestClient(self.harness.app()) as client:
                service = client.app.state.service
                old_events = service.store.events(accepted['mission_id'], 0)
                with patch.object(self.harness.repository, 'read_snapshot', side_effect=PlayerDataError('missing_data', '最新数据不可用')):
                    same = self.post(client, self.request(parent))
                    self.assertFalse(same['created'])
                    self.assertEqual(same['mission_id'], accepted['mission_id'])
                    self.assertEqual(client.post('/api/mission-continuations', json=self.request(parent, content='different')).status_code, 409)
                self.assertEqual(service.store.events(accepted['mission_id'], 0), old_events)
                self.assertEqual(len(service.store.missions()), 2)
                self.assertEqual(self.harness.calls, old_calls)

    def test_concurrent_duplicate_submissions_create_and_enqueue_once(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                parent, _ = self.parent(client)
                service = client.app.state.service
                with patch.object(service, '_enqueue') as enqueue, patch.object(self.harness.repository, 'read_snapshot', wraps=self.harness.repository.read_snapshot) as reads:
                    with ThreadPoolExecutor(max_workers=6) as executor:
                        answers = list(executor.map(lambda _: self.post(client, self.request(parent)), range(6)))
                    self.assertEqual(sum(answer['created'] for answer in answers), 1)
                    self.assertEqual(len({answer['mission_id'] for answer in answers}), 1)
                    self.assertEqual(len({answer['message_id'] for answer in answers}), 1)
                    enqueue.assert_called_once()
                    reads.assert_called_once()

    def test_create_transaction_rolls_back_every_record_on_failure(self):
        from backend.models import ContinuationRequest
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                parent, _ = self.parent(client)
                service = client.app.state.service
                tables = ('missions', 'mission_inputs', 'messages', 'events', 'creation_requests')
                before = {table: service.store.db.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in tables}
                with service.store.db:
                    service.store.db.execute("CREATE TRIGGER fail_creation BEFORE INSERT ON creation_requests BEGIN SELECT RAISE(ABORT,'fixture transaction failure'); END")
                with patch.object(service, '_enqueue') as enqueue:
                    with self.assertRaises(sqlite3.IntegrityError):
                        service.create_continuation(ContinuationRequest(**self.request(parent)))
                    enqueue.assert_not_called()
                after = {table: service.store.db.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in tables}
                self.assertEqual(after, before)

    def test_invalid_context_history_and_external_references_create_nothing(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                parent, old = self.parent(client)
                service = client.app.state.service
                invalid = [self.request(parent, conversation_id='other'), self.request(parent, operation='retry'),
                    self.request(parent, selected_results=[{'subtask_id': 'missing', 'version': 1}]),
                    self.request(parent, selected_results=[{'subtask_id': 'training', 'version': 7}]),
                    self.request(parent, selected_results=[{'subtask_id': 'training', 'version': 1}] * 2),
                    self.request(parent, recommendation_id='unverified-recommendation'), self.request(parent, assessment_id='unverified-assessment'),
                    self.request(parent, reason='   ')]
                for key in ('career_id', 'branch_id', 'player_id'):
                    invalid.append(self.request(parent, player_context={**old['input_reference']['context'], key: 'other'}))
                message_count = len(service.store.messages('conv_session'))
                for body in invalid:
                    with self.subTest(body=body):
                        self.assertEqual(client.post('/api/mission-continuations', json=body).status_code, 409)
                self.assertEqual(client.post('/api/mission-continuations', json=self.request('missing')).status_code, 404)
                self.assertEqual(len(service.store.missions()), 1)
                self.assertEqual(len(service.store.messages('conv_session')), message_count)
                from backend.runtime import inspect_checkpoint
                state = inspect_checkpoint(service.checkpoint_path, parent).values
                for status in ('INVALIDATED', 'UNVALIDATED'):
                    bad = deepcopy(state)
                    if status == 'INVALIDATED':
                        bad['subtask_results']['training']['validity'] = status
                    else:
                        bad['subtask_results']['training']['validated'] = False
                    with patch('backend.runtime.inspect_checkpoint', return_value=SimpleNamespace(values=bad)):
                        self.assertEqual(client.post('/api/mission-continuations', json=self.request(parent)).status_code, 409)
                for field in ('snapshot_id', 'state_version'):
                    bad = deepcopy(state)
                    bad['player_snapshot']['metadata'][field] = 'mismatched-source'
                    with patch('backend.runtime.inspect_checkpoint', return_value=SimpleNamespace(values=bad)):
                        self.assertEqual(client.post('/api/mission-continuations', json=self.request(parent)).status_code, 409)
                self.assertEqual(len(service.store.missions()), 1)

    def test_latest_read_failure_and_failed_child_preserve_parent_report(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                parent, old = self.parent(client)
                service = client.app.state.service
                report = service.store.report(parent)
                with patch.object(self.harness.repository, 'read_snapshot', side_effect=PlayerDataError('missing_data', 'Latest input is unavailable')):
                    self.assertEqual(client.post('/api/mission-continuations', json=self.request(parent)).status_code, 409)
                self.assertEqual(len(service.store.missions()), 1)
                self.assertIsNone(service.store.creation_result('request_one', 'not-created'))
                with patch('backend.runtime.create_runtime', side_effect=RuntimeError('fixture child failure')), self.assertLogs(level='ERROR'):
                    accepted = self.post(client, self.request(parent))
                    child = self.harness.wait(client, accepted['mission_id'], 'FAILED')
                self.assertEqual(child['lineage']['parent_mission_id'], parent)
                current = client.get('/api/missions/' + parent).json()
                for key in ('status', 'report', 'review', 'result', 'input_reference', 'sequence'):
                    self.assertEqual(current[key], old[key])
                self.assertEqual(service.store.report(parent), report)

    def test_application_retry_is_a_new_task_and_binds_parent_context(self):
        from backend.continuations import Continuation
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                parent, _ = self.parent(client)
                service = client.app.state.service
                mission = service.store.get(parent)
                service.fail(mission, 'fixture failure')
                service.player_context = PlayerContext('different-career', 'main', 'different-player')
                application_request = Continuation(parent_mission_id=parent, conversation_id='conv_session', request_id='application-retry',
                    reason='明确重新运行', content='独立重试', operation='retry', include_report=False)
                message, child, created = service.create_continuation(application_request)
                self.assertTrue(created)
                self.assertNotEqual(child, parent)
                paused = self.harness.wait(client, child, 'BLOCKED')
                self.assertEqual(paused['lineage']['operation'], 'retry')
                self.assertEqual(paused['input_reference']['context'], mission.input_reference.context)
                self.assertEqual(service.store.get(parent).status, 'FAILED')
                self.assertEqual(service.create_continuation(application_request), (message, child, False))

    def test_selected_history_and_new_requirements_enter_real_manager_and_coach_prompts(self):
        from graph import build_graph
        from langgraph.checkpoint.sqlite import SqliteSaver
        from agents.manager import create_manager_node, create_manager_loop_nodes
        from agents.coach import create_coach_node
        from evaluation.test_execution_outcomes import Model
        from evaluation.test_looping_plan import complete_output, task
        from loop_contracts import normalise_review_result
        from execution_context import input_fingerprint
        from report_validation import accept_document
        models = {}
        initial_states = []
        def actual_runtime(path, on_start):
            mission = {'objective': '重新制定20分钟训练安排', 'primary_goal': '重新制定20分钟训练安排',
                'output_type': 'report', 'constraints': ['每天只有20分钟'], 'context': {}, 'confidence': 10}
            plan = {'objective': mission['objective'], 'subtasks': [task('training')], 'information_gaps': []}
            models['manager'] = Model(json.dumps(mission), '[]', json.dumps(plan))
            models['coach'] = Model(json.dumps(complete_output('new analysis')))
            manager_node, checkpoint_node, manager = create_manager_node(models['manager'])
            revision, replan = create_manager_loop_nodes(manager)
            def reviewed(state):
                review = normalise_review_result({'decision': 'PASS', 'findings': [], 'reviewed_subtasks': ['training']}, state['subtasks'])
                return {'review_v2': review, 'review': {'decision': 'PASS'}}
            nodes = {'manager': manager_node, 'intent_checkpoint': checkpoint_node, 'manager_revision': revision,
                'manager_replan': replan, 'reviewer': reviewed, 'coach': create_coach_node(models['coach']),
                'analyst': lambda state: {}, 'nutrition': lambda state: {}, 'career': lambda state: {},
                'document': lambda state: accept_document(state, '# Test\n' + state['mission']['primary_goal'] + '\n依据与限制：测试')}
            def wrap(name, fn):
                def run(state):
                    if name == 'manager':
                        initial_states.append(deepcopy(state))
                    on_start(name, state)
                    return fn(state)
                run._telemetry_agent = getattr(fn, '_telemetry_agent', None)
                return run
            connection = sqlite3.connect(str(path), check_same_thread=False)
            return build_graph({name: wrap(name, fn) for name, fn in nodes.items()},
                checkpointer=SqliteSaver(connection), interrupt_before=['document']), connection
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.harness.app()) as client:
                parent, _ = self.parent(client)
                write_fixture(self.harness.fixture, speed=73)
                with patch('backend.runtime.create_runtime', side_effect=actual_runtime):
                    accepted = self.post(client, self.request(parent))
                    self.harness.wait(client, accepted['mission_id'], 'BLOCKED')
                service = client.app.state.service
                history = service.store.inputs(accepted['mission_id'])['continuation_context']
                for model in models.values():
                    prompt = '\n'.join(str(message.content) for call in model.prompts for message in call)
                    self.assertIn('UNIQUE_NEW_CONSTRAINT_20_MIN', prompt)
                    self.assertIn(parent, prompt)
                    self.assertIn('原球员速度：44', prompt)
                    self.assertIn(history['results'][0]['content_hash'], prompt)
                self.assertFalse(initial_states[0]['subtask_results'])
                self.assertFalse(initial_states[0]['domain_outputs'])
                self.assertEqual(initial_states[0]['player_profile']['attributes']['physical']['speed'], 73)
                from backend.runtime import inspect_checkpoint
                state = inspect_checkpoint(service.checkpoint_path, accepted['mission_id']).values
                current_task = state['plan']['subtasks'][0]
                self.assertEqual(state['subtask_results']['training']['input_material']['continuation_context'], history)
                original_fingerprint = input_fingerprint(state, current_task)
                state['continuation_context']['results'][0]['payload']['notes'] = ['changed historical evidence']
                self.assertNotEqual(input_fingerprint(state, current_task), original_fingerprint)


if __name__ == '__main__':
    unittest.main()
