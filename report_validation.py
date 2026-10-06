"""Final prose gate: structure, declared requirements and checkable references.

Does not certify scientific quality or the semantics of all prose sentences.
"""
import hashlib
import re
from execution_contracts import OutputError, review_passed

MODES = {'comprehensive_report': '#', 'pr_statement': '【对外发布稿】',
         'commercial_advisory': '【商业评估报告】', 'media_response': '【媒体应答手册】'}


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def current_review(state):
    if not review_passed(state) or not (state.get('plan') or state.get('plan_v2')):
        return False
    review = state['review_v2']
    results = state.get('subtask_results') or {}
    tasks = (state.get('plan') or state.get('plan_v2') or {}).get('subtasks', [])
    from execution_context import valid_result
    return all(valid_result(state, task['id'], check_fingerprint=True)
               and results.get(task['id'], {}).get('validated') is True
               and results[task['id']].get('validity') == 'CURRENT'
               and review.get('reviewed_versions', {}).get(task['id']) == results[task['id']].get('source_version')
               for task in tasks)


def validate_body(state, text, mode):
    if not current_review(state):
        raise OutputError('Stale or incomplete review')
    if not isinstance(text, str) or not text.strip() or mode not in MODES or MODES[mode] not in text:
        raise OutputError('Wrong or empty document mode')
    if mode == 'comprehensive_report' and not text.lstrip().startswith('#'):
        raise OutputError('Report requires a Markdown heading')
    if '依据与限制' not in text:
        raise OutputError('Evidence/limitations section is required')
    mission = state.get('mission') or {}
    if mode == 'comprehensive_report':
        objective = mission.get('primary_goal') or mission.get('objective')
        if objective and objective not in text:
            raise OutputError('Required objective is missing')
    for phrase in mission.get('required_phrases', []):
        if phrase not in text:
            raise OutputError('Explicit deliverable requirement missing')
    # Check explicit current-fact statements. Prediction-labelled sentences are
    # allowed, but are never imported as player facts.
    profile = (state.get('player_snapshot') or {}).get('profile') or state.get('player_profile') or {}
    labels = {'age': '年龄', 'overall': '综合评分|当前评分', 'height': '身高', 'weight': '体重'}
    for field, label in labels.items():
        for match in re.finditer(r'(?:' + label + r')\s*[:：为]?\s*(\d+(?:\.\d+)?)', text):
            expected = profile.get(field)
            if expected is None or float(match.group(1)) != expected:
                raise OutputError('Contradictory current player fact')
    allowed_urls = set()
    for task in (state.get('plan') or {}).get('subtasks', []):
        result = (state.get('subtask_results') or {}).get(task['id'], {})
        for item in result.get('evidence', []):
            if isinstance(item, dict):
                allowed_urls.update(item[k] for k in ('url', 'source') if isinstance(item.get(k), str))
    for url in re.findall(r'https?://[^\s)\]>]+', text):
        if url not in allowed_urls:
            raise OutputError('Unverified citation URL')
    return {'status': 'PASSED', 'content_hash': digest(text), 'mode': mode,
            'scope': ['structure', 'explicit_requirements', 'labelled_player_values', 'citation_urls'],
            'semantic_review': 'NOT_PERFORMED',
            'reviewed_versions': dict(state['review_v2'].get('reviewed_versions') or {})}


def accept_document(state, text, mode='comprehensive_report'):
    validation = validate_body(state, text, mode)
    return {'report_draft': text, 'final_report': text, 'final_result': text,
            'body_validation': validation, 'delivery_status': 'PUBLISHABLE', 'execution_outcome': 'SUCCEEDED'}
