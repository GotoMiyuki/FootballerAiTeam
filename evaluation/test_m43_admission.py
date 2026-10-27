"""Small lifecycle edge checks using the deterministic demo and isolated stores."""
import json
import os
from pathlib import Path
import time
import unittest

from fastapi.testclient import TestClient
from backend.main import create_app
from backend.models import OperationView
from backend.events import now
from backend.runtime_contracts import RuntimePolicy, OperationKind, OperationStatus
from backend.service import MissionService
from evaluation.player_fixtures import write_fixture
from player_data.repository import FixtureRepository


class AdmissionAndHistoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.environ['FAIT_M43_EXTRA_ROOT']) / self._testMethodName
        self.root.mkdir(parents=True, exist_ok=False)
        write_fixture(self.root / 'player', speed=44)
        self.repository = FixtureRepository(self.root / 'player')
        self.policy = RuntimePolicy(model_connect_seconds=.1, model_read_seconds=.2, model_call_seconds=5,
            tool_seconds=5, execution_seconds=20, queue_wait_seconds=10, queue_capacity=1,
            cleanup_seconds=1, shutdown_seconds=3)
        self.app = create_app(data_dir=self.root / 'tasks', demo=True, demo_delay=0,
            player_repository=self.repository, runtime_policy=self.policy)

    def wait(self, predicate):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            result = predicate()
            if result:
                return result
            time.sleep(.01)
        self.fail('Demo did not reach its expected boundary')

    def submit(self, client, request_id, **body):
        response = client.post('/api/messages', json={'request_id': request_id, 'conversation_id': 'admission',
            'content': '演示训练安排', **body})
        self.assertEqual(response.status_code, 202, response.text)
        accepted = response.json()
        self.wait(lambda: accepted['mission_id'] not in client.app.state.service.active)
        return accepted

    def test_full_queue_rejects_create_explain_continuation_and_input_without_writes(self):
        with TestClient(self.app) as client:
            completed = self.submit(client, 'complete')
            blocked = self.submit(client, 'pause', demo_scenario='blocked')
            service = client.app.state.service
            pause = service.store.get(blocked['mission_id']).blocked.pause_id
            service.demo_delay = 10
            running = client.post('/api/messages', json={'request_id': 'hold', 'conversation_id': 'admission', 'content': '等待'}).json()
            self.wait(lambda: service.store.get(running['mission_id']).agents[0].status == 'RUNNING')
            self.assertEqual(client.post('/api/messages', json={'request_id': 'queued', 'conversation_id': 'admission', 'content': '排队'}).status_code, 202)
            def counts():
                with service.store.lock:
                    return {table: service.store.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                        for table in ['missions', 'messages', 'events', 'operations', 'request_receipts', 'creation_requests', 'mission_inputs']}
            before = counts()
            cases = [('/api/messages', {'request_id': 'new-reject', 'conversation_id': 'admission', 'content': '新任务'}),
                ('/api/messages', {'request_id': 'explain-reject', 'conversation_id': 'admission', 'content': '解释', 'intent': 'explain', 'mission_id': completed['mission_id']}),
                ('/api/mission-continuations', {'request_id': 'continue-reject', 'conversation_id': 'admission', 'parent_mission_id': completed['mission_id'], 'content': '新要求', 'reason': '调整'}),
                (f"/api/missions/{blocked['mission_id']}/input", {'request_id': 'input-reject', 'pause_id': pause,
                    'values': {'doms': 1, 'sleep_hours': 8, 'fatigue': 1, 'pain': False, 'availability': '30 分钟'}})]
            for route, body in cases:
                with self.subTest(route=route, request=body['request_id']):
                    response = client.post(route, json=body)
                    self.assertEqual(response.status_code, 429, response.text)
                    self.assertEqual(response.json()['code'], 'QUEUE_FULL')
                    self.assertEqual(counts(), before)
            self.assertEqual(service.store.get(blocked['mission_id']).blocked.pause_id, pause)
            evidence = {'entries': [body['request_id'] for _, body in cases], 'before': before, 'after': counts(), 'pause_preserved': pause}
            (self.root / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')

    def test_restart_does_not_label_unrecorded_running_time_complete(self):
        with TestClient(self.app) as client:
            completed = self.submit(client, 'complete')
            service = client.app.state.service
            original_report = service.store.report(completed['mission_id'])
            for name, status in [('running', OperationStatus.RUNNING), ('queued', OperationStatus.QUEUED)]:
                service.store.save_operation(OperationView(id='op_' + name, mission_id=completed['mission_id'],
                    kind=OperationKind.EXPLANATION, status=status, created_at=now(),
                    execution_seconds=2.5 if name == 'running' else 0, budget_history_complete=True))
        reopened = MissionService(self.root / 'tasks', demo=True, demo_delay=0,
            player_repository=self.repository, runtime_policy=self.policy)
        try:
            running, queued = reopened.store.operation('op_running'), reopened.store.operation('op_queued')
            self.assertFalse(running.budget_history_complete)
            self.assertEqual(running.execution_seconds, 2.5)
            self.assertTrue(queued.budget_history_complete)
            self.assertEqual((running.error, queued.error), ('INTERRUPTED', 'INTERRUPTED'))
            self.assertEqual(reopened.store.report(completed['mission_id']), original_report)
            (self.root / 'evidence.json').write_text(json.dumps({'running': running.model_dump(mode='json'),
                'queued': queued.model_dump(mode='json')}, indent=2), encoding='utf-8')
        finally:
            reopened.close()
