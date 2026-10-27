"""Killable owner; all hooks are test-only and paths must be isolated."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

from backend.process_supervisor import await_start


def main():
    await_start()
    root, phase, action = Path(sys.argv[1]).resolve(), sys.argv[2], sys.argv[3]
    repo = Path(__file__).resolve().parents[1]
    if not root.is_relative_to(repo / 'local_data/game_imports/m4-implementation'):
        raise ValueError('Isolated M4 root required')
    from backend.models import MessageRequest, InputRequest
    from backend.service import MissionService
    from backend.process_runner import ProcessRunner
    from backend.runtime_contracts import RuntimePolicy
    from evaluation.player_fixtures import write_fixture
    from player_data.repository import FixtureRepository
    if phase == 'lease':
        from backend.storage_contracts import checkpoint_writer
        with checkpoint_writer(root / 'tasks/checkpoints.sqlite3'):
            (root / 'ready.json').write_text(json.dumps({'phase': 'lease'}), encoding='utf-8')
            threading.Event().wait()
    write_fixture(root / 'player', speed=44)
    policy = replace(RuntimePolicy(), execution_seconds=300, cleanup_seconds=1, shutdown_seconds=4)
    service = MissionService(root / 'tasks', player_repository=FixtureRepository(root / 'player'),
        runner=ProcessRunner('evaluation.m46_worker'), runtime_policy=policy)

    def ready(mid, oid, **extra):
        op = service.store.operation(oid)
        (root / 'ready.json').write_text(json.dumps({'mission_id': mid, 'operation_id': oid,
            'worker_pid': op.worker_pid, 'phase': phase, **extra}), encoding='utf-8')
        if action == 'kill':
            threading.Event().wait()

    original_save = service.store.save_operation
    def saving(op):
        if phase == 'publish_transaction' and op.status == 'COMPLETED':
            ready(op.mission_id, op.id)
        return original_save(op)
    original_project = service._project_recommendations
    def project(mission, state=None):
        if phase == 'published':
            ready(mission.id, mission.workflow_operation_id)
        return original_project(mission, state)
    service.store.save_operation, service._project_recommendations = saving, project
    message = MessageRequest(request_id='original-request', conversation_id='m46', content='m46:' + phase)
    if phase == 'created':
        with patch.object(service, '_enqueue'):
            accepted = service.submit(message)
        ready(accepted.mission_id, accepted.operation_id)
    else:
        if phase == 'queued':
            service.submit(message.model_copy(update={'content': 'm46:manager', 'request_id': 'blocking-first'}))
            deadline = time.monotonic() + 60
            while not (root / 'tasks/phase.json').exists() and time.monotonic() < deadline:
                time.sleep(.01)
        accepted = service.submit(message)
        if phase == 'queued':
            ready(accepted.mission_id, accepted.operation_id)
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            marker = root / 'tasks/phase.json'
            if marker.exists() and phase != 'queued':
                detail = json.loads(marker.read_text(encoding='utf-8'))
                ready(accepted.mission_id, accepted.operation_id, gated_pid=detail['pid'])
                break
            mission = service.store.get(accepted.mission_id)
            if mission.id not in service.active:
                if mission.status == 'BLOCKED':
                    reason = mission.blocked.reason
                    if phase == reason:
                        ready(accepted.mission_id, accepted.operation_id)
                        break
                    service.input(mission.id, InputRequest(request_id='resume-' + reason,
                        pause_id=mission.blocked.pause_id,
                        values={'approved': True} if reason == 'report_approval' else {'information': 'original answer'}))
                elif mission.status == 'COMPLETED':
                    if phase == 'explanation':
                        accepted = service.submit(MessageRequest(request_id='explanation-request', conversation_id='m46',
                            mission_id=mission.id, intent='explain', content='Explain original'))
                    else:
                        ready(accepted.mission_id, accepted.operation_id)
                        break
                elif mission.status == 'FAILED':
                    raise RuntimeError(mission.error)
            time.sleep(.01)
        else:
            raise RuntimeError('Lifecycle gate not reached')
    service.close()
    (root / 'closed.json').write_text(json.dumps({'closed': True}), encoding='utf-8')


if __name__ == '__main__':
    main()
