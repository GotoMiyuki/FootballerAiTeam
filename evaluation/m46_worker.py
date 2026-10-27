"""Real graph/checkpoint worker with explicit offline lifecycle gates."""
import json
import os
from pathlib import Path
import threading

from backend.execution_worker import execute, main
from backend.call_limits import invoke_tool


def offline_execute(payload, policy, emit, **_):
    folder = Path(payload['checkpoint_path']).parent
    phase = payload.get('mission', {}).get('objective', 'm46:explanation').split(':')[-1]

    def gate(node):
        (folder / 'phase.json').write_text(json.dumps({'phase': node, 'pid': os.getpid()}), encoding='utf-8')
        threading.Event().wait()

    class Model:
        def invoke(self, _):
            gate('explanation' if payload['kind'] == 'explanation' else 'tool')

    def runtime(path, on_start):
        from evaluation.test_session_semantics import SessionSemanticsTests
        harness = SessionSemanticsTests()
        harness.calls, harness.require_input = [], True
        def started(node, state):
            on_start(node, state)
            if node == phase:
                gate(node)
            if node == 'coach' and phase == 'tool':
                invoke_tool(Model(), {})
        return harness.runtime(path, started)

    return execute(payload, policy, emit, runtime_factory=runtime, llm_factory=Model)


if __name__ == '__main__':
    main(execute_fn=offline_execute)
