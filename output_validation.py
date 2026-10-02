"""Deterministic shape validation, not scientific validation of recommendations."""
import json
import math
from execution_contracts import OutputError
from registry import get_available_capabilities

CONTRACT_VERSION = '1'
DOCUMENT_TYPES = {'report', 'plan', 'analysis', 'statement', 'advisory', 'response'}


def parse_json(text):
    from agents.base import model_text
    text = model_text(text).strip()
    if text.startswith('```') and text.endswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    return json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(OutputError('Nonfinite number')))


def finite_values(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise OutputError('Nonfinite number')
    if isinstance(value, dict):
        for item in value.values():
            finite_values(item)
    elif isinstance(value, list):
        for item in value:
            finite_values(item)


def require(value, key, kind, nonempty=True):
    item = value.get(key)
    if not isinstance(item, kind) or isinstance(item, bool) and kind != bool:
        raise OutputError('Invalid field: ' + key)
    if nonempty and isinstance(item, (str, list, dict)) and not item:
        raise OutputError('Empty field: ' + key)
    if isinstance(item, str) and nonempty and not item.strip():
        raise OutputError('Empty text')
    return item


def rows(value, key, fields, allow_empty=False):
    items = require(value, key, list, not allow_empty)
    for item in items:
        if not isinstance(item, dict):
            raise OutputError('Row is not an object')
        for field in fields:
            require(item, field, str)


def string_list(value, key, nonempty=False):
    items = require(value, key, list, nonempty)
    if not all(isinstance(item, str) and item.strip() for item in items):
        raise OutputError('Invalid text list: ' + key)
    return items


def validate_hypotheses(items):
    if not isinstance(items, list) or len(items) > 4:
        raise OutputError('Invalid hypothesis collection')
    ids = set()
    for item in items:
        if not isinstance(item, dict):
            raise OutputError('Invalid hypothesis')
        identity = require(item, 'id', str)
        if identity in ids:
            raise OutputError('Duplicate hypothesis id')
        ids.add(identity)
        require(item, 'statement', str)
        confidence = require(item, 'confidence', (int, float))
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise OutputError('Invalid hypothesis confidence')
        if item.get('status') not in {'open', 'supported', 'weakened', 'rejected'}:
            raise OutputError('Invalid hypothesis status')
        string_list(item, 'supporting_evidence')
        string_list(item, 'contradicting_evidence')
    return items


def schedule(payload):
    value = require(payload, 'weekly_schedule', (list, dict))
    if isinstance(value, dict):
        for day, activity in value.items():
            if not isinstance(day, str) or not day.strip():
                raise OutputError('Invalid schedule day')
            if isinstance(activity, str) and activity.strip():
                continue
            if isinstance(activity, list) and activity and all(isinstance(a, str) and a.strip() for a in activity):
                continue
            raise OutputError('Schedule activities must be text or a text list')
    else:
        for row in value:
            if not isinstance(row, dict):
                raise OutputError('Invalid schedule row')
            require(row, 'day', str)
            string_list(row, 'activities', True)


def validate_specialist(capability, payload):
    if not isinstance(payload, dict):
        raise OutputError('Expected object')
    finite_values(payload)
    status = payload.get('status')
    if status in {'FAILED', 'NO_RESULT'}:
        require(payload, 'reason', str)
        return payload
    if status == 'BLOCKED':
        require(payload, 'blocked_reason', str)
        return payload
    if status == 'PARTIAL':
        # No capability currently authorizes partial observations as a full result.
        require(payload, 'observations', list)
        require(payload, 'limitations', list)
        return payload
    if status is not None and status not in {'COMPLETED', 'SUCCEEDED'}:
        raise OutputError('Unknown status')
    if capability == 'skill_training':
        string_list(payload, 'focus_areas', True)
        schedule(payload)
        rows(payload, 'drill_details', ['name', 'frequency', 'description'])
        for drill in payload['drill_details']:
            sets = require(drill, 'sets', (str, int))
            if isinstance(sets, int) and sets <= 0:
                raise OutputError('Invalid drill repetitions')
        require(payload, 'imbalance_notes', (list, str), False)
        require(payload, 'attribute_update_suggestions', dict, False)
        require(payload, 'notes', (str, list), False)
    elif capability == 'nutrition_plan':
        for key in ('daily_calories', 'carbs_g', 'protein_g', 'fat_g', 'bmi', 'bmr_kcal', 'tdee_kcal'):
            number = require(payload, key, (int, float))
            if number < 0 or key in {'daily_calories', 'bmi', 'bmr_kcal', 'tdee_kcal'} and number == 0:
                raise OutputError('Invalid nutrition quantity')
        rows(payload, 'meal_plan', ['meal', 'time', 'food'])
        string_list(payload, 'supplements')
        require(payload, 'hydration_plan', str)
    elif capability == 'performance_analysis':
        require(payload, 'period', str)
        string_list(payload, 'data_sources', True)
        rows(payload, 'trends', ['attribute', 'change', 'status', 'risk'], True)
        rows(payload, 'cross_category_findings', ['type', 'detail', 'severity'], True)
        risk = require(payload, 'injury_risk', dict)
        require(risk, 'level', str)
        score = risk.get('score')
        if score is not None and (isinstance(score, bool) or not isinstance(score, (int, float))):
            raise OutputError('Invalid risk score')
        require(risk, 'factors', list, False)
        require(risk, 'detail', (str, dict), False)
        require(payload, 'form_assessment', str)
        require(payload, 'recommendations', list, False)
        require(payload, 'summary', str)
    elif capability == 'career_planning':
        require(payload, 'current_status', dict)
        rows(payload, 'career_paths', ['direction', 'description', 'timeline'])
        for path in payload['career_paths']:
            for key in ('pros', 'cons'):
                require(path, key, (str, list))
        require(payload, 'marginal_value_analysis', (dict, list), False)
        require(payload, 'recommendations', list)
        require(payload, 'risks', list, False)
    elif capability == 'transfer_analysis':
        require(payload, 'current_status', dict)
        rows(payload, 'target_clubs', ['name', 'tactical_fit', 'league_environment', 'growth_potential', 'feasibility'])
        require(payload, 'market_valuation', (dict, str))
        require(payload, 'recommendations', list)
    else:
        raise OutputError('Unknown capability')
    return payload


def validate_plan(plan, mission):
    if not isinstance(plan, dict):
        raise OutputError('Invalid Plan')
    tasks = require(plan, 'subtasks', list, False)
    if not tasks and mission.get('output_type') not in {'statement', 'advisory', 'response'}:
        raise OutputError('Empty specialist plan requires an explicit document-only deliverable')
    ids = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise OutputError('Invalid task')
        identity = require(task, 'id', str)
        if identity in ids:
            raise OutputError('Duplicate task id')
        ids.add(identity)
        require(task, 'goal' if 'goal' in task else 'objective', str)
        if task.get('capability') not in get_available_capabilities():
            raise OutputError('Unknown capability')
        if task.get('status', 'pending') not in {'pending', 'blocked'}:
            raise OutputError('Planner may not certify completion or skip required work')
        dependencies = task.get('depends_on', task.get('dependencies', []))
        if not isinstance(dependencies, list) or not all(isinstance(d, str) for d in dependencies):
            raise OutputError('Invalid dependencies')
        if len(dependencies) != len(set(dependencies)):
            raise OutputError('Repeated dependency')
        if 'priority' in task and (isinstance(task['priority'], bool) or not isinstance(task['priority'], int) or task['priority'] < 1):
            raise OutputError('Invalid task priority')
    if len(tasks) > 8:
        raise OutputError('Plan exceeds supported task budget')
    visiting, visited = set(), set()
    by_id = {task['id']: task for task in tasks}
    def visit(identity):
        if identity in visiting or identity not in by_id:
            raise OutputError('Cyclic or unknown dependency')
        if identity in visited:
            return
        visiting.add(identity)
        for dep in by_id[identity].get('depends_on', by_id[identity].get('dependencies', [])):
            visit(dep)
        visiting.remove(identity)
        visited.add(identity)
    for identity in ids:
        visit(identity)
    return plan


def validate_review_shape(review, tasks):
    if not isinstance(review, dict) or review.get('decision') not in {'PASS', 'REVISE', 'REPLAN', 'BLOCKED'}:
        raise OutputError('Invalid review decision')
    ids = {t['id'] for t in tasks}
    covered = require(review, 'reviewed_subtasks', list, False)
    if any(not isinstance(i, str) or i not in ids for i in covered):
        raise OutputError('Invalid review coverage')
    if review['decision'] == 'PASS' and set(covered) != ids:
        raise OutputError('Incomplete review coverage')
    for finding in require(review, 'findings', list, False):
        if not isinstance(finding, dict):
            raise OutputError('Invalid finding')
        severity = require(finding, 'severity', str).upper()
        action = require(finding, 'action', str).upper()
        if severity not in {'INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'} or action not in {'KEEP', 'REVISION', 'REPLAN', 'BLOCKED'}:
            raise OutputError('Unknown finding semantics')
        scope = require(finding, 'subtask_ids', list, False)
        if any(i not in ids for i in scope) or severity not in {'INFO', 'LOW'} and not scope:
            raise OutputError('Unscoped material finding')
        require(finding, 'description', str)
        evidence = require(finding, 'evidence', list, False)
        if severity in {'MEDIUM', 'HIGH', 'CRITICAL'} and action == 'KEEP':
            raise OutputError('Material issue cannot authorize PASS')
        if action == 'REPLAN' and (not evidence or not finding.get('reason') or severity not in {'HIGH', 'CRITICAL'}):
            raise OutputError('Replan requires core evidence')
    if review['decision'] in {'REVISE', 'REPLAN'} and not review['findings']:
        raise OutputError('Review loop requires findings')
    return review
