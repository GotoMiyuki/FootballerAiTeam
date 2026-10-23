"""Application-owned execution and delivery contracts (model labels are data)."""
from copy import deepcopy
from functools import wraps
import json


class OutputError(ValueError):
    pass


class MissingInput(ValueError):
    pass


class ReactError(OutputError):
    def __init__(self, result):
        self.result = result


def guarded(agent):
    """Translate real node failures without fabricating a business payload."""
    def decorate(fn):
        @wraps(fn)
        def run(self, state):
            self._valid_observations = []
            try:
                from player_data.repository import snapshot_scope
                with snapshot_scope(state.get('player_snapshot')):
                    return fn(self, state)
            except ReactError as exc:
                result = failure_patch(agent, state, 'FAILED' if exc.result.exit_reason == 'MODEL_FAILED' else 'NO_RESULT',
                                       exc.result.exit_reason, '工具或模型未形成最终结果', exc.result.observations)
                result['tool_call_log'] = exc.result.tool_log
                return result
            except MissingInput as exc:
                return failure_patch(agent, state, 'BLOCKED', 'missing_input', str(exc))
            except (OutputError, json.JSONDecodeError):
                return failure_patch(agent, state, 'NO_RESULT', 'invalid_output', '未输出有效结果', self._valid_observations)
            except Exception:
                return failure_patch(agent, state, 'FAILED', 'execution_error', '执行失败，未输出结果', self._valid_observations)
        return run
    return decorate


def unavailable_review(reason='审查未完成', availability='UNAVAILABLE'):
    return {'availability': availability, 'decision': None, 'findings': [],
            'reviewed_subtasks': [], 'reviewed_versions': {}, 'scope': 'specialist_inputs',
            'blocking_information': [], 'summary': reason}


def failure_patch(agent, state, outcome='NO_RESULT', error_type='invalid_output', reason='未输出有效结果', observations=None):
    return {'execution_outcome': outcome, 'error_type': error_type, 'failure_reason': reason,
            'domain_outputs': {agent: {'status': outcome, 'error_type': error_type,
                                     'reason': reason, 'blocked_reason': reason if outcome == 'BLOCKED' else '',
                                     'observations': deepcopy(observations or [])}},
            'iteration': state.get('iteration', 0) + 1,
            'final_report': '', 'final_result': '', 'delivery_status': 'NOT_GENERATED'}


def review_passed(state):
    review = state.get('review_v2') or {}
    return review.get('availability') == 'COMPLETED' and review.get('decision') == 'PASS'


def publishable(state):
    from report_validation import digest, current_review
    body = state.get('body_validation') or {}
    return (state.get('execution_outcome') == 'SUCCEEDED'
            and state.get('delivery_status') == 'PUBLISHABLE'
            and current_review(state) and bool(state.get('final_report'))
            and body.get('status') == 'PASSED'
            and body.get('content_hash') == digest(state['final_report'])
            and body.get('reviewed_versions') == state['review_v2'].get('reviewed_versions'))
