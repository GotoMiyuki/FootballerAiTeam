"""Trusted worker: graph/checkpoint work only; workspace and E/C stay in owner."""
import contextlib
import json
import os
from pathlib import Path
import sys
import time

from backend.process_supervisor import await_start
from backend.runtime_contracts import CONTRACT_VERSION, RuntimePolicy
from backend.call_limits import execution_scope, invoke_model


MAX_FRAME_BYTES = 4 * 1024 * 1024


def _state(values):
    # LangGraph's Interrupt objects and internal messages stay in the checkpoint.
    # The final snapshot.next is the authority for the supported pause boundary.
    return {key: value for key, value in values.items() if key not in {'messages', '__interrupt__'}}


def execute(payload, policy, emit, *, runtime_factory=None, llm_factory=None):
    from contextlib import nullcontext
    from backend.storage_contracts import checkpoint_writer
    lease = checkpoint_writer(payload['checkpoint_path']) if payload['kind'] == 'workflow' else nullcontext()
    with lease:
        return _execute(payload, policy, emit, runtime_factory=runtime_factory, llm_factory=llm_factory)


def _execute(payload, policy, emit, *, runtime_factory=None, llm_factory=None):
    if payload['kind'] == 'explanation':
        from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
        from utils.helpers import create_llm
        messages = [SystemMessage(content=payload['system'])]
        messages += [(HumanMessage if item['role'] == 'user' else AIMessage)(content=item['content']) for item in payload['history']]
        llm = (llm_factory or create_llm)()
        emit({'type': 'ready', 'pid': os.getpid()})
        answer = invoke_model(llm, messages, agent='explanation', purpose='explain').content
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError('Unsupported model response')
        return {'type': 'done', 'answer': answer}

    from backend.runtime import create_runtime
    from backend.models import MissionView
    from backend.task_context import validate_pause
    from player_data.models import PlayerSnapshot
    from graph import create_initial_state
    from langgraph.types import Command
    mission = MissionView.model_validate(payload['mission'])
    def on_start(node, state):
        emit({'type': 'node', 'node': node, 'state': _state(state)})
    graph, connection = (runtime_factory or create_runtime)(Path(payload['checkpoint_path']), on_start)
    config = {'configurable': {'thread_id': mission.id}, 'recursion_limit': 100}
    try:
        emit({'type': 'ready', 'pid': os.getpid()})
        if payload['resume']:
            supplied = payload['supplied']
            validate_pause(mission, graph.get_state(config), reason='missing_user_input' if supplied else 'report_approval', inputs=payload['inputs'])
            graph_input = Command(resume=supplied) if supplied else None
        else:
            inputs = payload['inputs']
            graph_input = create_initial_state(mission.objective, snapshot=PlayerSnapshot.from_dict(inputs['player_snapshot']),
                continuation_context=inputs.get('continuation_context'), career_memory_context=inputs.get('career_memory_context'))
        for state in graph.stream(graph_input, config, stream_mode='values'):
            emit({'type': 'state', 'state': _state(state)})
        snapshot = graph.get_state(config)
        return {'type': 'done', 'state': _state(snapshot.values), 'next': list(snapshot.next)}
    finally:
        connection.close()


def main(*, runtime_factory=None, llm_factory=None, execute_fn=execute):
    await_start()
    payload = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    policy = RuntimePolicy(**payload['policy'])
    channel = sys.stdout.buffer
    def emit(message):
        body = {**message, 'version': CONTRACT_VERSION, 'operation_id': payload['operation_id'], 'run_id': payload['run_id']}
        frame = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8') + b'\n'
        if len(frame) > MAX_FRAME_BYTES:
            raise ValueError('Worker frame exceeds configured IPC limit')
        channel.write(frame)
        channel.flush()
    with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            with execution_scope(policy, time.monotonic() + payload['remaining_seconds'], emit=emit):
                result = execute_fn(payload, policy, emit, runtime_factory=runtime_factory, llm_factory=llm_factory)
            emit(result)
        except Exception as error:
            emit({'type': 'error', 'code': 'EXECUTION_ERROR', 'error_type': type(error).__name__})


if __name__ == '__main__':
    main()
