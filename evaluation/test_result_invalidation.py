from copy import deepcopy
import unittest
from agents.manager import ManagerAgent
from execution_context import input_fingerprint
from execution_contracts import publishable
from result_validity import reconcile_results, invalidate
from report_validation import accept_document
from loop_contracts import normalise_review_result, build_v2_state_patch
from evaluation.test_looping_plan import state_for, task, completed_result, build_harness, invoke
from graph import merge_subtask_results, _ready_subtask


def chain_state():
    tasks = [task(name, status='completed') for name in ('A', 'B', 'C', 'D')]
    tasks[1]['depends_on'] = ['A']
    tasks[2]['depends_on'] = ['B']
    state = state_for(tasks, results={name: completed_result(name) for name in ('A', 'B', 'C', 'D')})
    state['review_v2'] = {'availability': 'COMPLETED', 'decision': 'PASS', 'findings': [],
                          'reviewed_subtasks': ['A', 'B', 'C', 'D'], 'reviewed_versions': {name: 1 for name in ('A', 'B', 'C', 'D')}}
    state.update(accept_document(state, '# previous report\nproduce a safe recommendation\n依据与限制：fixture'))
    return state


class ResultInvalidationTests(unittest.TestCase):
    def revision_review(self, state, target='A'):
        return normalise_review_result({'decision': 'REVISE', 'reviewed_subtasks': ['A', 'B', 'C', 'D'],
                                       'findings': [{'id': 'issue', 'subtask_ids': [target], 'severity': 'HIGH',
                                                     'action': 'REVISION', 'description': 'Changed upstream', 'evidence': ['new evidence']}]}, state['subtasks'])

    def test_revision_cascades_and_recomputes_only_A_B_C(self):
        initial = chain_state()
        reviews = []
        def reviewer(state):
            reviews.append(1)
            if len(reviews) == 1:
                return {'review_v2': self.revision_review(state)}
            return {'review_v2': {'decision': 'PASS', 'findings': []}}
        graph, calls = build_harness(reviewer)
        final, _ = invoke(graph, initial)
        self.assertEqual([identity for agent, identity in calls if agent != 'Document'], ['A', 'B', 'C'])
        for identity in ('A', 'B', 'C'):
            self.assertEqual(final['subtask_results'][identity]['source_version'], 2)
        self.assertEqual(final['subtask_results']['D']['source_version'], 1)
        self.assertEqual(final['review_v2']['reviewed_versions'], {'A': 2, 'B': 2, 'C': 2, 'D': 1})
        self.assertTrue(publishable(final))
        self.assertEqual(len(final['result_history']), 3)

    def test_revision_immediately_revokes_report_review_and_reducer_validity(self):
        initial = chain_state()
        initial['review_v2'] = self.revision_review(initial)
        patch = ManagerAgent(None).run_revision(initial)
        merged = {**initial, **patch}
        self.assertEqual(merged['final_report'], '')
        self.assertFalse(merged['review_passed'])
        self.assertEqual(merged['review_v2']['availability'], 'NOT_RUN')
        self.assertEqual(merged['plan']['subtasks'][3]['status'], 'completed')
        self.assertEqual(merged['plan']['subtasks'][1]['status'], 'invalidated')
        results = merge_subtask_results(initial['subtask_results'], patch['subtask_results'])
        self.assertEqual(results['A']['validity'], 'INVALIDATED')
        self.assertFalse(publishable(merged))

    def test_dependency_set_version_context_constraints_and_missing_fingerprint(self):
        for mutation, expected in (
                (lambda s: s['plan']['subtasks'][1].update(depends_on=['D']), {'B', 'C'}),
                (lambda s: s['subtask_results']['A'].update(source_version=2), {'B', 'C'}),
                (lambda s: s['plan']['subtasks'][1].update(constraints=['local restriction']), {'B', 'C'}),
                (lambda s: s['subtask_results']['A'].pop('input_fingerprint'), {'A', 'B', 'C'}),
                (lambda s: s['player_snapshot']['metadata'].update(state_version='new-version'), {'A', 'B', 'C', 'D'}),
                (lambda s: s['player_snapshot']['context'].update(branch_id='other'), {'A', 'B', 'C', 'D'})):
            initial = chain_state()
            mutation(initial)
            patch = reconcile_results(initial)
            actual = {t['id'] for t in patch['plan']['subtasks'] if t['status'] != 'completed'}
            self.assertEqual(actual, expected)
            self.assertEqual(patch['final_report'], '')

    def test_display_fields_and_constraint_order_do_not_invalidate(self):
        initial = chain_state()
        initial['mission']['tone'] = 'new display tone'
        initial['plan']['version'] = 99
        initial['plan']['subtasks'][0]['priority'] = 8
        patch = reconcile_results(initial)
        self.assertTrue(all(t['status'] == 'completed' for t in patch['plan']['subtasks']))

    def test_changed_used_hypothesis_invalidates_only_its_consumer(self):
        initial = chain_state()
        initial['hypotheses'] = [{'id': 'h', 'statement': 'used assumption', 'status': 'open', 'confidence': .8}]
        initial['plan']['subtasks'][1]['hypothesis_ids'] = ['h']
        initial['subtask_results']['B']['input_fingerprint'] = input_fingerprint(initial, initial['plan']['subtasks'][1])
        initial['hypotheses'][0]['status'] = 'rejected'
        patch = reconcile_results(initial)
        self.assertEqual({t['id'] for t in patch['plan']['subtasks'] if t['status'] != 'completed'}, {'B', 'C'})

    def test_budget_exhaustion_is_failed_not_old_completed_or_published(self):
        initial = chain_state()
        initial['review_v2'] = self.revision_review(initial)
        initial['plan']['subtasks'][0]['revision_count'] = 1
        patch = ManagerAgent(None).run_revision(initial)
        self.assertEqual(patch['execution_outcome'], 'FAILED')
        self.assertEqual(patch['final_report'], '')
        self.assertEqual(patch['subtask_results']['B']['validity'], 'INVALIDATED')
        self.assertEqual(patch['plan']['subtasks'][3]['status'], 'completed')

    def test_upstream_failure_revokes_downstream_and_current_review(self):
        initial = chain_state()
        initial['plan']['subtasks'][0]['status'] = 'failed'
        initial['subtask_results']['A']['validity'] = 'INVALID'
        patch = reconcile_results(initial)
        self.assertNotEqual(patch['plan']['subtasks'][1]['status'], 'completed')
        self.assertNotEqual(patch['plan']['subtasks'][2]['status'], 'completed')
        self.assertEqual(patch['plan']['subtasks'][3]['status'], 'completed')

    def test_removed_new_and_reused_agent_task_id_do_not_inherit_current_results(self):
        initial = chain_state()
        initial['plan']['subtasks'] = [initial['plan']['subtasks'][3], task('new', 'skill_training')]
        patch = reconcile_results(initial)
        self.assertEqual(patch['plan']['subtasks'][0]['status'], 'completed')

        self.assertEqual(patch['plan']['subtasks'][1]['status'], 'pending')
        self.assertEqual({row['subtask_id'] for row in patch['result_history']}, {'A', 'B', 'C'})
        self.assertTrue(all(patch['subtask_results'][identity]['validity'] == 'INVALIDATED' for identity in ('A', 'B', 'C')))
        initial = chain_state()
        initial['plan']['subtasks'][3]['goal'] = 'same id and agent, different task goal'
        patch = reconcile_results(initial)
        self.assertNotEqual(patch['plan']['subtasks'][3]['status'], 'completed')
        self.assertEqual(patch['plan']['subtasks'][0]['status'], 'completed')

    def test_scoped_confirmed_feedback_is_semantic_input(self):
        initial = chain_state()
        initial['mission']['context']['resolved_information_gaps'] = [
            {'questions': ['available time'], 'answer': '20 minutes', 'affected_subtasks': ['B']}]
        patch = reconcile_results(initial)
        self.assertEqual({row['id'] for row in patch['plan']['subtasks'] if row['status'] != 'completed'}, {'B', 'C'})


if __name__ == '__main__':
    unittest.main()
