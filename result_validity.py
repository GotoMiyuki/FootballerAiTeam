"""Current result projection and minimal downstream invalidation."""
from copy import deepcopy
from execution_contracts import unavailable_review, OutputError
from execution_context import input_fingerprint, task_dependencies


def clear_delivery(state):
    return {'review_v2': unavailable_review('成果已变化，等待重新审查', 'NOT_RUN'), 'review': {},
            'review_passed': False, 'reviewed_data': {}, 'review_findings': [], 'review_conflicts': [], 'review_gaps': [],
            'final_report': '', 'final_result': '', 'report_draft': '', 'body_validation': {},
            'delivery_status': 'NOT_GENERATED', 'synthesis_guide': {},
            'domain_outputs': {'Document': '__DELETE_KEY__'}}


def descendants(plan, identities):
    affected = set(identities)
    changed = True
    while changed:
        changed = False
        for task in plan.get('subtasks', []):
            if task['id'] not in affected and affected.intersection(task_dependencies(task)):
                affected.add(task['id'])
                changed = True
    return affected


def invalidate(state, identities, *, plan=None, reason='upstream_changed', runnable=True):
    plan = deepcopy(plan or state.get('plan') or state.get('plan_v2') or {})
    affected = descendants(plan, identities)
    results = deepcopy(state.get('subtask_results') or {})
    history = []
    for identity in affected:
        if identity in results:
            previous = results[identity]
            if previous.get('validity') == 'CURRENT':
                history.append({**deepcopy(previous), 'validity': 'INVALIDATED', 'invalidation_reason': reason})
            results[identity]['validity'] = 'INVALIDATED'
            results[identity]['invalidation_reason'] = reason
    for task in plan.get('subtasks', []):
        if task['id'] in affected:
            task['status'] = 'pending' if task['id'] in identities and runnable else 'invalidated'
    return {**clear_delivery(state), 'plan': plan, 'subtask_results': results, 'result_history': history}


def reconcile_results(state, *, plan=None):
    """Validate reuse from upstream down. Missing fingerprints are unverified."""
    plan = deepcopy(plan or state.get('plan') or state.get('plan_v2') or {})
    results = deepcopy(state.get('subtask_results') or {})
    tasks = plan.get('subtasks', [])
    by_id = {task['id']: task for task in tasks}
    removed = {identity for identity, result in results.items()
               if identity not in by_id and result.get('validity') == 'CURRENT'}
    visited, visiting = set(), set()
    invalid = set()
    def visit(identity):
        if identity in visited:
            return
        if identity in visiting or identity not in by_id:
            raise OutputError('Invalid dependency graph during reuse')
        visiting.add(identity)
        task = by_id[identity]
        for dependency in task_dependencies(task):
            visit(dependency)
        if str(task.get('status', '')).upper() == 'COMPLETED':
            result = results.get(identity, {})
            try:
                fingerprint = input_fingerprint({**state, 'plan': plan, 'subtask_results': results}, task)
                reusable = (result.get('validated') is True and result.get('validity') == 'CURRENT'
                            and result.get('status') == 'COMPLETED' and bool(result.get('input_fingerprint'))
                            and result['input_fingerprint'] == fingerprint)
            except (OutputError, ValueError):
                reusable = False
            if not reusable:
                invalid.add(identity)
                task['status'] = 'invalidated'
                if identity in results:
                    results[identity]['validity'] = 'INVALIDATED'
            else:
                result['reused_from_version'] = result['source_version']
        visiting.remove(identity)
        visited.add(identity)
    for task in tasks:
        visit(task['id'])
    if not invalid and not removed:
        return {'plan': plan, 'subtask_results': results}
    return invalidate(state, invalid | removed, plan=plan, reason='input_fingerprint_changed')
