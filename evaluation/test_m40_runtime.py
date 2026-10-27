"""M4.0 contracts and real Windows process/checkpoint ownership boundaries."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import unittest
from unittest.mock import patch

from backend.process_supervisor import OwnedProcess, ProcessIsolationError, python_module_command
from backend.runtime_contracts import ExecutionIdentity, OperationStatus, RuntimePolicy, may_commit_result

REPO = Path(__file__).resolve().parents[1]


class RuntimeContractTests(unittest.TestCase):
    def test_unknown_or_unbounded_configuration_fails_closed(self):
        for env in [{'FAIT_RUNTIME_MODEL_CALL_SECONDS': 'nan'}, {'FAIT_RUNTIME_EXECUTION_SECONDS': 'inf'},
                    {'FAIT_RUNTIME_NETWORK_MAX_ATTEMPTS': '0'}, {'FAIT_RUNTIME_QUEUE_CAPACITY': '100000'},
                    {'FAIT_RUNTIME_TYPO': '3'}, {'FAIT_RUNTIME_CLEANUP_SECONDS': '11'},
                    {'FAIT_RUNTIME_TOOL_SECONDS': '1201'}]:
            with self.subTest(keys=list(env)), self.assertRaises(ValueError):
                RuntimePolicy.from_env(env)
        policy = RuntimePolicy.from_env({'FAIT_RUNTIME_NETWORK_MAX_ATTEMPTS': '1', 'FAIT_RUNTIME_QUEUE_CAPACITY': '2'})
        self.assertEqual((policy.network_max_attempts, policy.queue_capacity), (1, 2))

    def test_terminal_and_stale_generation_have_no_commit_authority(self):
        identity = ExecutionIdentity('operation_one', 2)
        self.assertTrue(may_commit_result(OperationStatus.RUNNING, identity, identity))
        for status in OperationStatus:
            if status != OperationStatus.RUNNING:
                self.assertFalse(may_commit_result(status, identity, identity))
        self.assertFalse(may_commit_result(OperationStatus.RUNNING, identity, ExecutionIdentity('operation_one', 1)))
        self.assertFalse(may_commit_result(OperationStatus.RUNNING, identity, ExecutionIdentity('operation_two', 2)))
        self.assertFalse(may_commit_result(OperationStatus.RUNNING, identity, None))


@unittest.skipUnless(os.name == 'nt', 'Windows Job isolation is explicitly Windows-only')
class WindowsOwnershipTests(unittest.TestCase):
    def setUp(self):
        folder = os.environ.get('FAIT_M40_TEST_ROOT')
        if not folder:
            self.skipTest('Set FAIT_M40_TEST_ROOT to a fresh isolated implementation directory')
        self.root = Path(folder).resolve() / self._testMethodName
        if not self.root.is_relative_to(REPO / 'local_data/game_imports/m4-implementation'):
            raise AssertionError('Test root must be isolated')
        self.root.mkdir(parents=True, exist_ok=False)
        self.log = (self.root / 'process.log').open('w', encoding='utf-8')
        self.records = []
        self.addCleanup(self.finish)

    def finish(self):
        (self.root / 'evidence.json').write_text(json.dumps(self.records, ensure_ascii=False, indent=2), encoding='utf-8')
        self.log.close()

    def worker(self, mode, folder=None):
        result = OwnedProcess('evaluation.m4_process_probe', mode, folder or self.root / 'worker', root=REPO, output=self.log)
        self.records.append({'owned_pid': result.pid, 'command': result.command, 'mode': mode})
        return result

    def ready(self, worker, folder=None):
        path = (folder or self.root / 'worker') / 'ready.json'
        end = time.monotonic() + 60
        while time.monotonic() < end:
            if path.exists():
                result = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(result['pid'], worker.pid)
                self.records.append({'ready': result})
                return result
            if worker.process.poll() is not None:
                self.fail(f'Own worker exited {worker.process.returncode}; see {self.log.name}')
            time.sleep(.02)
        self.fail('Own worker readiness exceeded 60s')

    def test_normal_existing_graph_finishes_and_closes(self):
        with self.worker('normal') as worker:
            self.assertEqual(worker.wait(60), 0)
        result = json.loads((self.root / 'worker/result.json').read_text(encoding='utf-8'))
        self.assertIn('原球员速度：44', result['report'])
        self.assertEqual(result['nodes'].count('document'), 1)
        self.records.append({'result': result})

    def test_permanent_block_is_bounded_and_next_worker_can_run(self):
        worker = self.worker('hold')
        try:
            self.ready(worker)
            started = time.monotonic()
        finally:
            worker.close()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 3)
        with self.worker('normal', self.root / 'next') as next_worker:
            self.assertEqual(next_worker.wait(60), 0)
        self.records.append({'cleanup_seconds': elapsed, 'first_exit_code': worker.process.returncode, 'next_exit_code': 0})

    def test_owner_kill_removes_its_checkpoint_child(self):
        with self.worker('parent') as parent:
            ready = self.ready(parent)
            child_pid = ready['child_pid']
            child_ready = json.loads((self.root / 'worker/child/ready.json').read_text(encoding='utf-8'))
            self.assertEqual(child_ready['pid'], child_pid)
            # Keep a handle to the exact child, so PID reuse cannot affect proof.
            api = ctypes.WinDLL('kernel32', use_last_error=True)
            api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            api.OpenProcess.restype = wintypes.HANDLE
            api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            api.WaitForSingleObject.restype = wintypes.DWORD
            api.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = api.OpenProcess(0x00100000, False, child_pid)  # SYNCHRONIZE, no terminate permission needed
            self.assertTrue(handle)
            try:
                started = time.monotonic()
                parent.process.kill()  # own Popen only; DO NOT close the outer Job yet
                parent.wait(3)
                self.assertEqual(api.WaitForSingleObject(handle, 3000), 0)
                self.records.append({'parent_killed': parent.pid, 'child_terminated': child_pid,
                    'seconds': time.monotonic() - started, 'outer_job_still_open_during_verification': True})
            finally:
                api.CloseHandle(handle)
        self.assert_checkpoint_readable(self.root / 'worker/child')

    def assert_checkpoint_readable(self, folder):
        from backend.runtime import inspect_checkpoint
        snapshot = inspect_checkpoint(folder / 'checkpoints.sqlite3', 'm40-probe')
        self.assertEqual(tuple(snapshot.next), ('document',))
        with sqlite3.connect(folder / 'checkpoints.sqlite3', timeout=1) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('SELECT count(*) FROM m40_uncommitted').fetchone()[0], 0)
            db.execute('BEGIN IMMEDIATE')  # a killed owner did release its writer lock
            db.rollback()
        self.records.append({'checkpoint_readable': True, 'uncommitted_write_rolled_back': True, 'sqlite_writer_reacquired': True})

    def test_checkpoint_survives_kill_and_new_process_resumes(self):
        folder = self.root / 'worker'
        worker = self.worker('checkpoint')
        try:
            before = self.ready(worker)
        finally:
            worker.close()
        self.assert_checkpoint_readable(folder)
        with self.worker('resume') as reopened:
            self.assertNotEqual(reopened.pid, worker.pid)
            self.assertEqual(reopened.wait(60), 0)
        result = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
        self.assertEqual(result['version'], before['version'])
        self.assertEqual(result['nodes'], ['document'])
        self.records.append({'resumed': result, 'old_pid': worker.pid, 'new_pid': reopened.pid})

    def test_failed_job_assignment_never_admits_child(self):
        with patch('backend.process_supervisor.WindowsJob.attach', side_effect=ProcessIsolationError('isolated test rejection')):
            with self.assertRaises(ProcessIsolationError):
                self.worker('hold')
        self.assertFalse((self.root / 'worker/ready.json').exists())

    def test_worker_without_bootstrap_admission_does_no_writes(self):
        command, env = python_module_command('evaluation.m4_process_probe', 'hold', self.root / 'unadmitted', root=REPO)
        result = subprocess.run(command, input=b'', capture_output=True, env=env, cwd=REPO, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'unadmitted').exists())
        self.records.append({'return_code': result.returncode, 'no_admission_no_writes': True})


if __name__ == '__main__':
    unittest.main()
