import json
import unittest
from agents.coach import CoachAgent
from agents.nutrition import NutritionAgent
from agents.analyst import AnalystAgent
from agents.career import CareerAgent
from execution_context import build_execution_context
from execution_contracts import OutputError
from evaluation.test_execution_outcomes import Model
from evaluation.test_looping_plan import complete_output, completed_result, state_for, task
from graph import _ready_subtask


class DependencyContextTests(unittest.TestCase):
    def state(self, capability):
        upstream = task('upstream', 'performance_analysis', 'completed')
        dependent = task('dependent', capability)
        dependent['depends_on'] = ['upstream']
        result = completed_result('upstream', 7)
        result['observation']['result'] = {**complete_output('upstream'), 'summary': 'UNIQUE_UPSTREAM_EVIDENCE_714'}
        state = state_for([upstream, dependent], results={'upstream': result})
        state['current_subtask'] = 'dependent'
        state['domain_outputs']['Coach'] = 'UNRELATED_OLD_AGENT_RESULT'
        state['observations'] = [{'subtask_id': 'deleted', 'result': 'UNRELATED_HISTORY'}]
        return state

    def test_all_capabilities_receive_unique_dependency_in_initial_and_revision_prompts(self):
        for cls, capability in ((CoachAgent, 'skill_training'), (NutritionAgent, 'nutrition_plan'),
                                (AnalystAgent, 'performance_analysis'), (CareerAgent, 'career_planning'),
                                (CareerAgent, 'transfer_analysis')):
            for revision in (False, True):
                state = self.state(capability)
                if revision:
                    state['revision_contexts'] = {'dependent': {'reviewer_findings': [{'description': 'fix locally'}]}}
                payload = complete_output('dependent')
                payload['target_clubs'] = [{'name': 'Target', 'tactical_fit': 'fit', 'league_environment': 'league', 'growth_potential': 'growth', 'feasibility': 'conditional'}]
                payload['market_valuation'] = 'estimate'
                payload['notes'] = ['UNIQUE_UPSTREAM_EVIDENCE_714']
                model = Model(*(['["Target"]', json.dumps(payload)] if capability == 'transfer_analysis' else [json.dumps(payload)]))
                result = cls(model).run(state)
                self.assertNotIn('failure_reason', result, capability)
                prompt = '\n'.join(str(m.content) for m in model.prompts[-1])
                self.assertIn('UNIQUE_UPSTREAM_EVIDENCE_714', prompt)
                self.assertIn('"source_version": 7', prompt)
                self.assertIn('"phase": "revision"' if revision else '"phase": "initial"', prompt)
                self.assertNotIn('UNRELATED_OLD_AGENT_RESULT', prompt)
                self.assertNotIn('UNRELATED_HISTORY', prompt)
                self.assertIn('UNIQUE_UPSTREAM_EVIDENCE_714', str(result))

    def test_completed_label_without_valid_result_is_not_ready(self):
        for invalid in ('missing', 'unvalidated', 'invalidated'):
            state = self.state('skill_training')
            if invalid == 'missing':
                state['subtask_results'] = {}
            elif invalid == 'unvalidated':
                state['subtask_results']['upstream']['validated'] = False
            else:
                state['subtask_results']['upstream']['validity'] = 'INVALIDATED'
            self.assertIsNone(_ready_subtask(state))
            with self.assertRaises(OutputError):
                build_execution_context(state, state['plan']['subtasks'][1])

    def test_context_is_copied_and_identity_and_versions_come_from_A(self):
        state = self.state('skill_training')
        context = build_execution_context(state, state['plan']['subtasks'][1])
        self.assertEqual(context['player_input']['player_snapshot_id'], state['player_snapshot']['metadata']['snapshot_id'])
        context['dependencies']['upstream']['payload']['summary'] = 'caller mutated'
        self.assertEqual(state['subtask_results']['upstream']['observation']['result']['summary'], 'UNIQUE_UPSTREAM_EVIDENCE_714')

    def test_stale_ancestor_rejects_direct_context_even_before_reconciliation(self):
        upstream = task('ancestor', 'performance_analysis', 'completed')
        middle = task('middle', 'skill_training', 'completed')
        middle['depends_on'] = ['ancestor']
        dependent = task('dependent', 'nutrition_plan')
        dependent['depends_on'] = ['middle']
        state = state_for([upstream, middle, dependent], results={
            'ancestor': completed_result('ancestor'), 'middle': completed_result('middle')})
        build_execution_context(state, dependent)
        upstream['goal'] = 'changed semantic input; same result version and completed label'
        with self.assertRaises(OutputError):
            build_execution_context(state, dependent)
        self.assertIsNone(_ready_subtask(state))

    def test_corrupt_dependency_cycle_fails_closed(self):
        state = self.state('skill_training')
        state['plan']['subtasks'][0]['depends_on'] = ['upstream']
        with self.assertRaises(OutputError):
            build_execution_context(state, state['plan']['subtasks'][1])


if __name__ == '__main__':
    unittest.main()
