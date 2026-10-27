"""Execution identities and runtime policy used by MissionService and its worker.

Only the coordinator may commit public/business effects. Internal checkpoints
are not proof of an accepted operation result.
"""
from dataclasses import dataclass, fields
from enum import Enum
import math
import os
import re


CONTRACT_VERSION = 1


class OperationKind(str, Enum):
    WORKFLOW = 'workflow'
    EXPLANATION = 'explanation'


class OperationStatus(str, Enum):
    QUEUED = 'QUEUED'
    RUNNING = 'RUNNING'
    PAUSED = 'PAUSED'
    COMPLETED = 'COMPLETED'
    FAILED = 'FAILED'
    CANCELLED = 'CANCELLED'


class EndReason(str, Enum):
    COMPLETED = 'COMPLETED'
    USER_CANCELLED = 'USER_CANCELLED'
    MODEL_TIMEOUT = 'MODEL_TIMEOUT'
    TOOL_TIMEOUT = 'TOOL_TIMEOUT'
    EXECUTION_TIMEOUT = 'EXECUTION_TIMEOUT'
    QUEUE_TIMEOUT = 'QUEUE_TIMEOUT'
    INTERRUPTED = 'INTERRUPTED'
    SERVICE_SHUTDOWN = 'SERVICE_SHUTDOWN'
    EXECUTION_ERROR = 'EXECUTION_ERROR'
    WORKER_PROTOCOL_ERROR = 'WORKER_PROTOCOL_ERROR'
    WORKER_START_TIMEOUT = 'WORKER_START_TIMEOUT'


TERMINAL_STATUSES = frozenset({OperationStatus.COMPLETED, OperationStatus.FAILED, OperationStatus.CANCELLED})


@dataclass(frozen=True)
class ExecutionIdentity:
    operation_id: str
    run_id: int

    def __post_init__(self):
        if not isinstance(self.operation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', self.operation_id):
            raise ValueError('Invalid operation identity')
        if isinstance(self.run_id, bool) or not isinstance(self.run_id, int) or self.run_id < 1:
            raise ValueError('Execution generation must be a positive integer')


def may_commit_result(current_status, current_identity, incoming_identity):
    """Call inside the coordinator's final transaction, never as an earlier promise."""
    return (isinstance(current_identity, ExecutionIdentity)
            and isinstance(incoming_identity, ExecutionIdentity)
            and current_status == OperationStatus.RUNNING
            and current_identity == incoming_identity)


@dataclass(frozen=True)
class RuntimePolicy:
    model_connect_seconds: float = 10.0
    model_read_seconds: float = 120.0
    model_call_seconds: float = 180.0
    tool_seconds: float = 90.0
    execution_seconds: float = 1200.0
    queue_wait_seconds: float = 120.0
    queue_capacity: int = 3
    network_max_attempts: int = 2
    worker_start_seconds: float = 60.0
    cleanup_seconds: float = 3.0
    shutdown_seconds: float = 10.0

    def __post_init__(self):
        integers = {'queue_capacity': (1, 16), 'network_max_attempts': (1, 3)}
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name in integers:
                lo, hi = integers[field.name]
                if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
                    raise ValueError(f'{field.name} must be an integer in [{lo}, {hi}]')
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 86400:
                raise ValueError(f'{field.name} must be finite seconds in (0, 86400]')
        if not self.model_connect_seconds <= self.model_read_seconds <= self.model_call_seconds <= self.execution_seconds:
            raise ValueError('Model timeouts must fit the execution budget')
        if self.tool_seconds > self.execution_seconds:
            raise ValueError('Tool timeout must fit the execution budget')
        if self.cleanup_seconds >= self.shutdown_seconds:
            raise ValueError('Cleanup grace must be shorter than shutdown budget')

    @classmethod
    def from_env(cls, environ=None):
        environment = os.environ if environ is None else environ
        names = {f'FAIT_RUNTIME_{f.name.upper()}': f.name for f in fields(cls)}
        unknown = [key for key in environment if key.startswith('FAIT_RUNTIME_') and key not in names]
        if unknown:
            raise ValueError('Unknown runtime configuration keys: ' + ', '.join(sorted(unknown)))
        result = {}
        for key, name in names.items():
            if key in environment:
                try:
                    result[name] = (int if name in {'queue_capacity', 'network_max_attempts'} else float)(environment[key])
                except (ValueError, TypeError) as exc:
                    raise ValueError(f'Invalid runtime configuration: {key}') from exc
        return cls(**result)
