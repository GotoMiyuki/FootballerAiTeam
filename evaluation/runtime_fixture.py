"""Explicit in-process adapter for existing graph semantics tests, not deadline proof."""
from backend.call_limits import execution_scope
from backend.execution_worker import execute


class InlineRunner:
    def run(self, payload, policy, deadline, stop, on_event):
        # Resolve patched factories in this process. Production always uses ProcessRunner.
        with execution_scope(policy, deadline, stop=stop, emit=on_event):
            try:
                return execute(payload, policy, on_event)
            finally:
                on_event({'type': 'worker_finished'})


def run_pending(service, mission_id):
    """Tests that held acceptance explicitly dispatch through the production lifecycle."""
    service._enqueue(mission_id, service._execute).result(timeout=60)
