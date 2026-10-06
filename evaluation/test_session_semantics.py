"""C1.1: real HTTP/store/checkpoints with deterministic nodes and explanation model."""
import contextlib
import io
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from backend.main import create_app
from evaluation.player_fixtures import write_fixture
from player_data.repository import FixtureRepository


class SessionSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fixture = self.root / 'player'
        write_fixture(self.fixture, name='Original Player', speed=44)
        self.repository = FixtureRepository(self.fixture)
        self.calls = []
        self.require_input = False

    def tearDown(self):
        self.temp.cleanup()

    def app(self):
        return create_app(data_dir=self.root / 'tasks', demo=False, player_repository=self.repository)

    def runtime(self, path, on_start):
        from graph import build_graph
        from langgraph.checkpoint.sqlite import SqliteSaver
        from agents.manager import ManagerAgent
        from evaluation.test_looping_plan import task, complete_output
        from loop_contracts import build_v2_state_patch, normalise_review_result
        from report_validation import accept_document
        manager = ManagerAgent(None)

        def planning(state):
            mission = {'primary_goal': state['user_context']['request'], 'objective': state['user_context']['request'],
                'constraints': [], 'context': {'information_gaps': []}, 'output_type': 'report'}
            plan = {'version': 1, 'objective': mission['objective'], 'constraints': [], 'information_gaps': [],
                'subtasks': [task('training')], 'revision_targets': [], 'termination_reason': ''}
            material = {**state, 'mission': mission, 'plan': plan}
            return {'mission': mission, 'plan': plan, **build_v2_state_patch(material)}

        def reviewer(state):
            blocked = self.require_input and not state['user_context'].get('human_inputs')
            result = normalise_review_result({'decision': 'BLOCKED' if blocked else 'PASS',
                'blocking_information': ['原任务需要恢复反馈'] if blocked else [],
                'findings': [{'action': 'BLOCKED', 'severity': 'HIGH', 'subtask_ids': ['training'],
                    'description': '缺少恢复反馈', 'reason': '缺少恢复反馈', 'evidence': []}] if blocked else [],
                'summary': '需要恢复反馈' if blocked else '已通过', 'reviewed_subtasks': ['training']}, state['subtasks'])
            return {'review_v2': result, 'review': {'decision': result['decision']}}

        def document(state):
            return accept_document(state, '# Original report\n' + state['mission']['primary_goal']
                + '\n原球员速度：' + str(state['player_snapshot']['profile']['attributes']['physical']['speed'])
                + '\n依据与限制：确定性测试样本')

        nodes = {'manager': planning, 'intent_checkpoint': lambda state: {}, 'manager_revision': manager.run_revision,
            'manager_replan': manager.run_replan, 'reviewer': reviewer,
            'coach': lambda state: {'domain_outputs': {'Coach': complete_output('training')}},
            'analyst': lambda state: {}, 'nutrition': lambda state: {}, 'career': lambda state: {}, 'document': document}
        def wrap(name, function):
            def run(state):
                self.calls.append((name, state['player_snapshot']['metadata']['state_version']))
                on_start(name, state)
                return function(state)
            return run
        connection = sqlite3.connect(str(path), check_same_thread=False)
        return build_graph({name: wrap(name, fn) for name, fn in nodes.items()},
            checkpointer=SqliteSaver(connection), interrupt_before=['document']), connection

    def start(self, client):
        response = client.post('/api/messages', json={'conversation_id': 'conv_session', 'content': '解释训练安排依据'})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()['mission_id']

    def wait(self, client, identity, status):
        # The first graph import can exceed a few seconds on Windows.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if identity not in client.app.state.service.active:
                view = client.get(f'/api/missions/{identity}').json()
                if view['status'] == status:
                    return view
                if view['status'] == 'FAILED' and status != 'FAILED':
                    self.fail(view['error'])
            time.sleep(0.01)
        self.fail(f'Task did not reach {status}')

    def complete(self, client, identity):
        paused = self.wait(client, identity, 'BLOCKED')
        self.assertEqual(paused['blocked']['reason'], 'report_approval')
        self.assertEqual(client.post(f'/api/missions/{identity}/input', json={'values': {'approved': True}}).status_code, 202)
        return self.wait(client, identity, 'COMPLETED')

    def explain(self, client, identity, *, intent='explain'):
        result = client.post('/api/messages', json={'conversation_id': 'conv_session', 'mission_id': identity,
            'intent': intent, 'content': '为什么原报告这样安排？'})
        self.assertEqual(result.status_code, 202, result.text)
        self.assertEqual(result.json()['mission_id'], identity)
        self.wait(client, identity, 'COMPLETED')
        return result.json()['message_id']

    def test_history_and_explanation_keep_original_version_and_never_run_graph(self):
        with patch('backend.runtime.create_runtime', side_effect=self.runtime):
            with TestClient(self.app()) as client:
                identity = self.start(client)
                old = self.complete(client, identity)
                service = client.app.state.service
                original_report = service.store.report(identity)
                original_events = len(service.store.events(identity, 0))
                old_calls = list(self.calls)
                write_fixture(self.fixture, name='Latest Player', speed=98)
                new_version = self.repository.read_snapshot().metadata['state_version']
                self.assertNotEqual(new_version, old['input_reference']['state_version'])
                model = Mock()
                model.invoke.return_value = SimpleNamespace(content='原报告的安排依据来自原任务。')
                with patch.object(self.repository, 'read_snapshot', side_effect=AssertionError('Latest read forbidden')):
                    with patch('backend.runtime.create_runtime', side_effect=AssertionError('Graph forbidden')):
                        for _ in range(3):
                            self.assertEqual(client.get(f'/api/missions/{identity}').json()['input_reference'], old['input_reference'])
                            self.assertEqual(client.get('/api/missions').json()[0]['id'], identity)
                            self.assertEqual(client.get(f'/api/missions/{identity}/report').status_code, 200)
                        with patch('utils.helpers.create_llm', return_value=model):
                            question_id = self.explain(client, identity)
                self.assertEqual(self.calls, old_calls)
                current = client.get(f'/api/missions/{identity}').json()
                for key in ('status', 'input_reference', 'report', 'review', 'review_history', 'result', 'telemetry'):
                    self.assertEqual(current[key], old[key])
                self.assertEqual(service.store.report(identity), original_report)
                explanation_events = service.store.events(identity, original_events)
                self.assertEqual([event['type'] for event in explanation_events], ['message.created', 'message.created'])
                messages = model.invoke.call_args.args[0]
                self.assertEqual(len(messages), 2)  # system + explicit explanation question
                prompt = messages[0].content
                self.assertIn(old['input_reference']['state_version'], prompt)
                self.assertIn('原球员速度：44', prompt)
                self.assertNotIn(new_version, prompt)
                self.assertNotIn('Latest Player', prompt)
                self.assertIn('不生成新的方案', prompt)
                answer = service.store.messages('conv_session')[-1]
                self.assertEqual(answer.kind, 'explanation')
                self.assertEqual(answer.in_reply_to, question_id)
                self.assertEqual(current['available_operations'], ['view', 'explain', 'reevaluate'])
                self.assertNotIn(str(self.fixture), str(current))
                self.assertNotIn('channel_values', str(current))

    def test_failed_or_empty_explanation_does_not_damage_report_and_followup_is_compatible(self):
        with patch('backend.runtime.create_runtime', side_effect=self.runtime):
            with TestClient(self.app()) as client:
                identity = self.start(client)
                old = self.complete(client, identity)
                service = client.app.state.service
                report = service.store.report(identity)
                for response in (RuntimeError('private model failure'), '   ', []):
                    with self.subTest(response=response):
                        model = Mock()
                        if isinstance(response, Exception):
                            model.invoke.side_effect = response
                        else:
                            model.invoke.return_value = SimpleNamespace(content=response)
                        sequence = service.store.get(identity).sequence
                        with patch('utils.helpers.create_llm', return_value=model), self.assertLogs(level='ERROR'):
                            self.explain(client, identity, intent='followup')
                        after = client.get(f'/api/missions/{identity}').json()
                        self.assertEqual(after['status'], 'COMPLETED')
                        self.assertEqual(after['report'], old['report'])
                        self.assertEqual(after['review'], old['review'])
                        self.assertEqual(after['result'], old['result'])
                        self.assertEqual(service.store.report(identity), report)
                        failure = service.store.messages('conv_session')[-1]
                        self.assertEqual(failure.operation_status, 'FAILED')
                        self.assertEqual(failure.kind, 'explanation')
                        events = service.store.events(identity, sequence)
                        self.assertTrue(all(event['type'] == 'message.created' for event in events))
                        self.assertEqual(events[-1]['data']['operation_status'], 'FAILED')
                        self.assertNotIn('private model failure', str(events))

    def test_restart_missing_input_and_report_approval_resume_same_snapshot(self):
        self.require_input = True
        with patch('backend.runtime.create_runtime', side_effect=self.runtime):
            with TestClient(self.app()) as client:
                identity = self.start(client)
                paused = self.wait(client, identity, 'BLOCKED')
                self.assertEqual(paused['available_operations'], ['view', 'supply_input'])
                reference = paused['input_reference']
                self.assertEqual(client.post('/api/messages', json={'conversation_id': 'conv_session', 'mission_id': identity,
                    'intent': 'explain', 'content': '继续'}).status_code, 409)
            write_fixture(self.fixture, name='Changed after pause', speed=91)
            with TestClient(self.app()) as client:
                with patch.object(self.repository, 'read_snapshot', side_effect=AssertionError('Must keep old snapshot')):
                    response = client.post('/api/messages', json={'conversation_id': 'conv_session', 'mission_id': identity,
                        'intent': 'followup', 'content': '原任务补充：睡眠八小时'})
                    self.assertEqual(response.status_code, 202, response.text)
                    self.assertEqual(response.json()['mission_id'], identity)
                    approval = self.wait(client, identity, 'BLOCKED')
                    self.assertEqual(approval['available_operations'], ['view', 'approve_report'])
                    calls = list(self.calls)
                    for values in ({'approved': False}, {'information': '批准'}, {'approved': 'yes'}):
                        self.assertEqual(client.post(f'/api/missions/{identity}/input', json={'values': values}).status_code, 422)
                    self.assertEqual(client.post('/api/messages', json={'conversation_id': 'conv_session', 'mission_id': identity,
                        'intent': 'followup', 'content': '批准'}).status_code, 409)
                    self.assertEqual(self.calls, calls)
                    complete = self.complete(client, identity)
                self.assertEqual(complete['input_reference'], reference)
                self.assertEqual(self.calls[len(calls):], [('document', reference['state_version'])])
                self.assertTrue(all(version == reference['state_version'] for _, version in self.calls))
                self.assertEqual(len(client.get('/api/missions').json()), 1)
                self.assertIn('原球员速度：44', client.get(f'/api/missions/{identity}/report').json()['markdown'])

    def test_missing_or_incompatible_checkpoint_rejected_before_mutation(self):
        with patch('backend.runtime.create_runtime', side_effect=self.runtime):
            for kind in ('missing', 'wrong_pause', 'missing_snapshot', 'wrong_version', 'unverified', 'missing_pause', 'unsupported_pause'):
                with self.subTest(kind=kind), TestClient(self.app()) as client:
                    identity = self.start(client)
                    self.wait(client, identity, 'BLOCKED')
                    service = client.app.state.service
                    if kind == 'missing':
                        with contextlib.closing(sqlite3.connect(service.checkpoint_path)) as connection:
                            with connection:
                                connection.execute('DELETE FROM checkpoints WHERE thread_id=?', (identity,))
                    elif kind in {'missing_snapshot', 'wrong_version'}:
                        graph, connection = self.runtime(service.checkpoint_path, lambda *_: None)
                        try:
                            config = {'configurable': {'thread_id': identity}}
                            raw = graph.get_state(config).values['player_snapshot']
                            if kind == 'missing_snapshot':
                                raw.pop('profile')
                            else:
                                raw['metadata']['state_version'] = 'wrong-version'
                            graph.update_state(config, {'player_snapshot': raw}, as_node='reviewer')
                        finally:
                            connection.close()
                    else:
                        mission = service.store.get(identity)
                        if kind == 'wrong_pause':
                            mission.blocked.reason = 'missing_user_input'
                        elif kind == 'missing_pause':
                            mission.blocked = None
                        elif kind == 'unsupported_pause':
                            mission.blocked.reason = 'legacy-unknown'
                        else:
                            from backend.models import PlayerInputReference
                            mission.input_reference = PlayerInputReference()
                        service.store.save(mission)
                    calls = list(self.calls)
                    events = service.store.events(identity, 0)
                    message_count = len(service.store.messages('conv_session'))
                    before = service.store.get(identity).model_dump()
                    view = client.get(f'/api/missions/{identity}').json()
                    self.assertEqual(view['available_operations'], ['view'])
                    self.assertTrue(view['resume_error'])
                    self.assertEqual(client.post(f'/api/missions/{identity}/input', json={'values': {'approved': True}}).status_code, 422)
                    self.assertEqual(client.post('/api/messages', json={'conversation_id': 'conv_session', 'mission_id': identity,
                        'intent': 'followup', 'content': '继续'}).status_code, 409)
                    self.assertEqual(service.store.get(identity).model_dump(), before)
                    self.assertEqual(len(service.store.messages('conv_session')), message_count)
                    self.assertEqual(service.store.events(identity, 0), events)
                    self.assertEqual(self.calls, calls)

    def test_cli_metadata_only_history_is_read_only_and_needs_no_model_config(self):
        import app
        from utils import sessions
        index = self.root / 'sessions.json'
        with patch.object(sessions, 'SESSIONS_FILE', str(index)):
            identity = sessions.create_session('metadata-only CLI task')
            original = index.read_bytes()
            output = io.StringIO()
            with patch('sys.argv', ['app.py', '--continue', identity]):
                with patch.object(app, 'check_config', side_effect=AssertionError('Read-only history needs no API key')):
                    with patch.object(app, 'build_agent_graph', side_effect=AssertionError('No graph allowed')):
                        with patch.object(app, 'create_initial_state', side_effect=AssertionError('No initial state allowed')):
                            with patch('builtins.input', side_effect=AssertionError('No execution prompt allowed')):
                                with contextlib.redirect_stdout(output):
                                    app.main()
            self.assertEqual(index.read_bytes(), original)
            self.assertIn('无法恢复原执行', output.getvalue())
            self.assertNotIn('[恢复会话]', output.getvalue())

    def test_cli_same_process_approval_delivers_only_publishable_report(self):
        import app
        from graph import create_initial_state
        graph, connection = self.runtime(self.root / 'cli.sqlite3', lambda *_: None)
        output = io.StringIO()
        try:
            initial = create_initial_state('CLI报告流程验收', snapshot=self.repository.read_snapshot())
            with patch('builtins.input', return_value=''), contextlib.redirect_stdout(output):
                report, state = app.run_stream(graph, initial, 'cli-task')
                app.print_result(report, state, {}, graph)
            self.assertIn('原球员速度：44', output.getvalue())
            self.assertEqual([name for name, _ in self.calls].count('document'), 1)
            rejected = SimpleNamespace(values={**state.values, 'delivery_status': 'NOT_GENERATED',
                'final_report': 'UNPUBLISHED REPORT MUST STAY HIDDEN', 'failure_reason': '报告未通过发布检查'})
            failure_output = io.StringIO()
            with contextlib.redirect_stdout(failure_output):
                app.print_result('UNPUBLISHED REPORT MUST STAY HIDDEN', rejected, {}, graph)
            self.assertNotIn('UNPUBLISHED REPORT MUST STAY HIDDEN', failure_output.getvalue())
            self.assertNotIn('协作流程完成', failure_output.getvalue())
            self.assertIn('未交付报告', failure_output.getvalue())
        finally:
            connection.close()


if __name__ == '__main__':
    unittest.main()
