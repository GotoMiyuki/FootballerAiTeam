"""Content-free, durable per-operation observations and conservative usage totals."""
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field


class AttemptMeter(BaseModel):
    status: Literal['STARTED', 'RESPONDED', 'FAILED', 'INTERRUPTED'] = 'STARTED'
    status_code: int | None = None


class CallMeter(BaseModel):
    run_id: int
    kind: Literal['model', 'tool']
    agent: str = ''
    purpose: str = ''
    status: Literal['RUNNING', 'SUCCEEDED', 'FAILED', 'INTERRUPTED'] = 'RUNNING'
    duration_seconds: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    attempts_observed: bool = False
    attempts: dict[str, AttemptMeter] = Field(default_factory=dict)


class OperationMeter(BaseModel):
    version: Literal[1] = 1
    mode: Literal['live', 'demo'] = 'live'
    history_complete: bool = True
    finished_runs: list[int] = Field(default_factory=list)
    calls: dict[str, CallMeter] = Field(default_factory=dict)


METRIC_EVENTS = {'span_start', 'span_end', 'model_attempt', 'worker_finished'}


def record(meter, run_id, event):
    """Only internal worker metadata reaches this function; repeats are idempotent."""
    kind = event['type']
    if kind == 'worker_finished':
        if run_id in meter.finished_runs:
            return False
        meter.finished_runs.append(run_id)
        return True
    if run_id in meter.finished_runs:
        return False
    identity = event['span_id']
    if kind == 'span_start':
        if identity in meter.calls:
            return False
        meter.calls[identity] = CallMeter(run_id=run_id, kind=event['kind'],
            agent=event.get('agent', ''), purpose=event.get('purpose', ''))
        return True
    call = meter.calls.get(identity)
    if call is None or call.run_id != run_id or call.status != 'RUNNING':
        return False
    if kind == 'model_attempt':
        key = str(event['attempt'])
        call.attempts_observed = True
        if event['phase'] == 'start':
            if key in call.attempts:
                return False
            call.attempts[key] = AttemptMeter()
        else:
            attempt = call.attempts.get(key)
            if attempt is None or attempt.status != 'STARTED':
                return False
            attempt.status, attempt.status_code = 'RESPONDED', event['status_code']
        return True
    call.status = event['status']
    call.duration_seconds = event['duration_seconds']
    call.input_tokens, call.output_tokens = event.get('input_tokens'), event.get('output_tokens')
    for attempt in call.attempts.values():
        if attempt.status == 'STARTED':
            attempt.status = 'FAILED'
    return True


def finish_run(meter, run_id, *, observed_starts=None, monotonic_now=None):
    if meter.mode == 'demo' and run_id not in meter.finished_runs:
        meter.finished_runs.append(run_id)
    if run_id not in meter.finished_runs:
        meter.history_complete = False
    for identity, call in meter.calls.items():
        if call.run_id == run_id and call.status == 'RUNNING':
            call.status = 'INTERRUPTED'
            started = (observed_starts or {}).get(identity)
            if started is not None and monotonic_now is not None:
                call.duration_seconds = max(0, monotonic_now - started)
            for attempt in call.attempts.values():
                if attempt.status == 'STARTED':
                    attempt.status = 'INTERRUPTED'


def meter_summary(operation):
    meter = operation.metering
    if meter is None:
        return {'available': False, 'logical_calls': None, 'network_attempts': None,
            'known_network_attempts': 0, 'known_input_tokens': 0, 'known_output_tokens': 0,
            'input_tokens': None, 'output_tokens': None, 'usage_known_calls': 0,
            'usage_unknown_calls': None, 'unknown_attempts': None, 'complete': False}
    models = [c for c in meter.calls.values() if c.kind == 'model']
    tools = [c for c in meter.calls.values() if c.kind == 'tool']
    closed = operation.run_id in meter.finished_runs
    complete = meter.history_complete and closed
    attempts = [a for c in models for a in c.attempts.values()]
    observed = all(c.attempts_observed for c in models)
    known_in = sum(c.input_tokens for c in models if c.input_tokens is not None)
    known_out = sum(c.output_tokens for c in models if c.output_tokens is not None)
    known = sum(c.input_tokens is not None and c.output_tokens is not None for c in models)
    # Only response usage is known after a retry; an error attempt's billing is unknown.
    all_usage_known = complete and observed and all(c.status == 'SUCCEEDED'
        and c.input_tokens is not None and c.output_tokens is not None and len(c.attempts) == 1
        and all(a.status_code is not None and 200 <= a.status_code < 300 for a in c.attempts.values()) for c in models)
    return {'available': True, 'mode': meter.mode, 'logical_calls': len(models),
        'succeeded_calls': sum(c.status == 'SUCCEEDED' for c in models),
        'failed_calls': sum(c.status == 'FAILED' for c in models),
        'interrupted_calls': sum(c.status == 'INTERRUPTED' for c in models),
        'running_calls': sum(c.status == 'RUNNING' for c in models), 'tool_calls': len(tools),
        'known_network_attempts': len(attempts), 'network_attempts': len(attempts) if complete and observed else None,
        'known_input_tokens': known_in, 'known_output_tokens': known_out,
        'input_tokens': known_in if all_usage_known else None, 'output_tokens': known_out if all_usage_known else None,
        'usage_known_calls': known, 'usage_unknown_calls': len(models) - known,
        'unknown_attempts': sum(a.status_code is None or not 200 <= a.status_code < 300 for a in attempts),
        'complete': all_usage_known, 'counts_complete': complete}


def elapsed(start, end=None):
    if not start:
        return None
    try:
        first = datetime.fromisoformat(start)
        last = datetime.fromisoformat(end) if end else datetime.now(timezone.utc)
        return max(0, (last - first).total_seconds())
    except (ValueError, TypeError):
        return None


def time_summary(operation, read_at=None):
    queue = operation.queue_seconds + (elapsed(operation.queued_at, read_at) or 0)
    execution = operation.execution_seconds + (elapsed(operation.run_started_at, read_at) or 0)
    waiting = operation.user_wait_seconds + (elapsed(operation.pause_started_at, read_at) or 0)
    end = operation.closed_at or (operation.ended_at if operation.cleanup_status != 'PENDING' and not operation.run_started_at else None)
    return {'history_complete': operation.timing_history_complete,
        'queue_seconds': queue if operation.timing_history_complete else None,
        'execution_seconds': execution if operation.timing_history_complete else None,
        'user_wait_seconds': waiting if operation.timing_history_complete else None,
        'known_queue_seconds': queue, 'known_execution_seconds': execution, 'known_user_wait_seconds': waiting,
        'wall_seconds': elapsed(operation.created_at, end or read_at),
        'live': bool(operation.queued_at or operation.run_started_at or operation.pause_started_at)}


def runtime_view(store, mission_id):
    with store.lock:
        mission = store.get(mission_id)
        operations = store.operations(mission_id)
        read_at = datetime.now(timezone.utc).isoformat()
        views = [{**op.model_dump(mode='json'), 'usage': meter_summary(op), 'timing': time_summary(op, read_at)} for op in operations]
    summaries = [view['usage'] for view in views]
    available = bool(operations) and all(s['available'] for s in summaries)
    complete = available and all(s['complete'] for s in summaries)
    totals = {key: sum(s.get(key, 0) or 0 for s in summaries) for key in [
        'logical_calls', 'tool_calls', 'known_network_attempts', 'known_input_tokens', 'known_output_tokens',
        'usage_known_calls', 'usage_unknown_calls', 'unknown_attempts']}
    totals.update(available=available, complete=complete,
        counts_complete=available and all(s.get('counts_complete', False) for s in summaries),
        network_attempts=sum(s['network_attempts'] for s in summaries) if available and all(s['network_attempts'] is not None for s in summaries) else None,
        input_tokens=totals['known_input_tokens'] if complete else None,
        output_tokens=totals['known_output_tokens'] if complete else None)
    return {'mission_id': mission.id, 'mission_sequence': mission.sequence, 'read_at': read_at,
        'workflow_operation_id': mission.workflow_operation_id, 'operations': views, 'totals': totals,
        'report_telemetry': mission.telemetry}
