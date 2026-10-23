"""Explicit subtask inputs, canonical fingerprints and dependency validity."""
from copy import deepcopy
import json
from execution_contracts import OutputError
from output_validation import CONTRACT_VERSION
from player_data.models import content_hash

PAYLOAD_FIELDS = {
    'skill_training': {'focus_areas', 'weekly_schedule', 'drill_details', 'imbalance_notes', 'attribute_update_suggestions', 'notes', 'references'},
    'nutrition_plan': {'daily_calories', 'carbs_g', 'protein_g', 'fat_g', 'bmi', 'bmr_kcal', 'tdee_kcal', 'meal_plan', 'supplements', 'hydration_plan'},
    'performance_analysis': {'period', 'data_sources', 'trends', 'cross_category_findings', 'injury_risk', 'form_assessment', 'recommendations', 'summary'},
    'career_planning': {'current_status', 'career_paths', 'marginal_value_analysis', 'recommendations', 'risks'},
    'transfer_analysis': {'current_status', 'target_clubs', 'market_valuation', 'recommendations'},
}


def task_dependencies(task):
    return task.get('depends_on', task.get('dependencies', []))


def valid_result(state, identity, *, check_fingerprint=False, _visiting=None):
    task = next((t for t in (state.get('plan') or state.get('plan_v2') or {}).get('subtasks', []) if t.get('id') == identity), None)
    result = (state.get('subtask_results') or {}).get(identity, {})
    valid = (task is not None and str(task.get('status', '')).upper() == 'COMPLETED'
             and result.get('status') == 'COMPLETED' and result.get('validated') is True
             and result.get('validity') == 'CURRENT' and bool(result.get('observation')))
    if valid and check_fingerprint:
        visiting = set(_visiting or ())
        if identity in visiting:
            return False
        visiting.add(identity)
        try:
            valid = bool(result.get('input_fingerprint')) and result['input_fingerprint'] == input_fingerprint(state, task, _visiting=visiting)
        except (OutputError, ValueError):
            return False
    return valid


def relevant_constraints(state, task):
    mission = state.get('mission') or {}
    plan_constraints = (state.get('plan') or {}).get('constraints', [])
    return sorted(set(str(value) for value in list(mission.get('constraints') or [])
                      + list(plan_constraints or []) + list(task.get('constraints') or [])))


def confirmed_inputs(state, task):
    inputs = deepcopy(task.get('confirmed_inputs') or [])
    resolved = ((state.get('mission') or {}).get('context') or {}).get('resolved_information_gaps') or []
    for record in resolved:
        if task['id'] in record.get('affected_subtasks', []):
            inputs.append({'questions': deepcopy(record.get('questions') or []), 'answer': record.get('answer')})
    return inputs


def used_hypotheses(state, task):
    ids = set(task.get('hypothesis_ids') or task.get('hypotheses') or [])
    fields = {'id', 'statement', 'confidence', 'status', 'supporting_evidence', 'contradicting_evidence'}
    return sorted(({k: deepcopy(v) for k, v in h.items() if k in fields} for h in state.get('hypotheses', []) if h.get('id') in ids), key=lambda h: h['id'])


def input_material(state, task, *, _visiting=None):
    snapshot = state.get('player_snapshot') or {}
    metadata = snapshot.get('metadata') or {}
    versions = {}
    for dependency in sorted(task_dependencies(task)):
        if not valid_result(state, dependency, check_fingerprint=True, _visiting=_visiting):
            raise OutputError('Missing or invalid dependency result: ' + dependency)
        versions[dependency] = state['subtask_results'][dependency]['source_version']
    # Plan version, priority, display tone and read time are not semantic inputs.
    material = {'contract_version': CONTRACT_VERSION, 'capability': task.get('capability'),
            'goal': task.get('goal') or task.get('objective'), 'purpose': task.get('purpose'),
            'mission_objective': (state.get('mission') or {}).get('objective'),
            'constraints': relevant_constraints(state, task), 'used_hypotheses': used_hypotheses(state, task),
            'confirmed_inputs': confirmed_inputs(state, task),
            'player_context': snapshot.get('context'), 'player_state_version': metadata.get('state_version'),
            'player_snapshot_id': metadata.get('snapshot_id'), 'dependency_versions': versions}
    if state.get('continuation_context'):
        material['continuation_context'] = deepcopy(state['continuation_context'])
    return material


def input_fingerprint(state, task, *, _visiting=None):
    return content_hash(input_material(state, task, _visiting=_visiting))


def build_execution_context(state, task):
    material = input_material(state, task)
    by_id = {t['id']: t for t in (state.get('plan') or {}).get('subtasks', [])}
    dependencies = {}
    for identity in task_dependencies(task):
        result = state['subtask_results'][identity]
        upstream = by_id[identity]
        payload = (result.get('observation') or {}).get('result')
        if not isinstance(payload, dict):
            raise OutputError('Dependency has no validated domain payload')
        projection = {k: deepcopy(v) for k, v in payload.items() if k in PAYLOAD_FIELDS[upstream['capability']]}
        dependencies[identity] = {'subtask_id': identity, 'capability': upstream['capability'],
            'source_version': result['source_version'], 'payload': projection,
            'evidence': deepcopy(result.get('evidence') or []), 'limitations': deepcopy(result.get('uncertainties') or []),
            'input_reference': deepcopy(result.get('input_reference') or {})}
    context = {'mission_id': state.get('mission_id') or (state.get('mission') or {}).get('mission_id'),
               'subtask_id': task['id'], 'plan_version': (state.get('plan') or {}).get('version'),
               'capability': task['capability'], 'goal': task.get('goal') or task.get('objective'),
               'constraints': material['constraints'], 'used_hypotheses': material['used_hypotheses'],
               'confirmed_inputs': material['confirmed_inputs'], 'player_input': {
                   key: deepcopy(material[key]) for key in ('player_context', 'player_state_version', 'player_snapshot_id')},
               'dependencies': dependencies, 'phase': 'revision' if task['id'] in (state.get('revision_contexts') or {}) else 'initial'}
    if material.get('continuation_context'):
        context['continuation_context'] = deepcopy(material['continuation_context'])
    if len(json.dumps(context, ensure_ascii=False)) > 64000:
        # Fail explicitly rather than silently truncating the decisive evidence.
        raise OutputError('Dependency context exceeds the supported evidence budget')
    return context
