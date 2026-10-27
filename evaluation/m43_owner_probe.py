"""Killable real MissionService owner used to prove nested Job cleanup."""
from pathlib import Path
import sys
import time

from backend.process_supervisor import await_start


def main():
    await_start()
    root = Path(sys.argv[1]).resolve()
    repo = Path(__file__).resolve().parents[1]
    if not root.is_relative_to(repo / 'local_data/game_imports/m4-implementation'):
        raise ValueError('Use the isolated M4 directory')
    from backend.models import MessageRequest
    from backend.process_runner import ProcessRunner
    from backend.service import MissionService
    from evaluation.player_fixtures import write_fixture
    from evaluation.m4_process_probe import write
    from player_data.repository import FixtureRepository
    write_fixture(root / 'player', speed=44)
    service = MissionService(root / 'tasks', player_repository=FixtureRepository(root / 'player'),
        runner=ProcessRunner('evaluation.m43_worker'))
    accepted = service.submit(MessageRequest(request_id='owner-fault', conversation_id='owner', content='m43:model-hang'))
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        operation = service.store.operation(accepted.operation_id)
        marker = root / 'tasks/runs' / operation.id / str(operation.run_id) / 'fault-ready'
        if operation.worker_pid and marker.exists():
            write(root / 'ready.json', {'mission_id': accepted.mission_id, 'operation_id': accepted.operation_id,
                'worker_pid': operation.worker_pid})
            import threading
            threading.Event().wait()
        time.sleep(.02)
    raise RuntimeError('Worker did not become ready')


if __name__ == '__main__':
    main()
