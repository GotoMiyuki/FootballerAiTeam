"""Offline worker faults and real graph fixtures. Only selected by test injection."""
import os
import json
from pathlib import Path
import sys
import threading

from backend.call_limits import invoke_model, invoke_tool
from backend.execution_worker import execute, main, MAX_FRAME_BYTES
from backend.runtime_contracts import CONTRACT_VERSION


def runtime(path, on_start):
    from evaluation.test_session_semantics import SessionSemanticsTests
    harness = SessionSemanticsTests()
    harness.calls = []
    harness.require_input = True
    graph, connection = harness.runtime(path, on_start)
    return graph, connection


class Model:
    def invoke(self, _messages):
        threading.Event().wait()


def offline_execute(payload, policy, emit, **factories):
    objective = payload.get('mission', {}).get('objective', '')
    mode = objective.removeprefix('m43:') if objective.startswith('m43:') else ''
    if payload['kind'] == 'explanation':
        mode = 'model-hang'
    if mode == 'startup-hang':
        threading.Event().wait()
    if mode:
        emit({'type': 'ready', 'pid': os.getpid()})
        marker = Path(payload['checkpoint_path']).parent / 'runs' / payload['operation_id'] / str(payload['run_id']) / 'fault-ready'
        marker.write_text(mode, encoding='utf-8')
        if mode == 'model-hang':
            invoke_model(Model(), [])
        elif mode == 'tool-hang':
            invoke_tool(Model(), {})
        elif mode == 'execution-hang':
            threading.Event().wait()
        elif mode == 'bad-protocol':
            sys.__stdout__.buffer.write(b'{invalid json}\n')
            sys.__stdout__.buffer.flush()
            threading.Event().wait()
        elif mode == 'oversized-protocol':
            sys.__stdout__.buffer.write(b'x' * (MAX_FRAME_BYTES + 1))
            sys.__stdout__.buffer.flush()
            threading.Event().wait()
        elif mode == 'stale-protocol':
            body = {'type': 'done', 'version': CONTRACT_VERSION, 'operation_id': payload['operation_id'],
                'run_id': payload['run_id'] + 1, 'state': {}, 'next': []}
            sys.__stdout__.buffer.write(json.dumps(body).encode('utf-8') + b'\n')
            sys.__stdout__.buffer.flush()
            threading.Event().wait()
        elif mode == 'instant-failure':
            raise RuntimeError('offline fault')
        else:
            raise ValueError('Unknown test mode')
    return execute(payload, policy, emit, runtime_factory=runtime, **{k: v for k, v in factories.items() if k != 'runtime_factory'})


if __name__ == '__main__':
    main(execute_fn=offline_execute)
