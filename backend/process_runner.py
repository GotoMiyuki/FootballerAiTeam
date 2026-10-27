"""Bounded JSON IPC and real process deadlines over the owned Windows Job."""
from dataclasses import asdict
import json
from pathlib import Path
import queue
import subprocess
import threading
import time

from backend.call_limits import ExecutionFailure
from backend.execution_worker import MAX_FRAME_BYTES
from backend.process_supervisor import OwnedProcess
from backend.runtime_contracts import CONTRACT_VERSION


class ProcessRunner:
    def __init__(self, module='backend.execution_worker'):
        self.module = module

    def run(self, payload, policy, deadline, stop, on_event):
        folder = Path(payload['checkpoint_path']).parent / 'runs' / payload['operation_id'] / str(payload['run_id'])
        folder.mkdir(parents=True, exist_ok=True)
        request = folder / 'input.json'
        request.write_text(json.dumps({**payload, 'policy': asdict(policy),
            'remaining_seconds': max(0, deadline - time.monotonic())}, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        if stop.is_set():
            raise ExecutionFailure(getattr(stop, 'reason', 'SERVICE_SHUTDOWN'))
        if time.monotonic() >= deadline:
            raise ExecutionFailure('EXECUTION_TIMEOUT')
        messages = queue.Queue(maxsize=8)
        read_error = threading.Event()
        eof = threading.Event()
        reader_stop = threading.Event()
        spans = {}
        startup_deadline = min(deadline, time.monotonic() + policy.worker_start_seconds)
        result = None
        with OwnedProcess(self.module, request, root=Path(__file__).resolve().parents[1],
                output=subprocess.PIPE, error_output=subprocess.DEVNULL, cleanup_seconds=policy.cleanup_seconds) as worker:
            on_event({'type': 'spawned', 'pid': worker.pid})
            def read():
                try:
                    while True:
                        frame = worker.process.stdout.readline(MAX_FRAME_BYTES + 1)
                        if not frame:
                            return
                        if len(frame) > MAX_FRAME_BYTES:
                            raise ValueError('IPC frame overflow')
                        message = json.loads(frame)
                        if (message['version'], message['operation_id'], message['run_id']) != (CONTRACT_VERSION, payload['operation_id'], payload['run_id']):
                            raise ValueError('Stale or incompatible worker message')
                        while not reader_stop.is_set():
                            try:
                                messages.put(message, timeout=.05)
                                break
                            except queue.Full:
                                continue
                        if reader_stop.is_set():
                            return
                except Exception:
                    read_error.set()
                finally:
                    eof.set()
            reader = threading.Thread(target=read, name=f'worker-ipc-{worker.pid}', daemon=True)
            reader.start()
            try:
                while result is None:
                    now = time.monotonic()
                    if stop.is_set():
                        raise ExecutionFailure(getattr(stop, 'reason', 'SERVICE_SHUTDOWN'))
                    if now >= deadline:
                        raise ExecutionFailure('EXECUTION_TIMEOUT')
                    if startup_deadline and now >= startup_deadline:
                        raise ExecutionFailure('WORKER_START_TIMEOUT')
                    for kind, end in spans.values():
                        if now >= end:
                            raise ExecutionFailure('MODEL_TIMEOUT' if kind == 'model' else 'TOOL_TIMEOUT')
                    if read_error.is_set():
                        raise ExecutionFailure('WORKER_PROTOCOL_ERROR')
                    try:
                        message = messages.get(timeout=.02)
                    except queue.Empty:
                        if eof.is_set() and worker.process.poll() is not None:
                            raise ExecutionFailure('EXECUTION_ERROR')
                        continue
                    kind = message['type']
                    if kind == 'ready':
                        if message['pid'] != worker.pid:
                            raise ExecutionFailure('WORKER_PROTOCOL_ERROR')
                        startup_deadline = None
                        on_event(message)
                    elif kind == 'span_start':
                        seconds = policy.model_call_seconds if message['kind'] == 'model' else policy.tool_seconds
                        spans[message['span_id']] = (message['kind'], min(deadline, time.monotonic() + seconds))
                        on_event(message)
                    elif kind == 'span_end':
                        spans.pop(message['span_id'], None)
                        on_event(message)
                    elif kind in {'node', 'state', 'model_attempt'}:
                        on_event(message)
                    elif kind == 'done':
                        on_event({'type': 'worker_finished'})
                        result = message
                    elif kind == 'error':
                        on_event({'type': 'worker_finished'})
                        raise ExecutionFailure('EXECUTION_ERROR')
                    else:
                        raise ExecutionFailure('WORKER_PROTOCOL_ERROR')
            finally:
                reader_stop.set()
                worker.close()
                reader.join(policy.cleanup_seconds)
                worker.process.stdout.close()
                if reader.is_alive():
                    raise ExecutionFailure('WORKER_PROTOCOL_ERROR')
        if stop.is_set():
            raise ExecutionFailure(getattr(stop, 'reason', 'SERVICE_SHUTDOWN'))
        if time.monotonic() >= deadline:
            raise ExecutionFailure('EXECUTION_TIMEOUT')
        return result
