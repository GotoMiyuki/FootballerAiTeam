"""Real Agents and graph boundaries with deterministic offline model responses."""
import json
import unittest
from unittest.mock import patch
from langchain_core.messages import AIMessage
from agents.coach import CoachAgent
from agents.nutrition import NutritionAgent
from agents.analyst import AnalystAgent
from agents.career import CareerAgent
from agents.manager import ManagerAgent
from agents.document import DocumentAgent
from agents.reviewer import ReviewerAgent
from execution_contracts import publishable
from loop_contracts import normalise_review_result, migrate_review
from evaluation.test_looping_plan import complete_output, state_for, task, build_harness, invoke


class Model:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []
    def bind_tools(self, tools):
        return self
    def invoke(self, messages):
        self.prompts.append(messages)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, AIMessage) else AIMessage(content=answer)


def reviewed_state():
    state = state_for([])
    state['review_v2'] = {'availability': 'COMPLETED', 'decision': 'PASS', 'findings': [],
                          'reviewed_subtasks': [], 'reviewed_versions': {}}
    state['player_profile'] = {'name': 'fixture', 'overall': 80, 'age': 22}
    return state


class ExecutionOutcomeTests(unittest.TestCase):
    def test_review_failures_and_old_records_never_pass(self):
        for payload in ({}, {'decision': 'unknown'}, {'status': 'failed'}, {'status': 'passed'}):
            for adapter in (normalise_review_result, migrate_review):
                result = adapter(payload, [])
                self.assertEqual(result['availability'], 'UNAVAILABLE')
                self.assertIsNone(result['decision'])
        for answer in (RuntimeError('private detail'), '', 'invalid', '{}'):
            result = ReviewerAgent(Model(answer)).run(reviewed_state())
            self.assertFalse(result['review_passed'])
            self.assertIsNone(result['review_v2']['decision'])
            self.assertEqual(result['reviewed_data'], {})

    def test_all_specialist_capabilities_fail_without_default_answers(self):
        for cls, capability in ((CoachAgent, 'skill_training'), (NutritionAgent, 'nutrition_plan'),
                                (AnalystAgent, 'performance_analysis'), (CareerAgent, 'career_planning'),
                                (CareerAgent, 'transfer_analysis')):
            for answer in ('not-json', '{}', '', RuntimeError('provider failed')):
                with self.subTest(capability=capability, answer=type(answer).__name__):
                    model = Model(*(['["Target"]', answer] if capability == 'transfer_analysis' else [answer]))
                    state = state_for([task('t', capability)])
                    state['current_subtask'] = 't'
                    result = cls(model).run(state)
                    self.assertIn(result['execution_outcome'], {'FAILED', 'NO_RESULT'})
                    self.assertFalse(result['final_report'])
                    self.assertNotIn('weekly_schedule', result['domain_outputs'][cls.__name__.replace('Agent', '')])
                    self.assertNotIn('private detail', str(result))

    def test_valid_specialist_schema_paths(self):
        for cls, capability in ((CoachAgent, 'skill_training'), (NutritionAgent, 'nutrition_plan'),
                                (AnalystAgent, 'performance_analysis'), (CareerAgent, 'career_planning')):
            state = state_for([task('t', capability)])
            state['current_subtask'] = 't'
            result = cls(Model(json.dumps(complete_output('t')))).run(state)
            self.assertNotIn('failure_reason', result)

    def test_document_modes_fail_and_normal_delivery_is_version_checked(self):
        modes = {'report': '# Report', 'statement': '【对外发布稿】',
                 'advisory': '【商业评估报告】', 'response': '【媒体应答手册】'}
        for kind, marker in modes.items():
            for answer in ('', RuntimeError('failure')):
                state = reviewed_state()
                state['mission']['output_type'] = kind
                result = DocumentAgent(Model(answer)).run(state)
                self.assertFalse(result['final_report'])
                self.assertNotEqual(result['delivery_status'], 'PUBLISHABLE')
            state = reviewed_state()
            state['mission']['output_type'] = kind
            text = marker + '\n' + state['mission']['primary_goal'] + '\n依据与限制：测试来源'
            result = DocumentAgent(Model(text)).run(state)
            self.assertTrue(publishable({**state, **result}))
            result['final_report'] += '\nchanged'
            self.assertFalse(publishable({**state, **result}))

    def test_body_rejects_wrong_values_citations_and_stale_report(self):
        for text in ('# R\nproduce a safe recommendation\n依据与限制：综合评分：99',
                     '# R\nproduce a safe recommendation\n依据与限制：https://invented.invalid'):
            result = DocumentAgent(Model(text)).run(reviewed_state())
            self.assertFalse(result['final_report'])

    def test_planning_failure_never_becomes_document_only_success(self):
        result = ManagerAgent(Model('invalid')).run(state_for([]))
        self.assertEqual(result['execution_outcome'], 'NO_RESULT')
        self.assertFalse(result['final_report'])

    def test_graph_rejects_self_reported_success_without_schema(self):
        graph, calls = build_harness(lambda state: {'review_v2': {'decision': 'PASS', 'findings': []}},
                                     outputs={'t': {'status': 'COMPLETED', 'result': 'fake success'}})
        result, _ = invoke(graph, state_for([task('t')]))
        self.assertEqual(result['subtask_results']['t']['status'], 'FAILED')
        self.assertFalse(result['final_report'])
        self.assertNotIn(('Document', 'final'), calls)


if __name__ == '__main__':
    unittest.main()
