"""Persistent cancel/commit races, real checkpoints, and owned process cleanup."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.call_limits import ExecutionFailure, execution_scope
from backend.main import create_app
from backend.models import CancelRequest
from backend.process_runner import ProcessRunner
from backend.runtime_contracts import ExecutionIdentity, RuntimePolicy
from evaluation import test_session_semantics as semantics
from evaluation.m4_audit import dump
from evaluation.player_fixtures import write_fixture
from evaluation.runtime_fixture import InlineRunner
from player_data.repository import FixtureRepository


class DelayedRunner:
    """Release an already computed result even after cancellation; not cleanup proof."""
    def __init__(self):
        self.hold = None
        self.before = False
        self.arrived = threading.Event()
        self.release = threading.Event()
        self.calls = []
        self.result = None

    def arm(self, kind='workflow', *, before=False):
        self.hold, self.before = kind, before
        self.arrived.clear()
        self.release.clear()

    def delay(self):
        self.arrived.set()
        if not self.release.wait(30):
            raise AssertionError('Test did not release its delayed runner')

    def run(self, payload, policy, deadline, stop, on_event):
        self.calls.append((payload['operation_id'], payload['run_id'], payload['kind']))
        if self.before and self.hold == payload['kind']:
            self.delay()
        if payload['kind'] == 'explanation':
            result = {'type': 'done', 'answer': 'delayed original report explanation'}
        else:
            result = InlineRunner().run(payload, policy, deadline, stop, on_event)
        self.result = result
        if not self.before and self.hold == payload['kind'] and not result.get('next'):
            self.delay()
        return result


class CancellationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.environ['FAIT_M44_TEST_ROOT']) / self._testMethodName
        self.root.mkdir(parents=True, exist_ok=False)
        self.h = semantics.SessionSemanticsTests()
        self.h.root, self.h.fixture = self.root, self.root / 'player'
        write_fixture(self.h.fixture, speed=44)
        self.h.repository = FixtureRepository(self.h.fixture)
        self.h.calls, self.h.require_input = [], False
        self.runner = DelayedRunner()
        self.counter = 0
        self.policy = RuntimePolicy(cleanup_seconds=1, shutdown_seconds=4)
        self.records = []
        self.runtime = patch('backend.runtime.create_runtime', side_effect=self.h.runtime)
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.addCleanup(lambda: dump(self.root / 'evidence.json', self.records))

    @contextmanager
    def client(self, runner=None, *, exceptions=True):
        with TestClient(create_app(data_dir=self.root / 'tasks', demo=False, player_repository=self.h.repository,
                runner=runner or self.runner, runtime_policy=self.policy), raise_server_exceptions=exceptions) as client:
            try:
                yield client
            finally:
                self.runner.release.set()

    def start(self, client, content='M44 original training'):
        self.counter += 1
        response = client.post('/api/messages', json={'request_id': f'create_{self.counter}',
            'conversation_id': 'conv_session', 'content': content})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def operation(self, client, accepted):
        return client.app.state.service.store.operation(accepted['operation_id'])

    def wait(self, predicate, seconds=60):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            value = predicate()
            if value:
                return value
            time.sleep(.01)
        self.fail('Expected boundary not reached')

    def cancel(self, client, accepted, request_id='cancel_one', **overrides):
        op = self.operation(client, accepted)
        return client.post(f"/api/missions/{accepted['mission_id']}/operations/{op.id}/cancel", json={
            'request_id': request_id, 'expected_run_id': op.run_id, 'expected_pause_id': op.pause_id, **overrides})

    def approve(self, client, accepted):
        mission = self.h.wait(client, accepted['mission_id'], 'BLOCKED')
        response = client.post(f"/api/missions/{accepted['mission_id']}/input", json={
            'request_id': f"approve_{self.counter}", 'pause_id': mission['blocked']['pause_id'], 'values': {'approved': True}})
        self.assertEqual(response.status_code, 202, response.text)

    def ec(self, service):
        names = ['recommendations', 'recommendation_batches', 'recommendation_events', 'recommendation_actions',
            'recommendation_action_heads', 'recommendation_action_requests', 'career_memories', 'career_memory_events',
            'career_memory_requests', 'comparison_plans', 'assessments', 'assessment_requests']
        with service.store.lock:
            return {name: service.store.db.execute('SELECT * FROM ' + name + ' ORDER BY rowid').fetchall() for name in names}

    def test_cancel_after_candidate_then_late_result_cannot_publish_or_project(self):
        with self.client() as client:
            accepted = self.start(client)
            self.runner.arm()
            self.approve(client, accepted)
            self.assertTrue(self.runner.arrived.wait(60))
            service = client.app.state.service
            old = service.store.get(accepted['mission_id'])
            identity = ExecutionIdentity(accepted['operation_id'], self.operation(client, accepted).run_id)
            before_ec = self.ec(service)
            receipt = self.cancel(client, accepted)
            self.assertEqual(receipt.status_code, 200, receipt.text)
            self.assertEqual(receipt.json()['status'], 'CANCELLED')
            self.assertEqual(receipt.json()['cleanup_status'], 'PENDING')
            sequence = service.store.get(old.id).sequence
            # Persistence, not a process-local stop flag, rejects stale publication/progress/failure.
            with execution_scope(self.policy, time.monotonic() + 10, identity=identity):
                for action in [lambda: service.complete(old, self.runner.result['state']['final_report'], state=self.runner.result['state']),
                               lambda: service.emitter.started(old, 'document', {}),
                               lambda: service.fail(old, 'late failure')]:
                    with self.assertRaises(ExecutionFailure):
                        action()
            self.assertEqual(service.store.get(old.id).sequence, sequence)
            self.runner.release.set()
            self.wait(lambda: old.id not in service.active)
            final = self.operation(client, accepted)
            self.assertEqual((final.status, final.cleanup_status), ('CANCELLED', 'COMPLETE'))
            self.assertIsNone(service.store.report(old.id))
            self.assertEqual(client.get(f'/api/missions/{old.id}/report').status_code, 404)
            self.assertEqual(self.ec(service), before_ec)
            self.assertEqual(service.recommendations.list(old.id).items, [])
            # The unpublished checkpoint candidate cannot re-enter via an explicit history selection.
            missions_before = len(service.store.missions())
            selected = client.post('/api/mission-continuations', json={'request_id': 'reuse_cancelled_candidate',
                'parent_mission_id': old.id, 'conversation_id': old.conversation_id, 'operation': 'retry',
                'content': 'new task', 'reason': 'explicit retry', 'include_report': False,
                'selected_results': [{'subtask_id': 'training', 'version': 1}]})
            self.assertEqual(selected.status_code, 409, selected.text)
            self.assertIn('内部专业成果不能引用', selected.json()['detail'])
            self.assertEqual(len(service.store.missions()), missions_before)
            self.assertFalse(any(e['type'] in {'result.created', 'report.created', 'mission.completed'} for e in service.store.events(old.id, 0)))
            self.records.append({'receipt': receipt.json(), 'final': final.model_dump(mode='json'), 'late_effects': 0, 'candidate_in_checkpoint_only': True})

    def test_queued_cancel_frees_capacity_and_never_invokes_worker(self):
        self.policy = replace(self.policy, queue_capacity=1)
        self.runner.arm(before=True)
        with self.client() as client:
            first = self.start(client)
            self.assertTrue(self.runner.arrived.wait(10))
            queued = self.start(client)
            receipt = self.cancel(client, queued)
            self.assertTrue(receipt.json()['accepted'])
            self.wait(lambda: self.operation(client, queued).cleanup_status == 'COMPLETE')
            third = self.start(client)
            self.assertTrue(self.cancel(client, third, 'cancel_third').json()['accepted'])
            self.assertEqual(self.runner.calls, [(first['operation_id'], 1, 'workflow')])
            self.cancel(client, first, 'cancel_first')
            self.runner.release.set()
            service = client.app.state.service
            self.wait(lambda: not service.active)
            self.assertTrue(all(op.status == 'CANCELLED' for op in service.store.operations()))
            self.records.append({'worker_calls': self.runner.calls, 'cancelled_queued': [queued, third], 'capacity_reused': True})

    def test_both_pauses_cancel_without_runtime_or_latest_read_and_survive_restart(self):
        for require_input in [True, False]:
            self.h.require_input = require_input
            with self.client() as client:
                accepted = self.start(client)
                view = self.h.wait(client, accepted['mission_id'], 'BLOCKED')
                before_calls = list(self.h.calls)
                with patch('backend.runtime.create_runtime', side_effect=AssertionError('No runtime on cancel')), \
                        patch.object(self.h.repository, 'read_snapshot', side_effect=AssertionError('No latest read')):
                    receipt = self.cancel(client, accepted, 'cancel_' + str(require_input))
                self.assertEqual(receipt.status_code, 200, receipt.text)
                self.assertEqual(client.get(f"/api/missions/{accepted['mission_id']}").json()['status'], 'CANCELLED')
                self.assertEqual(self.h.calls, before_calls)
                self.records.append({'pause': view['blocked']['reason'], 'receipt': receipt.json(), 'new_calls': 0})
            with self.client() as restarted:
                self.assertEqual(restarted.get(f"/api/missions/{accepted['mission_id']}").json()['status'], 'CANCELLED')
                self.assertEqual(self.operation(restarted, accepted).status, 'CANCELLED')
                self.assertEqual(restarted.post(f"/api/missions/{accepted['mission_id']}/input", json={
                    'request_id': 'late_input_' + str(require_input), 'pause_id': view['blocked']['pause_id'],
                    'values': {'approved': True}}).status_code, 422)

    def test_repeated_cancel_and_stale_generation_do_not_target_a_new_run(self):
        self.h.require_input = True
        with self.client() as client:
            accepted = self.start(client)
            first = self.h.wait(client, accepted['mission_id'], 'BLOCKED')
            old_operation = self.operation(client, accepted)
            supplied = client.post(f"/api/missions/{accepted['mission_id']}/input", json={
                'request_id': 'resume', 'pause_id': first['blocked']['pause_id'], 'values': {'information': 'recovered'}})
            self.assertEqual(supplied.status_code, 202, supplied.text)
            second = self.h.wait(client, accepted['mission_id'], 'BLOCKED')
            stale = self.cancel(client, accepted, expected_run_id=old_operation.run_id, expected_pause_id=old_operation.pause_id)
            self.assertEqual(stale.status_code, 409)
            with execution_scope(self.policy, time.monotonic() + 10, identity=ExecutionIdentity(old_operation.id, old_operation.run_id)):
                with self.assertRaises(ExecutionFailure):
                    client.app.state.service.emitter.emit(client.app.state.service.store.get(accepted['mission_id']), 'agent.started', agent='Document')
            receipt = self.cancel(client, accepted)
            service = client.app.state.service
            before = (len(service.store.events(accepted['mission_id'], 0)), len(service.store.messages('conv_session')))
            replay = self.cancel(client, accepted)
            self.assertTrue(replay.json()['replayed'])
            self.assertEqual(replay.json(), receipt.json() | {'replayed': True})
            self.assertEqual(before, (len(service.store.events(accepted['mission_id'], 0)), len(service.store.messages('conv_session'))))
            conflict = self.cancel(client, accepted, expected_run_id=999)
            self.assertEqual(conflict.status_code, 409)
            new_cancel = self.cancel(client, accepted, 'another_cancel')
            self.assertEqual((new_cancel.json()['accepted'], new_cancel.json()['outcome']), (False, 'ALREADY_TERMINAL'))
            self.records.append({'old_pause': first['blocked']['pause_id'], 'new_pause': second['blocked']['pause_id'], 'replay': replay.json()})

    def test_completed_first_cancel_is_terminal_receipt_without_withdrawing_delivery(self):
        with self.client() as client:
            accepted = self.start(client)
            self.h.complete(client, accepted['mission_id'])
            service = client.app.state.service
            report, ec = service.store.report(accepted['mission_id']), self.ec(service)
            before_messages = service.store.messages('conv_session')
            receipt = self.cancel(client, accepted)
            self.assertEqual((receipt.json()['accepted'], receipt.json()['outcome'], receipt.json()['status']), (False, 'ALREADY_TERMINAL', 'COMPLETED'))
            self.assertEqual(service.store.report(accepted['mission_id']), report)
            self.assertEqual(service.store.messages('conv_session'), before_messages)
            self.assertEqual(self.ec(service), ec)
            self.records.append(receipt.json())

    def test_explanation_cancel_keeps_original_delivery_and_its_ec_records(self):
        with self.client() as client:
            accepted = self.start(client)
            old = self.h.complete(client, accepted['mission_id'])
            service = client.app.state.service
            report, ec = service.store.report(accepted['mission_id']), self.ec(service)
            self.runner.arm('explanation')
            response = client.post('/api/messages', json={'request_id': 'explain', 'conversation_id': 'conv_session',
                'mission_id': accepted['mission_id'], 'intent': 'explain', 'content': 'explain original'})
            explanation = response.json()
            self.assertTrue(self.runner.arrived.wait(10))
            receipt = self.cancel(client, explanation)
            self.assertEqual(receipt.json()['status'], 'CANCELLED')
            self.runner.release.set()
            self.wait(lambda: accepted['mission_id'] not in service.active)
            current = service.store.get(accepted['mission_id']).model_dump(mode='json')
            self.assertEqual(current, old | {'sequence': current['sequence']})
            self.assertEqual(service.store.report(accepted['mission_id']), report)
            self.assertEqual(self.ec(service), ec)
            self.assertEqual(self.operation(client, accepted).status, 'COMPLETED')
            self.assertEqual(self.operation(client, explanation).status, 'CANCELLED')
            self.assertFalse(any(m.role == 'assistant' and m.kind == 'explanation' for m in service.store.messages('conv_session')))
            self.records.append({'original_workflow': self.operation(client, accepted).model_dump(mode='json'), 'explanation': self.operation(client, explanation).model_dump(mode='json')})

    def test_publication_failure_rolls_back_report_message_events_and_operation_together(self):
        with self.client() as client:
            accepted = self.start(client)
            self.h.wait(client, accepted['mission_id'], 'BLOCKED')
            service = client.app.state.service
            original = service.store.save_operation
            injected = []
            def save(operation):
                if operation.status == 'COMPLETED' and not injected:
                    injected.append(True)
                    raise RuntimeError('publication transaction fault')
                return original(operation)
            with patch.object(service.store, 'save_operation', side_effect=save):
                self.approve(client, accepted)
                self.h.wait(client, accepted['mission_id'], 'FAILED')
            self.assertTrue(injected)
            self.assertIsNone(service.store.report(accepted['mission_id']))
            self.assertFalse(any(m.role == 'assistant' for m in service.store.messages('conv_session')))
            self.assertFalse(any(e['type'] in {'report.created', 'result.created', 'mission.completed'} for e in service.store.events(accepted['mission_id'], 0)))
            self.assertEqual(self.operation(client, accepted).status, 'FAILED')
            self.assertEqual(service.recommendations.list(accepted['mission_id']).items, [])
            self.records.append({'rolled_back': ['report', 'assistant', 'result/report/completed events', 'COMPLETED operation'], 'checkpoint_candidate_unpublished': True})

    def test_explanation_commit_failure_never_leaves_an_answer_with_failed_operation(self):
        with self.client() as client:
            accepted = self.start(client)
            self.h.complete(client, accepted['mission_id'])
            service = client.app.state.service
            old_report, ec = service.store.report(accepted['mission_id']), self.ec(service)
            original = service.store.save_operation
            injected = []
            def save(operation):
                if operation.kind == 'explanation' and operation.status == 'COMPLETED' and not injected:
                    injected.append(True)
                    raise RuntimeError('explanation final write fault')
                return original(operation)
            with patch.object(service.store, 'save_operation', side_effect=save):
                response = client.post('/api/messages', json={'request_id': 'explain_failure', 'conversation_id': 'conv_session',
                    'mission_id': accepted['mission_id'], 'intent': 'explain', 'content': 'explain'})
                self.assertEqual(response.status_code, 202)
                explanation = response.json()
                self.wait(lambda: accepted['mission_id'] not in service.active)
            self.assertEqual(self.operation(client, explanation).status, 'FAILED')
            self.assertFalse(any(m.role == 'assistant' and m.kind == 'explanation' for m in service.store.messages('conv_session')))
            self.assertEqual(service.store.report(accepted['mission_id']), old_report)
            self.assertEqual(self.ec(service), ec)
            self.records.append({'explanation_answer_rolled_back': True, 'report_and_ec_preserved': True})

    def test_target_validation_cross_entry_identity_and_cancelled_parent_retry(self):
        with self.client() as client:
            accepted = self.start(client)
            self.h.wait(client, accepted['mission_id'], 'BLOCKED')
            operation = self.operation(client, accepted)
            path = f"/api/missions/{accepted['mission_id']}/operations/{operation.id}/cancel"
            for body in [{'request_id': 'bad'}, {'request_id': 'bad', 'expected_run_id': True},
                         {'request_id': 'bad', 'expected_run_id': 0}]:
                self.assertEqual(client.post(path, json=body).status_code, 422)
            self.assertEqual(self.cancel(client, accepted, expected_pause_id=None).status_code, 409)
            self.assertEqual(client.post(f'/api/missions/unknown/operations/{operation.id}/cancel', json={
                'request_id': 'unknown', 'expected_run_id': operation.run_id, 'expected_pause_id': operation.pause_id}).status_code, 404)
            receipt = self.cancel(client, accepted)
            self.assertTrue(receipt.json()['accepted'])
            self.assertEqual(client.post('/api/messages', json={'request_id': 'cancel_one',
                'conversation_id': 'conv_session', 'content': 'another intent'}).status_code, 409)
            retry = client.post('/api/mission-continuations', json={'request_id': 'retry_cancelled',
                'parent_mission_id': accepted['mission_id'], 'conversation_id': 'conv_session', 'operation': 'retry',
                'content': '明确创建新的训练任务', 'reason': '用户决定重新开始', 'include_report': False})
            self.assertEqual(retry.status_code, 202, retry.text)
            child = self.h.wait(client, retry.json()['mission_id'], 'BLOCKED')
            self.assertEqual(child['lineage']['parent_mission_id'], accepted['mission_id'])
            self.assertNotEqual(child['workflow_operation_id'], accepted['operation_id'])
            self.assertEqual(self.operation(client, accepted).status, 'CANCELLED')
            self.records.append({'cancelled_parent_preserved': True, 'new_child': child['id'], 'invalid_targets_rejected': True})

    def test_cancel_receipt_failure_rolls_back_revocation_and_does_not_signal_runner(self):
        with self.client(exceptions=False) as client:
            accepted = self.start(client)
            self.runner.arm()
            self.approve(client, accepted)
            self.assertTrue(self.runner.arrived.wait(60))
            service = client.app.state.service
            before = len(service.store.events(accepted['mission_id'], 0))
            with patch.object(service.store, 'save_receipt', side_effect=RuntimeError('receipt fault')):
                response = self.cancel(client, accepted)
                self.assertEqual(response.status_code, 500)
            self.assertEqual(self.operation(client, accepted).status, 'RUNNING')
            self.assertEqual(len(service.store.events(accepted['mission_id'], 0)), before)
            self.assertFalse(service.executions[accepted['operation_id']].cancelled.is_set())
            self.runner.release.set()
            self.h.wait(client, accepted['mission_id'], 'COMPLETED')
            self.assertIsNotNone(service.store.report(accepted['mission_id']))
            self.records.append({'cancel_rollback': True, 'runner_not_signalled': True, 'later_delivery': 'COMPLETED'})

    def test_complete_and_cancel_race_has_one_consistent_winner(self):
        with self.client() as client:
            for index in range(6):
                accepted = self.start(client)
                self.runner.arm()
                self.approve(client, accepted)
                self.assertTrue(self.runner.arrived.wait(60))
                barrier = threading.Barrier(2)
                def cancel():
                    barrier.wait(5)
                    return self.cancel(client, accepted, f'race_cancel_{index}')
                def release():
                    barrier.wait(5)
                    self.runner.release.set()
                with ThreadPoolExecutor(max_workers=2) as pool:
                    pending = pool.submit(cancel)
                    pool.submit(release).result(10)
                    receipt = pending.result(10)
                service = client.app.state.service
                self.wait(lambda: accepted['mission_id'] not in service.active)
                status = self.operation(client, accepted).status
                self.assertIn(status, {'CANCELLED', 'COMPLETED'})
                self.assertEqual(service.store.get(accepted['mission_id']).status, status)
                delivered = status == 'COMPLETED'
                self.assertEqual(service.store.report(accepted['mission_id']) is not None, delivered)
                self.assertEqual(receipt.json()['accepted'], not delivered)
                self.assertEqual(any(e['type'] == 'mission.completed' for e in service.store.events(accepted['mission_id'], 0)), delivered)
                self.records.append({'race': index, 'winner': str(status), 'receipt': receipt.json()})
                self.runner.hold = None

    def test_real_owned_worker_is_killed_before_cleanup_complete_and_next_task_runs(self):
        from evaluation.test_m43_runtime import process_handle
        self.runtime.stop()
        with self.client(ProcessRunner('evaluation.m43_worker')) as client:
            accepted = self.start(client, 'm43:model-hang')
            marker = self.root / 'tasks/runs' / accepted['operation_id'] / '1/fault-ready'
            self.wait(lambda: marker.exists())
            pid = self.operation(client, accepted).worker_pid
            with process_handle(pid) as exited:
                self.assertFalse(exited())
                started = time.monotonic()
                receipt = self.cancel(client, accepted)
                self.assertTrue(receipt.json()['accepted'])
                self.wait(lambda: self.operation(client, accepted).cleanup_status == 'COMPLETE')
                self.assertTrue(exited(1000))
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, 3)
            following = self.start(client, 'm43:instant-failure')
            self.wait(lambda: following['mission_id'] not in client.app.state.service.active)
            self.assertEqual(self.operation(client, following).status, 'FAILED')
            self.assertEqual(self.operation(client, accepted).status, 'CANCELLED')
            self.records.append({'owned_pid': pid, 'cancel_to_cleanup_seconds': elapsed, 'next_operation': self.operation(client, following).model_dump(mode='json')})
