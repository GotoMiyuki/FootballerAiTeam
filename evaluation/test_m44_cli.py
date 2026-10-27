"""Production CLI cancellation, local pauses and a separately owned HTTP task."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest

from backend.process_supervisor import OwnedProcess
from evaluation import test_cli_persistence as cli_fixtures
from evaluation.m4_audit import dump

REPO = Path(__file__).resolve().parents[1]


class CancellationCLITests(unittest.TestCase):
    def test_local_pause_cancel_replay_and_continue_never_invoke_models(self):
        root = Path(os.environ['FAIT_M44_TEST_ROOT']) / self._testMethodName
        root.mkdir(parents=True, exist_ok=False)
        harness = cli_fixtures.CLIPersistenceTests()
        harness.setUp()
        self.addCleanup(harness.tearDown)
        mission = harness.start()
        operation = harness.repository.operations(mission.id)[0]
        before = harness.calls()
        args = ['--cancel', mission.id, '--operation-id', operation.id, '--expected-run-id', str(operation.run_id),
            '--expected-pause-id', operation.pause_id, '--request-id', 'cli_cancel']
        output = harness.process(args, no_latest=True, read_only=True)
        self.assertIn('CANCELLED', output)
        self.assertEqual(harness.repository.mission(mission.id).status, 'CANCELLED')
        replay = harness.process(args, no_latest=True, read_only=True)
        self.assertIn('原回执重放', replay)
        invalid = harness.process(['--continue', mission.id, '--request-id', 'cli_cancel'],
            no_latest=True, read_only=True, expected=1)
        self.assertIn('已用于取消', invalid)
        self.assertEqual(harness.calls(), before)
        dump(root / 'evidence.json', {'cancel_output': output, 'replay_output': replay, 'invalid_continue': invalid, 'extra_node_calls': 0})

    def test_cli_cancels_running_http_owner_without_acquiring_its_writer_lock(self):
        import httpx
        root = Path(os.environ['FAIT_M44_TEST_ROOT']) / self._testMethodName
        root.mkdir(parents=True, exist_ok=False)
        with socket.socket() as port_probe:
            port_probe.bind(('127.0.0.1', 0))
            port = port_probe.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        with (root / 'owner.log').open('w', encoding='utf-8') as log, \
                OwnedProcess('evaluation.m44_api_fixture', root, '--port', port, root=REPO, output=log) as owner:
            end = time.monotonic() + 60
            while True:
                try:
                    if httpx.get(origin + '/api/health', timeout=.5).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > end or owner.process.poll() is not None:
                    self.fail('Isolated HTTP owner did not start; see owner.log')
                time.sleep(.05)
            accepted = httpx.post(origin + '/api/messages', json={'request_id': 'running', 'conversation_id': 'http_owner', 'content': 'm43:model-hang'}).json()
            marker = root / 'tasks/runs' / accepted['operation_id'] / '1/fault-ready'
            while not marker.exists():
                if time.monotonic() > end:
                    self.fail('Own model worker did not enter its span')
                time.sleep(.02)
            args = [sys.executable, 'app.py', '--cancel', accepted['mission_id'], '--operation-id', accepted['operation_id'],
                '--expected-run-id', '1', '--request-id', 'cli_remote_cancel', '--api-url', origin]
            result = subprocess.run(args, cwd=REPO, capture_output=True, encoding='utf-8', timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('CANCELLED', result.stdout)
            replay = subprocess.run(args, cwd=REPO, capture_output=True, encoding='utf-8', timeout=60)
            self.assertEqual(replay.returncode, 0, replay.stdout + replay.stderr)
            self.assertIn('原回执重放', replay.stdout)
            operations = httpx.get(origin + f"/api/missions/{accepted['mission_id']}/operations").json()
            self.assertEqual((operations[0]['status'], operations[0]['cleanup_status']), ('CANCELLED', 'COMPLETE'))
            self.assertIsNone(owner.process.poll())
            dump(root / 'evidence.json', {'owned_owner_pid': owner.pid, 'origin': origin, 'output': result.stdout,
                'replay': replay.stdout, 'operation': operations[0], 'http_owner_remained_running': True})
