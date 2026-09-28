"""Application boundary tests: real HTTP API, SQLite, event replay and core graph routing."""
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.main import create_app
from backend.models import MissionEvent

class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def wait(self, client, mission_id, status):
        for _ in range(250):
            response = client.get(f'/api/missions/{mission_id}').json()
            if response['status'] == status:
                # Lifecycle events and active-worker cleanup follow status publication.
                if mission_id not in client.app.state.service.active:
                    return response
            if response['status'] == 'FAILED' and status != 'FAILED':
                self.fail(response['error'])
            time.sleep(0.01)
        self.fail(f'Mission did not reach {status}: {response}')

    def start(self, client, scenario='pass'):
        response = client.post('/api/messages', json={'conversation_id': 'conv_test', 'content': '分析训练效果', 'demo_scenario': scenario})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()['mission_id']

    def test_pass_report_history_restart_and_followup(self):
        with TestClient(create_app(data_dir=self.path, demo=True, demo_delay=0)) as client:
            mission_id = self.start(client)
            snapshot = self.wait(client, mission_id, 'COMPLETED')
            self.assertEqual(snapshot['review']['decision'], 'PASS')
            self.assertEqual(client.get('/api/player').json()['name'], 'GotoMiyuki')
            self.assertIsInstance(client.get('/api/player/training-history').json(), list)
            self.assertIsInstance(client.get('/api/player/match-history').json(), list)
            self.assertIn('演示报告', client.get(f'/api/missions/{mission_id}/report').json()['markdown'])
            download = client.get(f'/api/missions/{mission_id}/report/download')
            self.assertIn('filename*=UTF-8', download.headers['content-disposition'])
            self.assertEqual(download.status_code, 200)
            followup = client.post('/api/messages', json={'conversation_id': 'conv_test', 'content': '为什么这样安排？', 'intent': 'followup', 'mission_id': mission_id})
            self.assertEqual(followup.json()['mission_id'], mission_id)
            self.wait(client, mission_id, 'COMPLETED')
            self.assertEqual(len(client.get('/api/missions').json()), 1)
            self.assertEqual(len(client.get('/api/conversations/conv_test/messages').json()), 4)
            events = client.app.state.service.store.events(mission_id, 0)
            self.assertEqual([event['sequence'] for event in events], list(range(1, len(events) + 1)))
            for event in events:
                MissionEvent.model_validate(event)
                self.assertEqual(event['data']['snapshot']['sequence'], event['sequence'])
                self.assertNotIn('domain_outputs', event['data']['snapshot'])
            after = events[-3]['sequence']
            self.assertEqual(len(client.app.state.service.store.events(mission_id, after)), 2)
        with TestClient(create_app(data_dir=self.path, demo=True, demo_delay=0)) as client:
            restored = client.get(f'/api/missions/{mission_id}').json()
            self.assertEqual(restored['plan'], snapshot['plan'])
            self.assertEqual(restored['status'], 'COMPLETED')
            self.assertEqual(len(client.get('/api/missions').json()), 1)

    def test_blocked_validation_and_resume_across_restart(self):
        with TestClient(create_app(data_dir=self.path, demo=True, demo_delay=0)) as client:
            mission_id = self.start(client, 'blocked')
            snapshot = self.wait(client, mission_id, 'BLOCKED')
            self.assertIsNone(snapshot['error'])
            self.assertEqual(len(snapshot['blocked']['required_inputs']), 6)
        with TestClient(create_app(data_dir=self.path, demo=True, demo_delay=0)) as client:
            endpoint = f'/api/missions/{mission_id}/input'
            self.assertEqual(client.post(endpoint, json={'values': {}}).status_code, 422)
            values = {'doms': 3, 'sleep_hours': 7.5, 'fatigue': 4, 'pain': False, 'availability': '30 分钟'}
            self.assertEqual(client.post(endpoint, json={'values': {**values, 'doms': 99}}).status_code, 422)
            self.assertEqual(client.post(endpoint, json={'values': {**values, 'pain': 'unknown'}}).status_code, 422)
            self.assertEqual(client.post(endpoint, json={'values': values}).status_code, 202)
            snapshot = self.wait(client, mission_id, 'COMPLETED')
            self.assertIsNone(snapshot['blocked'])
            self.assertEqual(client.post(endpoint, json={'values': values}).status_code, 422)

    def test_revision_and_replan_are_distinct(self):
        with TestClient(create_app(data_dir=self.path, demo=True, demo_delay=0)) as client:
            revision_id = self.start(client, 'revision')
            revision = self.wait(client, revision_id, 'COMPLETED')
            replan_id = self.start(client, 'replan')
            replan = self.wait(client, replan_id, 'COMPLETED')
            self.assertEqual(revision['plan']['version'], 1)
            self.assertEqual(revision['plan']['subtasks'][-1]['revision_count'], 1)
            self.assertEqual(replan['plan']['version'], 2)
            self.assertEqual([review['decision'] for review in revision['review_history']], ['REVISE', 'PASS'])
            self.assertEqual([review['decision'] for review in replan['review_history']], ['REPLAN', 'PASS'])

    def test_unknown_mission_and_cross_conversation_rejected(self):
        with TestClient(create_app(data_dir=self.path, demo=True, demo_delay=0)) as client:
            self.assertEqual(client.get('/api/missions/missing').status_code, 404)
            self.assertEqual(client.post('/api/messages', json={'conversation_id': 'c', 'content': '  '}).status_code, 422)
            mission_id = self.start(client)
            self.wait(client, mission_id, 'COMPLETED')
            self.assertEqual(client.post('/api/messages', json={'conversation_id': 'other', 'content': '追问', 'intent': 'followup', 'mission_id': mission_id}).status_code, 409)

    def test_live_application_adapter_with_real_graph_and_durable_hitl(self):
        # Existing core routing + real revision logic; only model/tool nodes are doubled.
        from graph import build_graph
        from langgraph.checkpoint.sqlite import SqliteSaver
        from agents.manager import ManagerAgent
        from evaluation.test_looping_plan import state_for, task, complete_output
        from loop_contracts import normalise_review_result
        def runtime(path, on_start):
            manager = ManagerAgent(None)
            def planning(_state):
                initial = state_for([task('subtask_01', 'performance_analysis'), task('subtask_02')])
                return {key: initial[key] for key in ['mission', 'plan', 'plan_v2', 'subtasks']}
            def reviewer(state):
                blocked = not state.get('user_context', {}).get('human_inputs')
                review = normalise_review_result({'decision': 'BLOCKED' if blocked else 'PASS',
                    'blocking_information': ['请补充恢复反馈'] if blocked else [],
                    'findings': [{'action': 'BLOCKED', 'severity': 'HIGH', 'subtask_ids': ['subtask_02'], 'description': '缺少恢复反馈', 'reason': '缺少恢复反馈'}] if blocked else [],
                    'summary': '需要恢复反馈' if blocked else '已通过'}, state['subtasks'])
                return {'review_v2': review, 'review': {'decision': review['decision']}}
            def specialist(name):
                def run(state):
                    return {'domain_outputs': {name: complete_output(state['current_subtask'])}}
                return run
            nodes = {'manager': planning, 'intent_checkpoint': lambda state: {}, 'manager_revision': manager.run_revision,
                'manager_replan': manager.run_replan, 'reviewer': reviewer, 'coach': specialist('Coach'), 'analyst': specialist('Analyst'),
                'nutrition': specialist('Nutrition'), 'career': specialist('Career'), 'document': lambda state: {'final_report': '# Test report'}}
            def wrap(name, fn):
                def run(state):
                    on_start(name, state)
                    return fn(state)
                return run
            connection = sqlite3.connect(str(path), check_same_thread=False)
            graph = build_graph({name: wrap(name, fn) for name, fn in nodes.items()}, checkpointer=SqliteSaver(connection), interrupt_before=['document'])
            return graph, connection
        with patch('backend.runtime.create_runtime', side_effect=runtime):
            with TestClient(create_app(data_dir=self.path, demo=False)) as client:
                mission_id = self.start(client)
                blocked = self.wait(client, mission_id, 'BLOCKED')
                self.assertEqual(blocked['blocked']['reason'], 'missing_user_input')
            with TestClient(create_app(data_dir=self.path, demo=False)) as client:
                self.assertEqual(client.post(f'/api/missions/{mission_id}/input', json={'values': {'information': '酸痛3/10，无疼痛，睡眠8小时'}}).status_code, 202)
                approved = self.wait(client, mission_id, 'BLOCKED')
                self.assertEqual(approved['blocked']['reason'], 'report_approval')
                self.assertEqual(approved['plan']['subtasks'][0]['revision_count'], 0)
                self.assertEqual(approved['plan']['subtasks'][1]['revision_count'], 1)
                self.assertEqual(client.post(f'/api/missions/{mission_id}/input', json={'values': {'approved': False}}).status_code, 422)
                self.assertEqual(client.post(f'/api/missions/{mission_id}/input', json={'values': {'approved': True}}).status_code, 202)
                completed = self.wait(client, mission_id, 'COMPLETED')
                self.assertEqual(completed['review']['decision'], 'PASS')
                self.assertTrue(all(task['status'] == 'COMPLETED' for task in completed['plan']['subtasks']))
                self.assertEqual(client.get(f'/api/missions/{mission_id}/report').json()['markdown'], '# Test report')

if __name__ == '__main__':
    unittest.main()
