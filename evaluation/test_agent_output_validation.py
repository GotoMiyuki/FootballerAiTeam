import copy
import unittest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from agents.coach import CoachAgent
from output_validation import validate_specialist, validate_plan, validate_hypotheses
from execution_contracts import OutputError
from evaluation.test_execution_outcomes import Model
from evaluation.test_looping_plan import complete_output, task


@tool
def ObservationTool(value: int) -> str:
    """Return an observed value, deliberately shaped like business JSON."""
    return '{"focus_areas": ["tool-only"]}'


@tool
def SearchTool(query: str) -> str:
    """Optional retrieval that fails deterministically."""
    raise RuntimeError('private transport failure')


class OutputValidationTests(unittest.TestCase):
    def test_tool_budget_never_returns_tool_json(self):
        response = AIMessage(content='pending', tool_calls=[{'name': 'ObservationTool', 'args': {'value': 1}, 'id': 'call'}])
        agent = CoachAgent(Model(response, response))
        agent.tools = [ObservationTool]
        result = agent._run_react_loop('task', max_iterations=2)
        self.assertEqual(result.exit_reason, 'BUDGET_EXHAUSTED')
        self.assertEqual(result.text, '')
        self.assertEqual(len(result.observations), 2)
        self.assertEqual(result.tool_log[0]['call_id'], 'call')

    def test_unknown_arguments_execution_and_mixed_tools(self):
        for name, args, error in [('UpdatePlayerAttributeTool', {}, 'UNKNOWN_TOOL'),
                                  ('ObservationTool', {'value': 'bad'}, 'INVALID_ARGUMENTS')]:
            agent = CoachAgent(Model(AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': 'c'}]), 'answer'))
            agent.tools = [ObservationTool]
            result = agent._run_react_loop('task')
            self.assertEqual(result.exit_reason, 'TOOL_ERROR')
            self.assertEqual(result.tool_log[0]['error_type'], error)
        response = AIMessage(content='', tool_calls=[
            {'name': 'ObservationTool', 'args': {'value': 1}, 'id': 'ok'},
            {'name': 'SearchTool', 'args': {'query': 'x'}, 'id': 'fail'}])
        agent = CoachAgent(Model(response, AIMessage(content=[{'type': 'text', 'text': 'answer'}])))
        agent.tools = [ObservationTool, SearchTool]
        result = agent._run_react_loop('task')
        self.assertEqual(result.exit_reason, 'FINAL')
        self.assertEqual(result.text, 'answer')
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(result.tool_log[1]['error_type'], 'EXECUTION_ERROR')

    def test_empty_and_model_failure_exit_reasons(self):
        for answer, expected in [('', 'EMPTY_OUTPUT'), (RuntimeError(), 'MODEL_FAILED')]:
            self.assertEqual(CoachAgent(Model(answer))._run_react_loop('task').exit_reason, expected)

    def test_invalid_numeric_and_missing_schema(self):
        for value in (True, float('nan'), float('inf'), -1, '2000'):
            payload = complete_output('x')
            payload['daily_calories'] = value
            with self.assertRaises(OutputError):
                validate_specialist('nutrition_plan', payload)
        for payload in ({}, {'status': 'SUCCESS'}, {'weekly_schedule': []}):
            with self.assertRaises(OutputError):
                validate_specialist('skill_training', payload)

    def test_invalid_plan_dependencies_capabilities_and_ids(self):
        for tasks in ([task('x'), task('x')], [{**task('x'), 'depends_on': ['missing']}],
                      [{**task('a'), 'depends_on': ['b']}, {**task('b'), 'depends_on': ['a']}],
                      [task('x', 'unknown')], [task('x', status='completed')]):
            with self.assertRaises(OutputError):
                validate_plan({'subtasks': tasks}, {'output_type': 'report'})
        validate_plan({'subtasks': []}, {'output_type': 'statement'})
        with self.assertRaises(OutputError):
            validate_plan({'subtasks': []}, {'output_type': 'report'})

    def test_hypotheses_are_not_clamped_coerced_or_silently_dropped(self):
        hypothesis = dict(id='h1', statement='uncertain', confidence=0.5, status='open',
                          supporting_evidence=[], contradicting_evidence=[])
        for item in ({}, {**hypothesis, 'confidence': True}, {**hypothesis, 'confidence': 2},
                     {**hypothesis, 'confidence': float('nan')}, {**hypothesis, 'status': 'unknown'},
                     {**hypothesis, 'supporting_evidence': 'not a list'}):
            with self.assertRaises(OutputError):
                validate_hypotheses([item])
        validate_hypotheses([])


if __name__ == '__main__':
    unittest.main()
