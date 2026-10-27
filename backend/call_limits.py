"""One model/tool policy and span boundary shared by agents and explanations."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import time
from uuid import uuid4

from backend.runtime_contracts import RuntimePolicy


class ExecutionFailure(RuntimeError):
    def __init__(self, code):
        self.code = str(getattr(code, 'value', code))
        super().__init__(self.code)


class ExecutionStop:
    def __init__(self, shutdown, cancelled):
        self.shutdown, self.cancelled = shutdown, cancelled

    def is_set(self):
        return self.cancelled.is_set() or self.shutdown.is_set()

    @property
    def reason(self):
        return 'USER_CANCELLED' if self.cancelled.is_set() else 'SERVICE_SHUTDOWN'


@dataclass
class CallScope:
    policy: RuntimePolicy
    deadline: float
    emit: object = None
    stop: object = None
    identity: object = None


_scope = ContextVar('execution_call_scope', default=None)
_model_call = ContextVar('active_model_transport', default=None)


@dataclass
class ModelCall:
    identity: str
    emit: object
    attempts: int = 0


def model_request(request):
    call = _model_call.get()
    if call is None:
        return
    call.attempts += 1
    request.extensions['fait_attempt'] = (call.identity, call.attempts)
    if call.emit:
        call.emit({'type': 'model_attempt', 'span_id': call.identity, 'attempt': call.attempts, 'phase': 'start'})


def model_response(response):
    call = _model_call.get()
    attempt = response.request.extensions.get('fait_attempt')
    if call and call.emit and attempt and attempt[0] == call.identity:
        call.emit({'type': 'model_attempt', 'span_id': call.identity, 'attempt': attempt[1],
            'phase': 'end', 'status_code': response.status_code})


def observe_transport(client):
    for kind, callback in [('request', model_request), ('response', model_response)]:
        if callback not in client.event_hooks[kind]:
            client.event_hooks[kind].append(callback)
    return client


@dataclass
class Span:
    identity: str
    end: dict = field(default_factory=dict)


@contextmanager
def execution_scope(policy, deadline, *, emit=None, stop=None, identity=None):
    token = _scope.set(CallScope(policy, deadline, emit, stop, identity))
    try:
        yield
    finally:
        _scope.reset(token)


def current_policy():
    scope = _scope.get()
    return scope.policy if scope else RuntimePolicy.from_env()


def current_identity():
    scope = _scope.get()
    return scope.identity if scope else None


def current_deadline():
    scope = _scope.get()
    return scope.deadline if scope else time.monotonic() + current_policy().execution_seconds


def remaining_seconds():
    scope = _scope.get()
    return max(0.0, scope.deadline - time.monotonic()) if scope else current_policy().execution_seconds


def check_execution():
    scope = _scope.get()
    if scope and scope.stop and scope.stop.is_set():
        raise ExecutionFailure(getattr(scope.stop, 'reason', 'SERVICE_SHUTDOWN'))
    if scope and time.monotonic() >= scope.deadline:
        raise ExecutionFailure('EXECUTION_TIMEOUT')


def current_stop():
    scope = _scope.get()
    return scope.stop if scope else None


@contextmanager
def call_span(kind, **metadata):
    check_execution()
    scope = _scope.get()
    span = Span(uuid4().hex)
    started = time.monotonic()
    status = 'FAILED'
    if scope and scope.emit:
        scope.emit({'type': 'span_start', 'span_id': span.identity, 'kind': kind, **metadata})
    try:
        yield span
        status = 'SUCCEEDED'
    finally:
        if scope and scope.emit:
            scope.emit({'type': 'span_end', 'span_id': span.identity, 'kind': kind, 'status': status,
                'duration_seconds': time.monotonic() - started, **span.end})


def invoke_model(model, messages, *, agent='', purpose=''):
    from utils.telemetry import token_usage_from_response
    scope = _scope.get()
    with call_span('model', agent=agent, purpose=purpose) as span:
        call = ModelCall(span.identity, scope.emit if scope else None)
        token = _model_call.set(call)
        try:
            response = model.invoke(messages)
            span.end.update(token_usage_from_response(response))
            return response
        finally:
            _model_call.reset(token)


def invoke_tool(tool, arguments):
    with call_span('tool', purpose=getattr(tool, 'name', type(tool).__name__)):
        return tool.invoke(arguments)
