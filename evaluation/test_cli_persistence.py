"""C1.3: process exit/reload, original pauses/input, read-only history and one writer."""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from evaluation.player_fixtures import write_fixture
from utils.sessions import SessionRepository


class CLIPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.player = self.root / 'player'
        self.tasks = self.root / 'cli'
        self.calls_file = self.root / 'calls.jsonl'
        write_fixture(self.player, name='Original CLI Player', speed=44)
        self.repository = SessionRepository(self.tasks)

    def tearDown(self):
        self.temp.cleanup()

    def process(self, args, supplied='', *, require_input=False, no_latest=False, read_only=False, expected=0):
        env = {**os.environ, 'FAIT_CLI_DATA_DIR': str(self.tasks), 'FAIT_PLAYER_DATA_MODE': 'demo',
            'FAIT_PLAYER_DATA_ROOT': str(self.player), 'FAIT_TEST_PLAYER_ROOT': str(self.player),
            'FAIT_TEST_CALLS': str(self.calls_file), 'FAIT_TEST_REQUIRE_INPUT': str(int(require_input)),
            'FAIT_TEST_NO_LATEST': str(int(no_latest)), 'FAIT_TEST_READ_ONLY': str(int(read_only)),
            'PYTHONUTF8': '1'}
        result = subprocess.run([sys.executable, '-m', 'evaluation.cli_process_fixture', *args],
            input=supplied, capture_output=True, encoding='utf-8', env=env,
            cwd=Path(__file__).resolve().parent.parent, timeout=60)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertNotIn('No live model allowed', result.stdout + result.stderr)
        self.assertNotIn('Resume/history must not', result.stdout + result.stderr)
        self.assertNotIn('History must not', result.stdout + result.stderr)
        return result.stdout

    def start(self, *, require_input=False):
        output = self.process(['原任务训练安排'], 'q\n', require_input=require_input)
        identity = re.search(r'\[新任务\] (mission_[a-f0-9]+)', output).group(1)
        mission = self.repository.mission(identity)
        self.assertEqual(mission.status, 'BLOCKED')
        self.assertIn('[暂停已保存]', output)
        return mission

    def calls(self):
        return [json.loads(line) for line in self.calls_file.read_text(encoding='utf-8').splitlines()]

    def test_approval_resumes_in_second_process_only_document_with_original_input(self):
        parent = self.start()
        self.assertEqual(parent.blocked.reason, 'report_approval')
        before = self.calls()
        self.assertNotIn('document', [call['node'] for call in before])
        fixed = self.repository._read('SELECT payload FROM mission_inputs WHERE mission_id=?', (parent.id,))
        write_fixture(self.player, speed=99)
        output = self.process(['--continue', parent.id], '\n', no_latest=True)
        completed = self.repository.mission(parent.id)
        self.assertEqual(completed.status, 'COMPLETED')
        self.assertEqual(completed.input_reference, parent.input_reference)
        self.assertIn('原球员速度：44', output)
        added = self.calls()[len(before):]
        self.assertEqual([call['node'] for call in added], ['document'])
        self.assertNotEqual(added[0]['pid'], before[0]['pid'])
        self.assertEqual(added[0]['version'], parent.input_reference.state_version)
        self.assertEqual(self.repository._read('SELECT payload FROM mission_inputs WHERE mission_id=?', (parent.id,)), fixed)
        self.assertEqual(len(self.repository.list()), 1)

    def test_missing_input_then_approval_survive_three_distinct_processes(self):
        parent = self.start(require_input=True)
        self.assertEqual(parent.blocked.reason, 'missing_user_input')
        write_fixture(self.player, speed=98)
        before = self.calls()
        self.process(['--continue', parent.id], '恢复反馈：没有疼痛\nq\n', require_input=True, no_latest=True)
        paused = self.repository.mission(parent.id)
        self.assertEqual(paused.blocked.reason, 'report_approval')
        middle = self.calls()[len(before):]
        self.assertNotIn('manager', [call['node'] for call in middle])
        self.assertNotIn('document', [call['node'] for call in middle])
        self.assertIn('manager_revision', [call['node'] for call in middle])
        from backend.runtime import inspect_checkpoint
        state = inspect_checkpoint(self.repository.checkpoint_path, parent.id).values
        self.assertIn('没有疼痛', json.dumps(state['user_context']['human_inputs'], ensure_ascii=False))
        self.process(['--continue', parent.id], '\n', require_input=True, no_latest=True)
        current = self.repository.mission(parent.id)
        self.assertEqual(current.status, 'COMPLETED')
        self.assertEqual(current.input_reference, parent.input_reference)
        self.assertIn('原球员速度：44', self.repository.report(current))
        self.assertEqual(len({call['pid'] for call in self.calls()}), 3)
        self.assertEqual([call['node'] for call in self.calls()].count('document'), 1)

    def test_completed_history_is_read_only_without_configuration_or_latest_access(self):
        parent = self.start()
        self.process(['--continue', parent.id], '\n', no_latest=True)
        before = self.repository.workspace_path.read_bytes()
        checkpoint = self.repository.checkpoint_path.read_bytes()
        calls = self.calls()
        for args in (['--list'], ['--show', parent.id], ['--continue', parent.id]):
            output = self.process(args, no_latest=True, read_only=True)
            self.assertIn(parent.id, output)
        self.assertEqual(self.repository.workspace_path.read_bytes(), before)
        self.assertEqual(self.repository.checkpoint_path.read_bytes(), checkpoint)
        self.assertEqual(self.calls(), calls)

    def test_invalid_missing_checkpoint_or_snapshot_refuses_before_any_mutation(self):
        from backend.store import Store
        from backend.runtime import inspect_checkpoint
        for kind in ('missing_checkpoint', 'missing_snapshot', 'wrong_version', 'wrong_pause'):
            with self.subTest(kind=kind):
                parent = self.start()
                if kind == 'missing_checkpoint':
                    with contextlib.closing(sqlite3.connect(self.repository.checkpoint_path)) as db, db:
                        db.execute('DELETE FROM checkpoints WHERE thread_id=?', (parent.id,))
                elif kind in {'missing_snapshot', 'wrong_version'}:
                    from evaluation import test_session_semantics as semantics
                    harness = semantics.SessionSemanticsTests()
                    harness.calls, harness.require_input = [], False
                    graph, db = harness.runtime(self.repository.checkpoint_path, lambda *_: None)
                    try:
                        values = inspect_checkpoint(self.repository.checkpoint_path, parent.id).values['player_snapshot']
                        if kind == 'missing_snapshot':
                            values.pop('profile')
                        else:
                            values['metadata']['state_version'] = 'wrong-version'
                        graph.update_state({'configurable': {'thread_id': parent.id}}, {'player_snapshot': values}, as_node='reviewer')
                    finally:
                        db.close()
                else:
                    store = Store(self.repository.workspace_path)
                    try:
                        parent.blocked.reason = 'missing_user_input'
                        store.save(parent)
                    finally:
                        store.close()
                before = self.repository.workspace_path.read_bytes()
                calls = self.calls()
                output = self.process(['--continue', parent.id], read_only=True, no_latest=True, expected=1)
                self.assertIn('无法恢复', output)
                self.assertEqual(self.repository.workspace_path.read_bytes(), before)
                self.assertEqual(self.calls(), calls)

    def test_related_cli_uses_new_identity_latest_snapshot_and_persistent_dedup(self):
        parent = self.start()
        self.process(['--continue', parent.id], '\n', no_latest=True)
        original = self.repository.report(self.repository.mission(parent.id))
        write_fixture(self.player, speed=73)
        args = ['--reevaluate', parent.id, '新的训练要求', '--reason', '新比赛', '--request-id', 'cli-request-one']
        self.process(args, 'q\n')
        child = self.repository.mission(self.repository.list()[0]['thread_id'])
        self.assertNotEqual(child.id, parent.id)
        self.assertEqual(child.lineage.parent_mission_id, parent.id)
        self.assertNotEqual(child.input_reference.state_version, parent.input_reference.state_version)
        before = self.calls()
        self.process(args, no_latest=True)
        self.assertEqual(self.calls(), before)
        self.assertEqual(len(self.repository.list()), 2)
        self.assertEqual(child.status, 'BLOCKED')
        self.assertEqual(self.repository.report(self.repository.mission(parent.id)), original)
        self.process(['--continue', child.id], '\n', no_latest=True)
        self.assertIn('原球员速度：73', self.repository.report(self.repository.mission(child.id)))

    def test_one_writer_guard_blocks_other_process_but_allows_read_only_history(self):
        from backend.cli import CLIMissionService
        parent = self.start()
        service = CLIMissionService(self.tasks)
        try:
            before = self.repository.workspace_path.read_bytes()
            output = self.process(['新并发任务'], expected=1)
            self.assertIn('已有写入进程', output)
            self.process(['--show', parent.id], no_latest=True, read_only=True)
            self.assertEqual(self.repository.workspace_path.read_bytes(), before)
        finally:
            service.close()
        self.process(['--continue', parent.id], '\n', no_latest=True)
        self.assertEqual(self.repository.mission(parent.id).status, 'COMPLETED')

    def test_unrelated_text_cannot_approve_or_replan_and_exit_preserves_pause(self):
        parent = self.start()
        calls = self.calls()
        before = self.repository.workspace_path.read_bytes()
        output = self.process(['--continue', parent.id], '请重新分析并生成报告\nq\n', no_latest=True)
        self.assertIn('输入无效', output)
        self.assertEqual(self.calls(), calls)
        self.assertEqual(self.repository.workspace_path.read_bytes(), before)
        self.assertEqual(self.repository.mission(parent.id).blocked.reason, 'report_approval')

    def test_failed_cli_task_retry_creates_child_and_never_resumes_original(self):
        from backend.store import Store
        parent = self.start()
        store = Store(self.repository.workspace_path)
        try:
            parent.status, parent.blocked, parent.error = 'FAILED', None, 'fixture interrupted'
            store.save(parent)
        finally:
            store.close()
        write_fixture(self.player, speed=73)
        self.process(['--retry', parent.id, '重新尝试训练安排', '--reason', '明确重试',
            '--request-id', 'cli-retry-one'], 'q\n')
        child = self.repository.mission(self.repository.list()[0]['thread_id'])
        self.assertNotEqual(child.id, parent.id)
        self.assertEqual(child.lineage.operation, 'retry')
        self.assertEqual(self.repository.mission(parent.id).status, 'FAILED')
        self.assertNotEqual(child.input_reference.state_version, parent.input_reference.state_version)

    def test_index_empty_read_does_not_initialize_directory_and_legacy_is_unchanged(self):
        from utils import sessions
        import app
        self.assertEqual(self.repository.list(), [])
        self.assertFalse(self.tasks.exists())
        legacy = self.root / 'sessions.json'
        with patch.object(sessions, 'SESSIONS_FILE', str(legacy)), patch.object(app.config, 'CLI_DATA_DIR', str(self.tasks)):
            identity = sessions.create_session('旧元数据任务')
            before = legacy.read_bytes()
            output = io.StringIO()
            with patch('sys.argv', ['app.py', '--continue', identity]), \
                    patch.object(app, 'check_config', side_effect=AssertionError('No model config')), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(app.main(), 0)
            self.assertIn('无法恢复原执行', output.getvalue())
            self.assertEqual(legacy.read_bytes(), before)
            self.assertFalse(self.tasks.exists())


if __name__ == '__main__':
    unittest.main()
