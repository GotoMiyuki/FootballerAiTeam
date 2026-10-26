"""E2.2 actual application/SQLite/A/E/C/B boundaries with synthetic observations and offline models."""
from copy import deepcopy
import json
import sqlite3
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from evaluation import test_assessments as assessment_tests
from backend.models import ContinuationRequest


class FeedbackFollowupTests(unittest.TestCase):
    _helpers = assessment_tests.AssessmentTests
    setUp, tearDown = _helpers.setUp, _helpers.tearDown
    add, app, start = _helpers.add, _helpers.app, _helpers.start
    plan_body, plan, evaluate = _helpers.plan_body, _helpers.plan, _helpers.evaluate

    def prepare(self, client):
        self.start(client)
        self.plan(client)
        decision = client.post(self.base + '/decisions', json=dict(context=self.context.to_dict(), request_id='choice',
            recommendation_revision=1, status='ADOPTED', reason='明确采用测试建议', previous_decision_id=None))
        self.assertEqual(decision.status_code, 200, decision.text)
        response = client.post(self.base + '/executions', json=dict(context=self.context.to_dict(), request_id='execution',
            recommendation_revision=1, status='PARTIAL', actual_action='UNIQUE_EXECUTION 练习20分钟（合成测试反馈）',
            window={'time_domain': 'game', 'label': '2026-09-04'}))
        self.assertEqual(response.status_code, 200, response.text)
        self.execution_record = response.json()['record']
        self.add('f1', '2026-09-04', {'rating': 4})
        self.add('f2', '2026-09-05', {'rating': 6})
        self.assessment = self.evaluate(client, execution_id=self.execution_record['execution_id'])['record']
        return client.app.state.service

    def body(self, **changes):
        return dict(parent_mission_id=self.identity, conversation_id='conv_session', request_id='adjust-one',
            reason='UNIQUE_REASON 观察后调整', content='UNIQUE_NEW_REQUEST 根据比赛评分调整速度训练',
            include_report=False, selected_results=[], recommendation_id=self.rec['recommendation_id'], recommendation_revision=1,
            assessment_id=self.assessment['assessment_id'], assessment_revision=self.assessment['revision'],
            execution_ids=[self.execution_record['execution_id']], **changes)

    def post(self, client, **changes):
        response = client.post('/api/mission-continuations', json=self.body(**changes))
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def test_fixed_feedback_latest_snapshot_old_execution_and_new_choice_independence(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.app()) as client:
                service = self.prepare(client)
                self.add('profile-new', '2026-09-06', {'attributes': {'physical': {'speed': 43}}}, kind='profile_patch')
                before = self.repository.state_path(self.context).read_bytes()
                with patch.object(service, '_enqueue'):
                    accepted = self.post(client)
                child = accepted['mission_id']
                fixed = service.frozen_feedback(child)
                self.assertTrue(fixed['assessment']['latest_input_differs_from_assessment'])
                self.assertEqual(fixed['assessment']['result']['after'], 5)
                self.assertEqual(fixed['decision']['status'], 'ADOPTED')
                self.assertEqual(fixed['executions'][0]['status'], 'PARTIAL')
                self.assertEqual(fixed['submission_input_reference']['state_version'], self.version)
                old_inputs = service.store.inputs(child)
                response = client.post(self.base + '/executions', json=dict(context=self.context.to_dict(), request_id='correct',
                    recommendation_revision=1, status='NOT_EXECUTED', actual_action='UNIQUE_CORRECTED 未练习',
                    corrects_execution_id=self.execution_record['execution_id'], correction_reason='测试更正'))
                self.assertEqual(response.status_code, 200, response.text)
                with patch.object(service.assessments, 'view', side_effect=AssertionError('No latest E after submit')), patch.object(
                        self.repository, 'read_snapshot', side_effect=AssertionError('No latest A after submit')):
                    service._execute(child)
                    self.harness.complete(client, child)
                    self.assertEqual(client.get(f'/api/missions/{child}/feedback-input').json(), fixed)
                self.assertEqual(service.store.inputs(child), old_inputs)
                self.assertEqual(self.repository.state_path(self.context).read_bytes(), before)
                recommendations = client.get(f'/api/missions/{child}/recommendations').json()['items']
                self.assertTrue(recommendations)
                for rec in recommendations:
                    self.assertEqual(rec['origin_feedback']['assessment_id'], self.assessment['assessment_id'])
                    activity = client.get(f'/api/missions/{child}/recommendations/{rec["recommendation_id"]}/activity', params=self.context.to_dict()).json()
                    self.assertIsNone(activity['current_decision_id'])
                    self.assertEqual(activity['executions'], [])
                links = client.get(self.base + '/followups', params=self.context.to_dict())
                self.assertEqual(links.status_code, 200, links.text)
                self.assertEqual(links.json()['items'][0]['mission_id'], child)
                self.assertEqual(links.json()['items'][0]['status'], 'COMPLETED')

    def test_stale_or_cross_context_sources_create_nothing(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.app()) as client:
                service = self.prepare(client)
                invalid = [dict(assessment_revision=2), dict(recommendation_revision=2), dict(recommendation_revision=None),
                    dict(recommendation_id=None), dict(assessment_id=None), dict(execution_ids=['missing']),
                    dict(execution_ids=[self.execution_record['execution_id']] * 2), dict(operation='retry'),
                    dict(player_context={**self.context.to_dict(), 'branch_id': 'other'})]
                for index, fields in enumerate(invalid):
                    body = {**self.body(), **fields, 'request_id': f'invalid-{index}'}
                    self.assertIn(client.post('/api/mission-continuations', json=body).status_code, [409, 422])
                self.add('correct-f1', '2026-09-04', {'rating': 8}, corrects_observation_id='f1')
                self.assertEqual(client.post('/api/mission-continuations', json=self.body()).status_code, 409)
                self.assertEqual(len(service.store.missions()), 1)
                self.assertEqual(client.get(self.base + '/followups', params={**self.context.to_dict(), 'branch_id': 'other'}).status_code, 409)

    def test_retry_is_original_task_after_correction_and_restart(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.app()) as client:
                service = self.prepare(client)
                with patch.object(service, '_enqueue'):
                    first = self.post(client)
                saved = service.frozen_feedback(first['mission_id'])
                self.add('correct-f1', '2026-09-04', {'rating': 8}, corrects_observation_id='f1')
                with patch.object(service, '_read_submission_snapshot', side_effect=AssertionError('Retry cannot read latest')):
                    again = self.post(client)
                self.assertEqual(first['mission_id'], again['mission_id'])
                self.assertFalse(again['created'])
                self.assertEqual(client.post('/api/mission-continuations', json={**self.body(), 'content': '改内容'}).status_code, 409)
            with TestClient(self.app()) as client:
                again = self.post(client)
                self.assertEqual(again['mission_id'], first['mission_id'])
                self.assertEqual(client.get(f'/api/missions/{first["mission_id"]}/feedback-input').json(), saved)
                self.assertEqual(client.get(f'/api/missions/{first["mission_id"]}').json()['status'], 'FAILED')

    def test_creation_failure_rolls_back_references_and_failed_child_preserves_parent(self):
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.app()) as client:
                service = self.prepare(client)
                original = service.store.get(self.identity).model_dump()
                report = service.store.report(self.identity)
                with service.store.db:
                    service.store.db.execute("CREATE TRIGGER fail_create BEFORE INSERT ON mission_inputs BEGIN SELECT RAISE(ABORT,'failure'); END")
                with patch.object(service, '_enqueue') as enqueue:
                    with self.assertRaises(sqlite3.IntegrityError):
                        service.create_continuation(ContinuationRequest(**self.body()))
                    enqueue.assert_not_called()
                self.assertEqual(len(service.store.missions()), 1)
                with service.store.db:
                    service.store.db.execute('DROP TRIGGER fail_create')
                with patch('backend.runtime.create_runtime', side_effect=RuntimeError('offline failure')), self.assertLogs(level='ERROR'):
                    child = self.post(client)['mission_id']
                    self.harness.wait(client, child, 'FAILED')
                self.assertEqual(service.store.get(self.identity).model_dump(), original)
                self.assertEqual(service.store.report(self.identity), report)
                self.assertTrue(service.frozen_feedback(child))

    def test_checkpoint_and_public_feedback_corruption_are_rejected(self):
        from backend.runtime import inspect_checkpoint
        from types import SimpleNamespace
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.app()) as client:
                service = self.prepare(client)
                with patch.object(service, '_enqueue'):
                    child = self.post(client)['mission_id']
                service._execute(child)
                self.harness.wait(client, child, 'BLOCKED')
                state = deepcopy(inspect_checkpoint(service.checkpoint_path, child).values)
                state['continuation_context'].pop('career_feedback')
                with patch('backend.runtime.inspect_checkpoint', return_value=SimpleNamespace(values=state, next=('document',))):
                    view = client.get(f'/api/missions/{child}').json()
                    self.assertNotIn('approve_report', view['available_operations'])
                mission = service.store.get(child)
                mission.lineage.feedback_reference['assessment_revision'] += 1
                service.store.save(mission)
                self.assertEqual(client.get(f'/api/missions/{child}/feedback-input').status_code, 409)

    def test_real_manager_coach_and_fingerprint_consume_fixed_feedback(self):
        from graph import build_graph
        from langgraph.checkpoint.sqlite import SqliteSaver
        from agents.manager import create_manager_node, create_manager_loop_nodes
        from agents.coach import create_coach_node
        from evaluation.test_execution_outcomes import Model
        from evaluation.test_looping_plan import task, complete_output
        from loop_contracts import normalise_review_result
        from report_validation import accept_document
        models = {}
        def runtime(path, on_start):
            mission = dict(objective='结果驱动训练调整', primary_goal='结果驱动训练调整', constraints=[], context={}, output_type='report', confidence=10)
            plan = dict(objective=mission['objective'], subtasks=[task('training')], information_gaps=[])
            models['manager'] = Model(json.dumps(mission), '[]', json.dumps(plan))
            models['coach'] = Model(json.dumps(complete_output('adjusted focus')))
            manager, checkpoint, instance = create_manager_node(models['manager'])
            revision, replan = create_manager_loop_nodes(instance)
            def reviewed(state):
                return {'review_v2': normalise_review_result(dict(decision='PASS', findings=[], reviewed_subtasks=['training']), state['subtasks']), 'review': {'decision': 'PASS'}}
            nodes = dict(manager=manager, intent_checkpoint=checkpoint, manager_revision=revision, manager_replan=replan,
                coach=create_coach_node(models['coach']), analyst=lambda _: {}, nutrition=lambda _: {}, career=lambda _: {},
                reviewer=reviewed, document=lambda state: accept_document(state, '# 调整报告\n结果驱动训练调整\n依据与限制：合成测试'))
            def wrap(name, fn):
                def run(state):
                    on_start(name, state)
                    return fn(state)
                run._telemetry_agent = getattr(fn, '_telemetry_agent', None)
                return run
            connection = sqlite3.connect(str(path), check_same_thread=False)
            return build_graph({key: wrap(key, value) for key, value in nodes.items()}, checkpointer=SqliteSaver(connection), interrupt_before=['document']), connection
        with patch('backend.runtime.create_runtime', side_effect=self.harness.runtime):
            with TestClient(self.app()) as client:
                service = self.prepare(client)
                with patch('backend.runtime.create_runtime', side_effect=runtime):
                    child = self.post(client)['mission_id']
                    self.harness.wait(client, child, 'BLOCKED')
                fixed = service.frozen_feedback(child)
                for model in models.values():
                    for call in model.prompts:
                        prompt = '\n'.join(str(message.content) for message in call)
                        for value in ('UNIQUE_EXECUTION', 'UNIQUE_REASON', 'UNIQUE_NEW_REQUEST', self.assessment['assessment_id'], fixed['content_hash']):
                            self.assertIn(value, prompt)
                from backend.runtime import inspect_checkpoint
                from execution_context import input_fingerprint
                state = deepcopy(inspect_checkpoint(service.checkpoint_path, child).values)
                fingerprint = input_fingerprint(state, state['plan']['subtasks'][0])
                state['continuation_context']['career_feedback']['assessment']['revision'] += 1
                self.assertNotEqual(input_fingerprint(state, state['plan']['subtasks'][0]), fingerprint)


if __name__ == '__main__':
    unittest.main()
