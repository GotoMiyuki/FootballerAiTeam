"""M4.5: actual SDK attempts, unknown usage, operation accounting and HTTP/SSE."""
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.call_limits import execution_scope, invoke_model
from backend.main import create_app
from backend.models import OperationView
from backend.operation_metrics import OperationMeter, record, meter_summary, runtime_view
from backend.process_runner import ProcessRunner
from backend.process_supervisor import OwnedProcess
from backend.runtime_contracts import RuntimePolicy, ExecutionIdentity
from evaluation import test_session_semantics as semantics
from evaluation.m4_audit import dump
from evaluation.player_fixtures import write_fixture
from evaluation.runtime_fixture import InlineRunner
from player_data.repository import FixtureRepository

REPO = Path(__file__).resolve().parents[1]


class MeteringUnitTests(unittest.TestCase):
    def test_unknown_partial_zero_and_legacy_graph_usage_are_distinct(self):
        from utils.telemetry import token_usage_from_response, make_node_telemetry_delta, merge_telemetry
        self.assertEqual(token_usage_from_response(SimpleNamespace()), {'input_tokens': None, 'output_tokens': None})
        self.assertEqual(token_usage_from_response(SimpleNamespace(usage_metadata={'input_tokens': 0, 'output_tokens': 0})), {'input_tokens': 0, 'output_tokens': 0})
        partial = token_usage_from_response(SimpleNamespace(usage_metadata={'input_tokens': 8}))
        self.assertEqual(partial, {'input_tokens': 8, 'output_tokens': None})
        state = {}
        for node, event in [('manager_revision', partial), ('manager_replan', {'input_tokens': None, 'output_tokens': None})]:
            delta = make_node_telemetry_delta(node_name=node, role='manager', latency_ms=10, llm_events=[event])
            state = merge_telemetry(state, delta)
        self.assertIsNone(state['input_tokens'])
        self.assertEqual((state['known_input_tokens'], state['usage_unknown_calls'], state['revision_count'], state['replan_count']), (8, 2, 1, 1))
        legacy = merge_telemetry({'llm_call_count': 1, 'input_tokens': 0, 'output_tokens': 0}, {})
        self.assertIsNone(legacy['input_tokens'])
        self.assertEqual(legacy['usage_unknown_calls'], 1)

    def test_sdk_retry_success_failure_400_missing_and_explicit_zero(self):
        import httpx
        from config import config
        from utils.helpers import create_llm
        policy = RuntimePolicy(model_connect_seconds=.2, model_read_seconds=.4, model_call_seconds=4, execution_seconds=10, tool_seconds=1)
        results = []
        for mode, statuses, expected in [('retry_success', [500, 200], 2), ('retry_failure', [500, 500], 2),
                ('400', [400], 1), ('missing', [200], 1), ('zero', [200], 1)]:
            meter = OperationMeter()
            op = OperationView(id='op', mission_id='mission', kind='explanation', created_at='2026-10-26T00:00:00+00:00', metering=meter)
            requests, events = [], []
            def emit(event):
                events.append(event)
                record(meter, 1, event)
            def transport(request):
                requests.append(request.extensions['timeout'])
                status = statuses[min(len(requests) - 1, len(statuses) - 1)]
                if status != 200:
                    return httpx.Response(status, json={'error': {'message': 'secret-response-body', 'type': 'offline'}})
                body = {'id': 'offline', 'object': 'chat.completion', 'created': 0, 'model': 'offline',
                    'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'secret-response-content'}, 'finish_reason': 'stop'}]}
                if mode != 'missing':
                    body['usage'] = {'prompt_tokens': 0 if mode == 'zero' else 11, 'completion_tokens': 0 if mode == 'zero' else 7, 'total_tokens': 0 if mode == 'zero' else 18}
                return httpx.Response(200, json=body)
            with httpx.Client(transport=httpx.MockTransport(transport)) as client, \
                    patch.object(config, 'OPENAI_API_KEY', 'secret-offline-key'), patch.object(config, 'OPENAI_BASE_URL', 'http://offline.invalid/v1'), \
                    execution_scope(policy, time.monotonic() + 10, emit=emit):
                model = create_llm(http_client=client)
                if statuses[-1] != 200:
                    with self.assertRaises(Exception):
                        invoke_model(model, ['secret-prompt'], agent='manager', purpose='manager_revision')
                else:
                    invoke_model(model, ['secret-prompt'], agent='manager', purpose='manager_revision')
            record(meter, 1, {'type': 'worker_finished'})
            summary = meter_summary(op)
            self.assertEqual((summary['logical_calls'], summary['network_attempts']), (1, expected))
            self.assertEqual(len(requests), expected)
            self.assertNotIn('secret-', json.dumps(events))
            self.assertNotIn('offline.invalid', json.dumps(events))
            if mode == 'zero':
                self.assertEqual((summary['complete'], summary['input_tokens'], summary['output_tokens']), (True, 0, 0))
            else:
                self.assertIsNone(summary['input_tokens'])
            if mode == 'retry_success':
                self.assertEqual((summary['known_input_tokens'], summary['known_output_tokens'], summary['unknown_attempts']), (11, 7, 1))
            if mode in {'retry_failure', '400'}:
                self.assertEqual((summary['failed_calls'], summary['usage_unknown_calls']), (1, 1))
            if mode == 'missing':
                self.assertEqual(summary['usage_unknown_calls'], 1)
            # Replaying metadata must not change either count or revision-worthy content.
            before = meter.model_dump()
            for event in events:
                record(meter, 1, event)
            self.assertEqual(meter.model_dump(), before)
            results.append({'mode': mode, 'summary': summary, 'events': events})
        root = Path(os.environ['FAIT_M45_TEST_ROOT']) / self._testMethodName
        root.mkdir(parents=True, exist_ok=False)
        dump(root / 'evidence.json', results)


class MeteringServiceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.environ['FAIT_M45_TEST_ROOT']) / self._testMethodName
        self.root.mkdir(parents=True, exist_ok=False)
        self.h = semantics.SessionSemanticsTests()
        self.h.root, self.h.fixture = self.root, self.root / 'player'
        write_fixture(self.h.fixture, speed=44)
        self.h.repository = FixtureRepository(self.h.fixture)
        self.h.calls, self.h.require_input = [], False
        self.runtime = patch('backend.runtime.create_runtime', side_effect=self.h.runtime)
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.policy = RuntimePolicy()
        self.records = []
        self.addCleanup(lambda: dump(self.root / 'evidence.json', self.records))

    @contextmanager
    def client(self):
        with TestClient(create_app(data_dir=self.root / 'tasks', demo=False, player_repository=self.h.repository,
                runner=InlineRunner(), runtime_policy=self.policy)) as client:
            yield client

    def explain(self, client, identity, mode, request_id):
        response = client.post('/api/messages', json={'request_id': request_id, 'conversation_id': 'conv_session',
            'mission_id': identity, 'intent': 'explain', 'content': 'm45:' + mode})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def wait(self, predicate, seconds=60):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            result = predicate()
            if result:
                return result
            time.sleep(.01)
        self.fail('Expected runtime boundary not reached')

    def test_two_explanations_are_independent_and_do_not_rewrite_report_generation(self):
        with self.client() as client:
            identity = self.h.start(client)
            original = self.h.complete(client, identity)
            service = client.app.state.service
            workflow_id = original['workflow_operation_id']
            original_workflow = service.store.operation(workflow_id).model_dump()
            report = service.store.report(identity)
            # A real worker/SDK/IPC path for each explanation; no external provider.
            service.runner = ProcessRunner('evaluation.m45_worker')
            accepted = []
            for mode in ['retry_success', 'missing']:
                item = self.explain(client, identity, mode, mode)
                accepted.append(item)
                self.wait(lambda: identity not in service.active)
            view = client.get(f'/api/missions/{identity}/runtime').json()
            self.assertEqual(service.store.operation(workflow_id).model_dump(), original_workflow)
            self.assertEqual(service.store.report(identity), report)
            self.assertEqual(service.store.get(identity).telemetry, original['telemetry'])
            self.assertEqual(len(view['operations']), 3)
            self.assertEqual((view['totals']['logical_calls'], view['totals']['network_attempts']), (2, 3))
            self.assertEqual((view['totals']['known_input_tokens'], view['totals']['known_output_tokens']), (11, 7))
            self.assertIsNone(view['totals']['input_tokens'])
            revisions = {op.id: op.revision for op in service.store.operations(identity)}
            for _ in range(3):
                client.get(f'/api/missions/{identity}/runtime')
                client.get(f'/api/missions/{identity}/operations')
                service.store.events(identity, 0)
            self.assertEqual(revisions, {op.id: op.revision for op in service.store.operations(identity)})
            replay = self.explain(client, identity, 'missing', 'missing')
            self.assertTrue(replay['replayed'])
            self.assertEqual(client.get(f'/api/missions/{identity}/runtime').json()['totals'], view['totals'])
            self.records.append(view)

    def test_pause_wait_is_separate_persistent_and_request_replay_does_not_add_it_again(self):
        self.h.require_input = True
        with self.client() as client:
            identity = self.h.start(client)
            paused = self.h.wait(client, identity, 'BLOCKED')
            op_id = paused['workflow_operation_id']
            execution = client.app.state.service.store.operation(op_id).execution_seconds
            time.sleep(.08)
            timing = client.get(f'/api/missions/{identity}/runtime').json()['operations'][0]['timing']
            self.assertGreaterEqual(timing['user_wait_seconds'], .05)
            self.assertEqual(timing['execution_seconds'], execution)
        time.sleep(.05)
        with self.client() as client:
            request = {'request_id': 'resume_pause', 'pause_id': paused['blocked']['pause_id'], 'values': {'information': '恢复正常'}}
            self.assertEqual(client.post(f'/api/missions/{identity}/input', json=request).status_code, 202)
            self.h.wait(client, identity, 'BLOCKED')
            op = client.app.state.service.store.operation(op_id)
            wait = op.user_wait_seconds
            self.assertGreaterEqual(wait, .1)
            self.assertTrue(client.post(f'/api/missions/{identity}/input', json=request).json()['replayed'])
            self.assertEqual(client.app.state.service.store.operation(op_id).user_wait_seconds, wait)
            self.h.complete(client, identity)
            final = client.get(f'/api/missions/{identity}/runtime').json()
            self.assertEqual(final['operations'][0]['run_id'], 3)
            self.assertEqual(final['operations'][0]['metering']['finished_runs'], [1, 2, 3])
            self.assertGreaterEqual(final['operations'][0]['timing']['wall_seconds'], wait)
            self.records.append(final)

    def test_timeout_and_cancel_keep_inflight_usage_unknown(self):
        from evaluation.test_m43_runtime import process_handle
        with self.client() as client:
            identity = self.h.start(client)
            self.h.complete(client, identity)
            service = client.app.state.service
            service.runner = ProcessRunner('evaluation.m45_worker')
            for reason in ['timeout', 'cancel']:
                service.policy = replace(self.policy, model_connect_seconds=.1, model_read_seconds=.2,
                    model_call_seconds=.6 if reason == 'timeout' else 10)
                accepted = self.explain(client, identity, 'hang', reason)
                marker = self.root / 'tasks/runs' / accepted['operation_id'] / '1/request-started'
                self.wait(lambda: marker.exists())
                if reason == 'cancel':
                    response = client.post(f"/api/missions/{identity}/operations/{accepted['operation_id']}/cancel", json={
                        'request_id': 'cancel_hang', 'expected_run_id': 1})
                    self.assertTrue(response.json()['accepted'])
                self.wait(lambda: identity not in service.active)
                op = service.store.operation(accepted['operation_id'])
                self.assertEqual(op.status, 'FAILED' if reason == 'timeout' else 'CANCELLED')
                with process_handle(op.worker_pid) as exited:
                    self.assertTrue(exited(1000))
                summary = meter_summary(op)
                self.assertEqual((summary['logical_calls'], summary['known_network_attempts'], summary['interrupted_calls']), (1, 1, 1))
                self.assertIsNone(summary['input_tokens'])
                self.assertFalse(summary['counts_complete'])
                self.assertEqual(service.store.get(identity).status, 'COMPLETED')
                self.records.append({'reason': reason, 'summary': summary, 'operation': op.model_dump(mode='json')})

    def test_legacy_and_crash_do_not_become_zero_usage(self):
        with self.client() as client:
            identity = self.h.start(client)
            self.h.complete(client, identity)
            service = client.app.state.service
            op = service.store.operation(service.store.get(identity).workflow_operation_id)
            op.metering, op.timing_history_complete = None, False
            service.store.save_operation(op)
            service.store.save_operation(OperationView(id='op_crashed_explanation', mission_id=identity, kind='explanation',
                created_at='2026-10-26T00:00:00+00:00', status='RUNNING', metering=OperationMeter(calls={})))
        with self.client() as client:
            result = client.get(f'/api/missions/{identity}/runtime').json()
            self.assertIsNone(result['operations'][0]['usage']['logical_calls'])
            self.assertIsNone(result['operations'][0]['timing']['execution_seconds'])
            self.assertFalse(result['totals']['available'])
            self.assertIsNone(result['totals']['input_tokens'])
            self.assertFalse(result['operations'][1]['metering']['history_complete'])
            self.assertEqual(result['operations'][1]['error'], 'INTERRUPTED')
            self.records.append(result)

    def test_logs_correlate_identity_without_provider_error_or_prompt(self):
        from unittest.mock import Mock
        with self.client() as client:
            identity = self.h.start(client)
            self.h.complete(client, identity)
            model = Mock(invoke=Mock(side_effect=RuntimeError('secret-provider-body secret-key')))
            with patch('utils.helpers.create_llm', return_value=model), self.assertLogs(level='INFO') as logs:
                accepted = self.explain(client, identity, 'private-prompt', 'log_request')
                self.wait(lambda: identity not in client.app.state.service.active)
            text = '\n'.join(logs.output)
            self.assertIn(identity, text)
            self.assertIn(accepted['operation_id'], text)
            self.assertIn('request=log_request', text)
            self.assertIn('run=1', text)
            self.assertNotIn('secret-', text)
            self.assertNotIn('private-prompt', text)
            self.records.append({'correlation_present': True, 'secrets_and_prompts_absent': True})

    def test_queue_cancel_records_wait_and_known_zero_model_calls(self):
        from evaluation.test_m44_cancellation import DelayedRunner
        delayed = DelayedRunner()
        delayed.arm(before=True)
        with self.client() as client:
            service = client.app.state.service
            service.runner = delayed
            first = self.h.start(client)
            self.assertTrue(delayed.arrived.wait(10))
            try:
                queued = self.h.start(client)
                time.sleep(.05)
                op = service.store.operation(service.store.get(queued).workflow_operation_id)
                receipt = client.post(f'/api/missions/{queued}/operations/{op.id}/cancel', json={
                    'request_id': 'cancel_queued', 'expected_run_id': 1})
                self.assertTrue(receipt.json()['accepted'])
                op = service.store.operation(op.id)
                usage = meter_summary(op)
                self.assertGreaterEqual(op.queue_seconds, .04)
                self.assertEqual((usage['logical_calls'], usage['network_attempts'], usage['input_tokens']), (0, 0, 0))
                self.assertTrue(usage['complete'])
                self.records.append({'operation': op.model_dump(mode='json'), 'usage': usage})
            finally:
                first_op = service.store.get(first).workflow_operation_id
                client.post(f'/api/missions/{first}/operations/{first_op}/cancel', json={'request_id': 'cancel_first', 'expected_run_id': 1})
                delayed.release.set()

    def test_publication_rollback_keeps_already_observed_response_usage(self):
        original_runtime = self.h.runtime
        model = SimpleNamespace(invoke=lambda _messages: SimpleNamespace(content='offline', usage_metadata={'input_tokens': 7, 'output_tokens': 3}))
        def runtime(path, on_start):
            def start(node, state):
                if node == 'document':
                    invoke_model(model, [], agent='document', purpose='document')
                on_start(node, state)
            return original_runtime(path, start)
        self.runtime.stop()
        with patch('backend.runtime.create_runtime', side_effect=runtime), self.client() as client:
            identity = self.h.start(client)
            self.h.wait(client, identity, 'BLOCKED')
            service = client.app.state.service
            original = service.store.save_operation
            injected = []
            def save(op):
                if op.status == 'COMPLETED' and not injected:
                    injected.append(True)
                    raise RuntimeError('report transaction fault')
                return original(op)
            paused = service.store.get(identity)
            with patch.object(service.store, 'save_operation', side_effect=save):
                self.assertEqual(client.post(f'/api/missions/{identity}/input', json={
                    'request_id': 'approve_fault', 'pause_id': paused.blocked.pause_id, 'values': {'approved': True}}).status_code, 202)
                self.h.wait(client, identity, 'FAILED')
            op = service.store.operation(paused.workflow_operation_id)
            self.assertIsNone(service.store.report(identity))
            self.assertEqual((meter_summary(op)['known_input_tokens'], meter_summary(op)['known_output_tokens']), (7, 3))
            self.assertEqual(meter_summary(op)['logical_calls'], 1)
            self.records.append({'report_unpublished': True, 'observed_usage_preserved': meter_summary(op)})


class HttpReplayTests(unittest.TestCase):
    def test_real_http_sse_replay_does_not_charge_calls_again(self):
        import httpx
        root = Path(os.environ['FAIT_M45_TEST_ROOT']) / self._testMethodName
        root.mkdir(parents=True, exist_ok=False)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        with (root / 'owner.log').open('w', encoding='utf-8') as log, OwnedProcess('evaluation.m44_api_fixture', root,
                '--port', port, root=REPO, output=log) as owner:
            end = time.monotonic() + 60
            while True:
                try:
                    if httpx.get(origin + '/api/health', timeout=.5).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > end or owner.process.poll() is not None:
                    self.fail('HTTP owner did not start')
                time.sleep(.02)
            accepted = httpx.post(origin + '/api/messages', json={'request_id': 'sse_meter', 'conversation_id': 'sse', 'content': 'm43:model-hang'}).json()
            path = origin + f"/api/missions/{accepted['mission_id']}"
            while True:
                view = httpx.get(path + '/runtime').json()
                if view['totals']['logical_calls'] == 1:
                    break
                if time.monotonic() > end:
                    self.fail('Model span not observed')
                time.sleep(.02)
            revision = view['operations'][0]['revision']
            replay_ids = []
            for _ in range(2):
                ids = []
                with httpx.stream('GET', path + '/events?after=0', timeout=3) as stream:
                    for line in stream.iter_lines():
                        if line.startswith('data: '):
                            ids.append(json.loads(line[6:])['sequence'])
                            if len(ids) == 2:
                                break
                replay_ids.append(ids)
                current = httpx.get(path + '/runtime').json()
                self.assertEqual(current['operations'][0]['revision'], revision)
                self.assertEqual(current['totals'], view['totals'])
            self.assertEqual(replay_ids[0], replay_ids[1])
            httpx.post(path + f"/operations/{accepted['operation_id']}/cancel", json={'request_id': 'stop', 'expected_run_id': 1})
            dump(root / 'evidence.json', {'origin': origin, 'owner_pid': owner.pid, 'same_replayed_sequences': replay_ids,
                'runtime_before_and_after_replay': view, 'read_only_revision': revision})
