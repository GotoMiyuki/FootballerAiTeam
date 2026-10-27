"""M4.2: request effects counted across HTTP concurrency, rollback and restart."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from backend.store import Store
from evaluation import test_session_semantics as sessions
from evaluation import test_cli_persistence as cli_fixtures
from evaluation.request_fixtures import input_body


class RequestIdentityTests(unittest.TestCase):
    def setUp(self):
        self.h = sessions.SessionSemanticsTests()
        self.h.setUp()
        self.addCleanup(self.h.tearDown)
        self.runtime_patch = patch('backend.runtime.create_runtime', side_effect=self.h.runtime)
        self.runtime_patch.start()
        self.addCleanup(self.runtime_patch.stop)

    def new(self, request_id='new-one', **extra):
        return {'request_id': request_id, 'conversation_id': 'conv_session', 'content': 'M42 request effects', **extra}

    def counts(self, service):
        return {table: service.store.db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
            for table in ['missions', 'mission_inputs', 'messages', 'events', 'creation_requests', 'operations', 'request_receipts']}

    def test_concurrent_creation_and_restart_replay_never_read_latest_or_enqueue_twice(self):
        with TestClient(self.h.app()) as client:
            service = client.app.state.service
            with patch.object(service, '_enqueue') as enqueue, patch.object(self.h.repository, 'read_snapshot', wraps=self.h.repository.read_snapshot) as read:
                with ThreadPoolExecutor(max_workers=6) as pool:
                    replies = list(pool.map(lambda _: client.post('/api/messages', json=self.new()), range(6)))
                self.assertTrue(all(r.status_code == 202 for r in replies))
                values = [r.json() for r in replies]
                self.assertEqual(len({r['mission_id'] for r in values}), 1)
                self.assertEqual(len({r['message_id'] for r in values}), 1)
                self.assertEqual(len({r['operation_id'] for r in values}), 1)
                self.assertEqual(sum(not r['replayed'] for r in values), 1)
                enqueue.assert_called_once()
                read.assert_called_once()
            identity = values[0]['mission_id']
            before = self.counts(service)
        with TestClient(self.h.app()) as client:
            service = client.app.state.service
            with patch.object(service, '_enqueue') as enqueue, patch.object(self.h.repository, 'read_snapshot', side_effect=AssertionError('No latest input on replay')):
                replay = client.post('/api/messages', json=self.new())
                self.assertEqual(replay.status_code, 202, replay.text)
                self.assertTrue(replay.json()['replayed'])
                self.assertEqual(replay.json()['mission_id'], identity)
                self.assertEqual(client.post('/api/messages', json=self.new(content='different')).status_code, 409)
                enqueue.assert_not_called()
            self.assertEqual(service.store.get(identity).status, 'FAILED')
            operation = client.get(f'/api/missions/{identity}/operations').json()[0]
            self.assertEqual((operation['status'], operation['error']), ('FAILED', 'INTERRUPTED'))
            for table in ['missions', 'mission_inputs', 'messages', 'creation_requests', 'operations', 'request_receipts']:
                self.assertEqual(self.counts(service)[table], before[table])
            self.assertEqual(self.h.calls, [])

    def test_explanation_has_independent_identity_and_once_only_model_effect(self):
        with TestClient(self.h.app()) as client:
            identity = self.h.start(client)
            original = self.h.complete(client, identity)
            service = client.app.state.service
            original_report = service.store.report(identity)
            before_messages = len(service.store.messages('conv_session'))
            entered, release = threading.Event(), threading.Event()
            def invoke(_):
                entered.set()
                if not release.wait(5):
                    raise AssertionError('Test release missing')
                return SimpleNamespace(content='one isolated explanation')
            model = Mock(invoke=Mock(side_effect=invoke))
            body = self.new('explain-one', mission_id=identity, intent='explain', content='why this report?')
            with patch('utils.helpers.create_llm', return_value=model):
                try:
                    first = client.post('/api/messages', json=body).json()
                    self.assertTrue(entered.wait(5))
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        duplicates = list(pool.map(lambda _: client.post('/api/messages', json=body).json(), range(4)))
                    self.assertTrue(all(r['message_id'] == first['message_id'] and r['replayed'] for r in duplicates))
                    self.assertEqual(client.post('/api/messages', json={**body, 'content': 'other'}).status_code, 409)
                finally:
                    release.set()
                self.h.wait(client, identity, 'COMPLETED')
                self.assertTrue(client.post('/api/messages', json=body).json()['replayed'])
            self.assertEqual(model.invoke.call_count, 1)
            self.assertEqual(len(service.store.messages('conv_session')), before_messages + 2)
            self.assertEqual(service.store.report(identity), original_report)
            self.assertEqual(service.store.get(identity).input_reference.model_dump(), original['input_reference'])
            operations = client.get(f'/api/missions/{identity}/operations').json()
            self.assertEqual([o['kind'] for o in operations], ['workflow', 'explanation'])
            self.assertNotEqual(first['operation_id'], original['workflow_operation_id'])

    def test_explanation_accepted_before_restart_stays_failed_without_harming_report(self):
        with TestClient(self.h.app()) as client:
            identity = self.h.start(client)
            self.h.complete(client, identity)
            report = client.get(f'/api/missions/{identity}/report').json()
            body = self.new('lost-explain', mission_id=identity, intent='explain')
            with patch.object(client.app.state.service, '_enqueue'):
                accepted = client.post('/api/messages', json=body).json()
        with TestClient(self.h.app()) as client, patch('utils.helpers.create_llm', side_effect=AssertionError('No model on replay')):
            replay = client.post('/api/messages', json=body).json()
            self.assertEqual(replay['operation_id'], accepted['operation_id'])
            self.assertTrue(replay['replayed'])
            self.assertEqual(client.get(f'/api/missions/{identity}').json()['status'], 'COMPLETED')
            self.assertEqual(client.get(f'/api/missions/{identity}/report').json(), report)
            self.assertEqual(client.get(f'/api/missions/{identity}/operations').json()[-1]['error'], 'INTERRUPTED')

    def test_duplicate_approval_returns_same_receipt_and_document_once(self):
        with TestClient(self.h.app()) as client:
            identity = self.h.start(client)
            self.h.wait(client, identity, 'BLOCKED')
            url = f'/api/missions/{identity}/input'
            body = input_body(client, url, {'values': {'approved': True}})
            with ThreadPoolExecutor(max_workers=6) as pool:
                responses = list(pool.map(lambda _: client.post(url, json=body), range(6)))
            self.assertTrue(all(r.status_code == 202 for r in responses))
            self.assertEqual(len({r.json()['message_id'] for r in responses}), 1)
            self.h.wait(client, identity, 'COMPLETED')
            self.assertEqual(sum(node == 'document' for node, _ in self.h.calls), 1)
            self.assertEqual(sum(m.kind == 'resume' for m in client.app.state.service.store.messages('conv_session')), 1)
            self.assertTrue(client.post(url, json=body).json()['replayed'])
            self.assertEqual(client.post(url, json={**body, 'values': {'approved': False}}).status_code, 409)
            operation = client.get(f'/api/missions/{identity}/operations').json()[0]
            self.assertEqual((operation['run_id'], operation['status']), (2, 'COMPLETED'))

    def test_later_pause_with_identical_fields_rejects_old_form(self):
        self.h.require_input, self.h.required_input_rounds = True, 2
        with patch('loop_contracts.MAX_REVISIONS_PER_SUBTASK', 2), TestClient(self.h.app()) as client:
            identity = self.h.start(client)
            first = self.h.wait(client, identity, 'BLOCKED')
            url = f'/api/missions/{identity}/input'
            body = input_body(client, url, {'values': {'information': 'first answer'}})
            self.assertEqual(client.post(url, json=body).status_code, 202)
            second = self.h.wait(client, identity, 'BLOCKED')
            self.assertEqual(second['blocked']['reason'], 'missing_user_input')
            self.assertNotEqual(first['blocked']['pause_id'], second['blocked']['pause_id'])
            self.assertEqual(first['blocked']['required_inputs'], second['blocked']['required_inputs'])
            before = self.counts(client.app.state.service)
            self.assertEqual(client.post(url, json={**body, 'request_id': 'stale-form'}).status_code, 409)
            self.assertTrue(client.post(url, json=body).json()['replayed'])
            self.assertEqual(self.counts(client.app.state.service), before)
            current = input_body(client, url, {'values': {'information': 'second answer'}})
            self.assertEqual(client.post(url, json=current).status_code, 202)
            self.assertEqual(self.h.wait(client, identity, 'BLOCKED')['blocked']['reason'], 'report_approval')

    def test_acceptance_transaction_rolls_back_receipt_message_event_and_operation(self):
        with TestClient(self.h.app()) as client:
            identity = self.h.start(client)
            self.h.wait(client, identity, 'BLOCKED')
            service = client.app.state.service
            url = f'/api/missions/{identity}/input'
            body = input_body(client, url, {'values': {'approved': True}})
            before, snapshot = self.counts(service), service.store.get(identity)
            with service.store.db:
                service.store.db.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON request_receipts BEGIN SELECT RAISE(ABORT,'injected receipt failure'); END")
            with patch.object(service, '_enqueue') as enqueue:
                with self.assertRaises(sqlite3.IntegrityError):
                    client.post(url, json=body)
                with self.assertRaises(sqlite3.IntegrityError):
                    client.post('/api/messages', json=self.new('rollback-new'))
                enqueue.assert_not_called()
            self.assertEqual(self.counts(service), before)
            self.assertEqual(service.store.get(identity), snapshot)

    def test_old_write_client_is_rejected_instead_of_receiving_a_generated_identity(self):
        with TestClient(self.h.app()) as client:
            before = self.counts(client.app.state.service)
            self.assertEqual(client.post('/api/messages', json={'conversation_id': 'c', 'content': 'missing identity'}).status_code, 422)
            self.assertEqual(self.counts(client.app.state.service), before)

    def test_legacy_pause_adoption_is_explicit_and_does_not_rewrite_fixed_input(self):
        with TestClient(self.h.app()) as client:
            identity = self.h.start(client)
            self.h.wait(client, identity, 'BLOCKED')
            service = client.app.state.service
            mission = service.store.get(identity)
            mission.workflow_operation_id, mission.blocked.pause_id = None, None
            service.store.save(mission)
            inputs = service.store.inputs(identity)
            cp = service.checkpoint_path.read_bytes()
        path = self.h.root / 'tasks/workspace.sqlite3'
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript('DROP TABLE operations; DROP TABLE request_receipts; DELETE FROM creation_requests; PRAGMA user_version=0;')
        with TestClient(self.h.app()) as client:
            service = client.app.state.service
            before_events = service.store.events(identity, 0)
            legacy = client.get(f'/api/missions/{identity}').json()
            self.assertTrue(legacy['blocked']['pause_id'].startswith('legacy_'))
            self.assertIsNone(service.store.get(identity).blocked.pause_id)
            self.assertEqual(service.store.events(identity, 0), before_events)
            with patch.object(service, '_enqueue'):
                response = client.post(f'/api/missions/{identity}/input', json={
                    'request_id': 'adopt-legacy', 'pause_id': legacy['blocked']['pause_id'], 'values': {'approved': True}})
            self.assertEqual(response.status_code, 202, response.text)
            self.assertTrue(client.get(f'/api/missions/{identity}/operations').json()[0]['legacy_history'])
            self.assertEqual(service.store.inputs(identity), inputs)
            self.assertEqual(service.checkpoint_path.read_bytes(), cp)

    def test_future_workspace_version_is_refused_without_rewriting_database(self):
        path = self.h.root / 'future.sqlite3'
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('PRAGMA user_version=99')
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, '版本'):
            Store(path)
        self.assertEqual(path.read_bytes(), before)


class CLIRequestIdentityTests(unittest.TestCase):
    setUp = cli_fixtures.CLIPersistenceTests.setUp
    tearDown = cli_fixtures.CLIPersistenceTests.tearDown
    process = cli_fixtures.CLIPersistenceTests.process
    calls = cli_fixtures.CLIPersistenceTests.calls
    start = cli_fixtures.CLIPersistenceTests.start

    def test_new_request_replays_across_processes_without_config_latest_or_runtime(self):
        args = ['same CLI request', '--request-id', 'cli-new-one']
        self.process(args, 'q\n')
        before = self.calls()
        original = self.repository.list()[0]
        output = self.process(args, no_latest=True, read_only=True)
        self.assertIn('[已有请求回执]', output)
        self.assertEqual(len(self.repository.list()), 1)
        self.assertEqual(self.repository.list()[0]['thread_id'], original['thread_id'])
        self.assertEqual(self.calls(), before)

    def test_input_receipt_replay_does_not_approve_the_next_pause(self):
        parent = self.start(require_input=True)
        args = ['--continue', parent.id, '--request-id', 'cli-input-one']
        self.process(args, 'first answer\nq\n', require_input=True, no_latest=True)
        before = self.calls()
        paused = self.repository.mission(parent.id)
        self.assertEqual(paused.blocked.reason, 'report_approval')
        output = self.process(args, no_latest=True, read_only=True)
        self.assertIn('[已有请求回执]', output)
        self.assertEqual(self.repository.mission(parent.id), paused)
        self.assertEqual(self.calls(), before)

    def test_explanation_replay_retains_original_report_and_runs_model_once(self):
        parent = self.start()
        self.process(['--continue', parent.id], '\n', no_latest=True)
        report = self.repository.report(self.repository.mission(parent.id))
        args = ['--explain', parent.id, 'why original?', '--request-id', 'cli-explain-one']
        with patch.dict(os.environ, {'FAIT_TEST_EXPLANATION': '1'}):
            self.assertIn('这是隔离的原报告解释', self.process(args, no_latest=True))
            before = self.calls()
            self.assertIn('这是隔离的原报告解释', self.process(args, no_latest=True, read_only=True))
        self.assertEqual(self.calls(), before)
        self.assertEqual(sum(call['node'] == 'explanation' for call in before), 1)
        self.assertEqual(self.repository.report(self.repository.mission(parent.id)), report)

    def test_failed_explanation_returns_failure_code_and_replay_does_not_call_model(self):
        parent = self.start()
        self.process(['--continue', parent.id], '\n', no_latest=True)
        report = self.repository.report(self.repository.mission(parent.id))
        args = ['--explain', parent.id, 'why?', '--request-id', 'failed-explanation']
        with patch.dict(os.environ, {'FAIT_TEST_EXPLANATION': '1', 'FAIT_TEST_EXPLANATION_FAILURE': '1'}):
            self.assertIn('解释操作未完成', self.process(args, no_latest=True, expected=1))
            calls = self.calls()
            self.assertIn('解释操作未完成', self.process(args, no_latest=True, read_only=True, expected=1))
        self.assertEqual(self.calls(), calls)
        self.assertEqual(self.repository.mission(parent.id).status, 'COMPLETED')
        self.assertEqual(self.repository.report(self.repository.mission(parent.id)), report)


if __name__ == '__main__':
    unittest.main()
