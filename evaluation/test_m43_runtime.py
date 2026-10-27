"""Real HTTP, owned workers and local HTTP transport. Never calls a live model."""
import contextlib
import ctypes
from ctypes import wintypes
from dataclasses import replace
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.process_runner import ProcessRunner
from backend.process_supervisor import OwnedProcess
from backend.runtime_contracts import RuntimePolicy
from evaluation.player_fixtures import write_fixture
from player_data.repository import FixtureRepository

REPO = Path(__file__).resolve().parents[1]


@contextlib.contextmanager
def process_handle(pid):
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x00100000, False, pid)
    if not handle:
        yield lambda _milliseconds=0: True  # already exited before a handle could be opened
        return
    try:
        yield lambda milliseconds=0: api.WaitForSingleObject(handle, milliseconds) == 0
    finally:
        api.CloseHandle(handle)


class BoundedRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.environ['FAIT_M43_TEST_ROOT']) / self._testMethodName
        self.root.mkdir(parents=True, exist_ok=False)
        self.player = self.root / 'player'
        write_fixture(self.player, speed=44)
        self.repository = FixtureRepository(self.player)
        self.policy = RuntimePolicy(model_connect_seconds=.2, model_read_seconds=.4, model_call_seconds=.6,
            tool_seconds=.6, execution_seconds=90, queue_wait_seconds=10, queue_capacity=1,
            worker_start_seconds=60, cleanup_seconds=1, shutdown_seconds=4)
        self.records = []
        self.addCleanup(lambda: (self.root / 'evidence.json').write_text(json.dumps(self.records, ensure_ascii=False, indent=2), encoding='utf-8'))

    def app(self, policy=None, folder='tasks'):
        return create_app(data_dir=self.root / folder, player_repository=self.repository, demo=False,
            runtime_policy=policy or self.policy, runner=ProcessRunner('evaluation.m43_worker'))

    def submit(self, client, content, request_id=None, **extra):
        return client.post('/api/messages', json={'request_id': request_id or content.replace(':', '_'),
            'conversation_id': 'm43', 'content': content, **extra})

    def wait(self, client, operation_id, predicate=None, seconds=60):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            operation = client.app.state.service.store.operation(operation_id)
            # Publication/pause precedes the coordinator's accounting finalizer.
            # Read a settled execution segment when asserting a frozen budget.
            if (predicate(operation) if predicate else operation.status in {'COMPLETED', 'PAUSED', 'FAILED'}
                    and operation_id not in client.app.state.service.executions):
                return operation
            time.sleep(.01)
        self.fail(f'Operation did not reach expected state: {operation}')

    def counts(self, service):
        with service.store.lock:
            return {name: service.store.db.execute(f'SELECT count(*) FROM {name}').fetchone()[0]
                for name in ['missions', 'mission_inputs', 'messages', 'events', 'operations', 'request_receipts', 'creation_requests']}

    def ready(self, client, accepted):
        marker = self.root / 'tasks/runs' / accepted['operation_id'] / '1/fault-ready'
        return self.wait(client, accepted['operation_id'], lambda op: op.worker_pid is not None and marker.exists())

    def assert_dead(self, pid):
        self.assertIsNotNone(pid)
        with process_handle(pid) as exited:
            self.assertTrue(exited(1500), f'Owned worker {pid} survived')

    def test_model_and_tool_hangs_are_killed_and_second_execution_runs(self):
        with TestClient(self.app()) as client:
            for mode, reason in [('model-hang', 'MODEL_TIMEOUT'), ('tool-hang', 'TOOL_TIMEOUT')]:
                started = time.monotonic()
                accepted = self.submit(client, 'm43:' + mode).json()
                operation = self.wait(client, accepted['operation_id'])
                elapsed = time.monotonic() - started
                self.assertEqual(operation.error, reason)
                self.assert_dead(operation.worker_pid)
                self.assertLess(elapsed, 5)
                self.assertEqual(client.get(f"/api/missions/{accepted['mission_id']}/report").status_code, 404)
                self.assertEqual(client.app.state.service.recommendations.list(accepted['mission_id']).items, [])
                self.records.append({'mode': mode, 'reason': reason, 'elapsed': elapsed, 'pid': operation.worker_pid})
            second = self.submit(client, 'm43:instant-failure').json()
            operation = self.wait(client, second['operation_id'])
            self.assertEqual(operation.error, 'EXECUTION_ERROR')
            self.assert_dead(operation.worker_pid)

    def test_queue_full_has_no_writes_and_replay_bypasses_admission(self):
        policy = replace(self.policy, model_call_seconds=5)
        with TestClient(self.app(policy)) as client:
            first = self.submit(client, 'm43:model-hang', 'first').json()
            self.ready(client, first)
            second = self.submit(client, 'm43:instant-failure', 'second')
            self.assertEqual(second.status_code, 202, second.text)
            before = self.counts(client.app.state.service)
            rejected = self.submit(client, 'm43:instant-failure', 'third')
            self.assertEqual(rejected.status_code, 429, rejected.text)
            self.assertEqual(rejected.json()['code'], 'QUEUE_FULL')
            self.assertFalse(rejected.json()['accepted'])
            self.assertEqual(rejected.headers['Retry-After'], '1')
            self.assertEqual(self.counts(client.app.state.service), before)
            replay = self.submit(client, 'm43:model-hang', 'first')
            self.assertTrue(replay.json()['replayed'])
            self.assertEqual(replay.json()['operation_id'], first['operation_id'])
            self.assertEqual(self.counts(client.app.state.service), before)
            self.records.append({'before': before, 'after': self.counts(client.app.state.service), 'rejection': rejected.json()})

    def test_queue_expiry_does_not_wait_for_the_running_hang(self):
        policy = replace(self.policy, model_call_seconds=5, queue_wait_seconds=.2)
        with TestClient(self.app(policy)) as client:
            first = self.submit(client, 'm43:model-hang', 'first').json()
            self.ready(client, first)
            second = self.submit(client, 'm43:instant-failure', 'second').json()
            started = time.monotonic()
            expired = self.wait(client, second['operation_id'])
            elapsed = time.monotonic() - started
            self.assertEqual(expired.error, 'QUEUE_TIMEOUT')
            self.assertIsNone(expired.worker_pid)
            self.assertLess(elapsed, 1)
            running = client.app.state.service.store.operation(first['operation_id'])
            self.assertEqual(running.status, 'RUNNING')
            replay = self.submit(client, 'm43:instant-failure', 'second').json()
            self.assertTrue(replay['replayed'])
            self.records.append({'expiry_seconds': elapsed, 'queued_seconds': expired.queue_seconds, 'first_still_running': True})

    def test_startup_and_whole_execution_timeouts_are_distinct(self):
        for mode, reason, policy in [
            ('startup-hang', 'WORKER_START_TIMEOUT', replace(self.policy, worker_start_seconds=.5)),
            ('execution-hang', 'EXECUTION_TIMEOUT', replace(self.policy, execution_seconds=.9))]:
            with self.subTest(mode=mode), TestClient(self.app(policy, mode)) as client:
                started = time.monotonic()
                accepted = self.submit(client, 'm43:' + mode).json()
                operation = self.wait(client, accepted['operation_id'])
                elapsed = time.monotonic() - started
                self.assertEqual(operation.error, reason)
                self.assert_dead(operation.worker_pid)
                self.assertLess(elapsed, 4)
                self.records.append({'mode': mode, 'error': operation.error, 'elapsed': elapsed})

    def test_protocol_failures_kill_the_owned_worker(self):
        for mode in ['bad-protocol', 'oversized-protocol', 'stale-protocol']:
            with self.subTest(mode=mode), TestClient(self.app(folder=mode)) as client:
                accepted = self.submit(client, 'm43:' + mode).json()
                operation = self.wait(client, accepted['operation_id'])
                self.assertEqual(operation.error, 'WORKER_PROTOCOL_ERROR')
                self.assert_dead(operation.worker_pid)
                self.records.append({'mode': mode, 'error': operation.error, 'pid': operation.worker_pid})

    def test_cross_process_pauses_keep_frozen_input_and_document_runs_once(self):
        with TestClient(self.app()) as client:
            accepted = self.submit(client, '原训练安排', 'normal').json()
            first = self.wait(client, accepted['operation_id'])
            self.assertEqual(first.status, 'PAUSED')
            used = first.execution_seconds
            first_pid = first.worker_pid
            self.assert_dead(first_pid)
            mission = client.get(f"/api/missions/{accepted['mission_id']}").json()
            self.assertEqual(mission['blocked']['reason'], 'missing_user_input')
            write_fixture(self.player, speed=99)
            time.sleep(.1)
            self.assertEqual(client.app.state.service.store.operation(first.id).execution_seconds, used)
            response = client.post(f"/api/missions/{accepted['mission_id']}/input", json={
                'request_id': 'information', 'pause_id': mission['blocked']['pause_id'], 'values': {'information': '恢复正常'}})
            self.assertEqual(response.status_code, 202, response.text)
            second = self.wait(client, first.id)
            self.assertEqual((second.status, second.run_id), ('PAUSED', 2))
            self.assertGreater(second.execution_seconds, used)
            mission = client.get(f"/api/missions/{accepted['mission_id']}").json()
            self.assertEqual(mission['blocked']['reason'], 'report_approval')
            response = client.post(f"/api/missions/{accepted['mission_id']}/input", json={
                'request_id': 'approval', 'pause_id': mission['blocked']['pause_id'], 'values': {'approved': True}})
            self.assertEqual(response.status_code, 202, response.text)
            final = self.wait(client, first.id)
            self.assertEqual((final.status, final.run_id), ('COMPLETED', 3))
            self.assert_dead(final.worker_pid)
            report = client.get(f"/api/missions/{accepted['mission_id']}/report").json()['markdown']
            self.assertIn('原球员速度：44', report)
            self.assertNotIn('原球员速度：99', report)
            events = client.app.state.service.store.events(accepted['mission_id'], 0)
            documents = [event for event in events if event['type'] == 'agent.started' and event['data'].get('agent') == 'Document']
            self.assertEqual(len(documents), 1)
            self.records.append({'runs': final.run_id, 'pids': [first_pid, second.worker_pid, final.worker_pid],
                'execution_seconds': final.execution_seconds, 'paused_seconds_excluded': True, 'document_events': len(documents)})
            # Explain runs as its own operation; a timeout must preserve the delivered report.
            original = client.app.state.service.store.get(accepted['mission_id']).model_dump(mode='json')
            explanation = self.submit(client, '解释原报告', 'explain', mission_id=accepted['mission_id'], intent='explain').json()
            failed = self.wait(client, explanation['operation_id'])
            self.assertEqual(failed.error, 'MODEL_TIMEOUT')
            self.assert_dead(failed.worker_pid)
            self.assertEqual(client.get(f"/api/missions/{accepted['mission_id']}/report").json()['markdown'], report)
            self.assertEqual(client.app.state.service.store.get(accepted['mission_id']).model_dump(mode='json'), original | {
                'sequence': client.app.state.service.store.get(accepted['mission_id']).sequence})

    def test_cumulative_budget_survives_pause_and_limits_resume(self):
        with TestClient(self.app()) as client:
            accepted = self.submit(client, '累计执行预算验证', 'budget').json()
            paused = self.wait(client, accepted['operation_id'])
            self.assertEqual(paused.status, 'PAUSED')
            used = paused.execution_seconds
            mission = client.get(f"/api/missions/{accepted['mission_id']}").json()
            # Shrink only the test service's total to prove resume uses remaining, not a fresh total.
            client.app.state.service.policy = replace(self.policy, execution_seconds=used + .1)
            response = client.post(f"/api/missions/{accepted['mission_id']}/input", json={
                'request_id': 'budget-resume', 'pause_id': mission['blocked']['pause_id'], 'values': {'information': '恢复正常'}})
            self.assertEqual(response.status_code, 202, response.text)
            failed = self.wait(client, paused.id)
            self.assertEqual((failed.error, failed.run_id), ('EXECUTION_TIMEOUT', 2))
            self.assertGreater(failed.execution_seconds, used)
            self.assertLess(failed.execution_seconds - used, 2)
            self.records.append({'before_resume': used, 'total': failed.execution_seconds, 'configured_total': used + .1})

    def test_shutdown_stops_admission_cleans_workers_and_releases_writer(self):
        from backend.service import MissionService
        policy = replace(self.policy, model_call_seconds=5)
        with TestClient(self.app(policy)) as client:
            first = self.submit(client, 'm43:model-hang', 'first').json()
            running = self.ready(client, first)
            second = self.submit(client, 'm43:instant-failure', 'second').json()
            service = client.app.state.service
            started = time.monotonic()
            service.executor.begin_shutdown()
            rejected = self.submit(client, 'm43:instant-failure', 'third')
            self.assertEqual(rejected.status_code, 503)
            self.assertEqual(rejected.json()['code'], 'SERVICE_STOPPING')
            service.close()
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, policy.shutdown_seconds)
            self.assert_dead(running.worker_pid)
            # After close every new write is still a 503, never access to a closed connection.
            self.assertEqual(self.submit(client, 'm43:instant-failure', 'third').status_code, 503)
            reopened = MissionService(self.root / 'tasks', player_repository=self.repository,
                runtime_policy=self.policy, runner=ProcessRunner('evaluation.m43_worker'))
            try:
                for accepted in [first, second]:
                    self.assertEqual(reopened.store.operation(accepted['operation_id']).error, 'SERVICE_SHUTDOWN')
            finally:
                reopened.close()
            self.records.append({'shutdown_seconds': elapsed, 'worker_pid': running.worker_pid, 'writer_reacquired': True})

    def test_owner_crash_kills_real_service_worker_and_restart_does_not_replay(self):
        from backend.service import MissionService
        folder = self.root / 'crashed'
        with (self.root / 'owner.log').open('w', encoding='utf-8') as log:
            with OwnedProcess('evaluation.m43_owner_probe', folder, root=REPO, output=log) as owner:
                end = time.monotonic() + 45
                while not (folder / 'ready.json').exists():
                    if owner.process.poll() is not None or time.monotonic() > end:
                        self.fail('Owner did not reach running worker; see owner.log')
                    time.sleep(.02)
                ready = json.loads((folder / 'ready.json').read_text(encoding='utf-8'))
                with process_handle(ready['worker_pid']) as exited:
                    self.assertFalse(exited())
                    started = time.monotonic()
                    owner.process.kill()  # keep outer Job open: nested owner's private Job must do the cleanup
                    owner.wait(3)
                    self.assertTrue(exited(2000))
                    elapsed = time.monotonic() - started
        reopened = MissionService(folder / 'tasks', player_repository=self.repository,
            runtime_policy=self.policy, runner=ProcessRunner('evaluation.m43_worker'))
        try:
            operation = reopened.store.operation(ready['operation_id'])
            self.assertEqual(operation.error, 'INTERRUPTED')
            self.assertFalse(reopened.active)
            self.assertEqual(reopened.store.get(ready['mission_id']).status, 'FAILED')
        finally:
            reopened.close()
        self.records.append({'owner_exit_to_child_exit_seconds': elapsed, 'worker_pid': ready['worker_pid'], 'restart': 'INTERRUPTED without replay'})


class ProviderPolicyTests(unittest.TestCase):
    def test_all_seven_roles_and_explanation_emit_the_same_model_span(self):
        from agents.manager import ManagerAgent
        from agents.analyst import AnalystAgent
        from agents.coach import CoachAgent
        from agents.nutrition import NutritionAgent
        from agents.career import CareerAgent
        from agents.reviewer import ReviewerAgent
        from agents.document import DocumentAgent
        from backend.call_limits import execution_scope, ExecutionFailure
        from backend.execution_worker import execute
        model = Mock(invoke=Mock(return_value=SimpleNamespace(content='offline')))
        policy = RuntimePolicy()
        events = []
        with execution_scope(policy, time.monotonic() + 10, emit=events.append):
            for role in [ManagerAgent, AnalystAgent, CoachAgent, NutritionAgent, CareerAgent, ReviewerAgent, DocumentAgent]:
                self.assertEqual(role(model)._invoke_llm([]).content, 'offline')
            result = execute({'kind': 'explanation', 'system': 'offline', 'history': []}, policy, events.append, llm_factory=lambda: model)
            self.assertEqual(result['answer'], 'offline')
        starts = [e for e in events if e['type'] == 'span_start']
        ends = [e for e in events if e['type'] == 'span_end']
        self.assertEqual([e['kind'] for e in starts], ['model'] * 8)
        self.assertEqual([e['span_id'] for e in starts], [e['span_id'] for e in ends])
        with execution_scope(policy, time.monotonic() - 1):
            with self.assertRaises(ExecutionFailure):
                ManagerAgent(model)._invoke_llm([])
        self.assertEqual(model.invoke.call_count, 8)

    def test_search_transport_timeout_is_clipped_to_remaining_execution(self):
        from config import config
        from tools.search import SearchTool
        from backend.call_limits import execution_scope, invoke_tool
        client = Mock(search=Mock(return_value={'answer': 'offline', 'results': []}))
        events = []
        with patch.object(config, 'TAVILY_API_KEY', 'offline'), patch('tavily.TavilyClient', return_value=client), \
                execution_scope(RuntimePolicy(), time.monotonic() + .2, emit=events.append):
            self.assertIn('offline', invoke_tool(SearchTool, {'query': 'offline'}))
        self.assertGreater(client.search.call_args.kwargs['timeout'], 0)
        self.assertLessEqual(client.search.call_args.kwargs['timeout'], .2)
        self.assertEqual([e['type'] for e in events], ['span_start', 'span_end'])
        self.assertEqual(events[0]['kind'], 'tool')

    def test_sdk_timeouts_and_attempts_use_policy_for_all_shared_model_clients(self):
        import httpx
        from langchain_openai import ChatOpenAI
        from backend.call_limits import execution_scope
        from utils.helpers import create_llm
        policy = RuntimePolicy(model_connect_seconds=.2, model_read_seconds=.4, model_call_seconds=3,
            tool_seconds=1, execution_seconds=10, network_max_attempts=2)
        results = []
        for statuses, expected in [([500, 200], 2), ([400], 1), ([500, 500], 2)]:
            attempts = []
            def respond(request):
                attempts.append({'timeout': request.extensions['timeout'], 'url': str(request.url)})
                status = statuses[min(len(attempts) - 1, len(statuses) - 1)]
                if status != 200:
                    return httpx.Response(status, json={'error': {'message': 'offline fault', 'type': 'test'}})
                return httpx.Response(200, json={'id': 'offline', 'object': 'chat.completion', 'created': 0, 'model': 'offline',
                    'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'stop'}]})
            with httpx.Client(transport=httpx.MockTransport(respond)) as client:
                def factory(**kwargs):
                    return ChatOpenAI(**{**kwargs, 'api_key': 'offline-only', 'base_url': 'http://offline.invalid/v1', 'http_client': client})
                with patch('utils.helpers.ChatOpenAI', side_effect=factory), execution_scope(policy, time.monotonic() + 10):
                    model = create_llm()
                    self.assertEqual(model.max_retries, 1)
                    if statuses[-1] == 200:
                        self.assertEqual(model.invoke('offline').content, 'ok')
                    else:
                        with self.assertRaises(Exception):
                            model.invoke('offline')
                self.assertEqual(len(attempts), expected)
                for attempt in attempts:
                    self.assertEqual(attempt['timeout'], {'connect': .2, 'read': .4, 'write': .4, 'pool': .2})
            results.append({'statuses': statuses, 'attempts': attempts})
        root = Path(os.environ['FAIT_M43_TEST_ROOT']) / self._testMethodName
        root.mkdir(parents=True, exist_ok=False)
        (root / 'evidence.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
