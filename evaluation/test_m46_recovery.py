"""M4.6 current-format recovery, refusal and real owner kill/restart matrix."""
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.main import create_app
from backend.models import MessageRequest, InputRequest
from backend.process_runner import ProcessRunner
from backend.process_supervisor import OwnedProcess
from backend.runtime_contracts import RuntimePolicy
from backend.service import MissionService
from backend.store import Store
from backend.runtime import inspect_checkpoint
from evaluation import test_session_semantics as semantics
from evaluation.runtime_fixture import InlineRunner
from evaluation.player_fixtures import write_fixture
from evaluation.m4_audit import dump, digest
from evaluation.test_m43_runtime import process_handle
from player_data.repository import FixtureRepository
from utils.sessions import SessionRepository

REPO = Path(__file__).resolve().parents[1]


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.environ['FAIT_M46_TEST_ROOT']) / self._testMethodName
        self.root.mkdir(parents=True, exist_ok=False)
        self.h = semantics.SessionSemanticsTests()
        self.h.root, self.h.fixture = self.root, self.root / 'player'
        write_fixture(self.h.fixture, speed=44)
        self.h.repository, self.h.calls, self.h.require_input = FixtureRepository(self.h.fixture), [], False
        self.runtime = patch('backend.runtime.create_runtime', side_effect=self.h.runtime)
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.records = []
        self.addCleanup(lambda: dump(self.root / 'evidence.json', self.records))

    def app(self):
        return create_app(data_dir=self.h.root / 'tasks', player_repository=self.h.repository, demo=False, runner=InlineRunner())

    def counts(self, service):
        return {name: service.store.db.execute(f'SELECT count(*) FROM {name}').fetchone()[0]
            for name in ['missions', 'events', 'messages', 'request_receipts', 'operations']}

    def test_both_pauses_reject_changed_checkpoint_material_before_acceptance(self):
        for pause in ['missing_user_input', 'report_approval']:
            for field in ['player_snapshot', 'continuation_context', 'career_memory_context', 'objective']:
                with self.subTest(pause=pause, field=field):
                    self.h.root = self.root / f'{pause}-{field}'
                    self.h.require_input = pause == 'missing_user_input'
                    with TestClient(self.app()) as client:
                        mid = self.h.start(client)
                        mission = self.h.wait(client, mid, 'BLOCKED')
                        service = client.app.state.service
                        state = inspect_checkpoint(service.checkpoint_path, mid).values
                        if field == 'player_snapshot':
                            changed = deepcopy(state[field])
                            changed['profile']['attributes']['physical']['speed'] = 99
                            patch_state = {field: changed}  # keep the same version/context IDs
                        elif field == 'objective':
                            patch_state = {'user_context': {**state['user_context'], 'request': 'other mission'}}
                        else:
                            patch_state = {field: {**state.get(field, {}), 'foreign_material': 'wrong frozen input'}}
                        graph, connection = self.h.runtime(service.checkpoint_path, lambda *_: None)
                        try:
                            graph.update_state({'configurable': {'thread_id': mid}}, patch_state)
                        finally:
                            connection.close()
                        before, calls = self.counts(service), list(self.h.calls)
                        with patch.object(self.h.repository, 'read_snapshot', side_effect=AssertionError('No latest input')):
                            view = client.get(f'/api/missions/{mid}').json()
                            self.assertTrue(view['resume_error'])
                            response = client.post(f'/api/missions/{mid}/input', json={'request_id': 'refused',
                                'pause_id': mission['blocked']['pause_id'],
                                'values': {'information': 'answer'} if self.h.require_input else {'approved': True}})
                        self.assertEqual(response.status_code, 422, response.text)
                        self.assertEqual((self.counts(service), self.h.calls), (before, calls))
                        self.records.append({'pause': pause, 'field': field, 'refused_before_acceptance': True})

    def test_fixed_input_versions_and_hashes_refused_before_any_resume_write(self):
        for kind in ['missing', 'shape', 'hash', 'version', 'mode', 'context']:
            with self.subTest(kind=kind):
                self.h.root = self.root / kind
                with TestClient(self.app()) as client:
                    mid = self.h.start(client)
                    mission = self.h.wait(client, mid, 'BLOCKED')
                    service = client.app.state.service
                    material = service.store.inputs(mid)
                    if kind == 'missing':
                        service.store.db.execute('DELETE FROM mission_inputs WHERE mission_id=?', (mid,))
                    else:
                        from player_data.models import content_hash
                        if kind == 'shape':
                            material = [1]
                        elif kind == 'hash':
                            material['content_hash'] = 'wrong'
                        else:
                            material.pop('content_hash')
                            if kind == 'version': material['schema_version'] = 99
                            if kind == 'mode': material['mode'] = 'demo'
                            if kind == 'context': material['player_snapshot']['context']['player_id'] = 'other'
                            material['content_hash'] = content_hash(material)
                        service.store.db.execute('UPDATE mission_inputs SET payload=? WHERE mission_id=?', (json.dumps(material), mid))
                    service.store.db.commit()
                    before = self.counts(service)
                    response = client.post(f'/api/missions/{mid}/input', json={'request_id': 'invalid-input',
                        'pause_id': mission['blocked']['pause_id'], 'values': {'approved': True}})
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(self.counts(service), before)
                    self.records.append({'kind': kind, 'status': response.status_code, 'unchanged': True})

    def test_operation_authority_overrides_a_stale_blocked_mission(self):
        from backend.runtime_contracts import OperationStatus, OperationKind
        with TestClient(self.app()) as client:
            mid = self.h.start(client); self.h.wait(client, mid, 'BLOCKED')
            service = client.app.state.service
            original = service.store.operation(service.store.get(mid).workflow_operation_id)
            for field, value in [('status', OperationStatus.FAILED), ('pause_id', 'other-pause'), ('kind', OperationKind.EXPLANATION)]:
                op = original.model_copy(update={field: value})
                service.store.save_operation(op)
                view = client.get(f'/api/missions/{mid}').json()
                self.assertTrue(view['resume_error'])
                self.assertNotIn('approve_report', view['available_operations'])
                before = self.counts(service)
                response = client.post(f'/api/missions/{mid}/input', json={'request_id': 'stale-' + field,
                    'pause_id': original.pause_id, 'values': {'approved': True}})
                self.assertEqual(response.status_code, 422)
                self.assertEqual(self.counts(service), before)
                self.records.append({'mismatch': field, 'operation_authority_enforced': True})

    def test_restart_explanation_is_atomic_visible_and_once_only(self):
        with TestClient(self.app()) as client:
            mid = self.h.start(client)
            self.h.complete(client, mid)
            service = client.app.state.service
            report, original = service.store.report(mid), service.store.get(mid)
            with patch.object(service, '_enqueue'):
                accepted = service.submit(MessageRequest(request_id='explain-after-report', conversation_id='conv_session',
                    mission_id=mid, intent='explain', content='why'))
        with TestClient(self.app()) as client:
            service = client.app.state.service
            self.assertEqual(service.store.operation(accepted.operation_id).error, 'INTERRUPTED')
            self.assertEqual(service.store.report(mid), report)
            current = service.store.get(mid)
            self.assertEqual((current.status, current.telemetry), ('COMPLETED', original.telemetry))
            self.assertTrue(any(m.kind == 'explanation' and m.operation_status == 'FAILED'
                for m in service.store.messages('conv_session')))
            before = self.counts(service)
            self.assertTrue(service.submit(MessageRequest(request_id='explain-after-report', conversation_id='conv_session',
                mission_id=mid, intent='explain', content='why')).replayed)
        with TestClient(self.app()) as client:
            self.assertEqual(self.counts(client.app.state.service), before)
        self.records.append({'report_preserved': True, 'failed_explanation_visible': True, 'restart_replay_once': True})

    def test_restart_failure_rolls_back_operation_mission_message_and_events(self):
        with TestClient(self.app()) as client:
            service = client.app.state.service
            with patch.object(service, '_enqueue'):
                accepted = service.submit(MessageRequest(request_id='created', conversation_id='conv_session', content='created'))
            before = self.counts(service)
        original = Store.save_operation
        def failure(store, operation):
            if operation.error == 'INTERRUPTED':
                raise RuntimeError('restart transaction fault')
            return original(store, operation)
        with patch.object(Store, 'save_operation', failure), self.assertRaises(RuntimeError):
            with TestClient(self.app()): pass
        repo = SessionRepository(self.h.root / 'tasks')
        self.assertEqual(repo.mission(accepted.mission_id).status, 'CREATED')
        self.assertEqual(repo.operations(accepted.mission_id)[0].status, 'QUEUED')
        with TestClient(self.app()) as client:
            self.assertEqual(client.app.state.service.store.get(accepted.mission_id).status, 'FAILED')
        self.records.append({'failed_startup_atomic': True, 'following_startup_recovers': True, 'baseline': before})

    def test_restart_preserves_completed_failed_timeout_and_cancelled_cleanup(self):
        with TestClient(self.app()) as client:
            complete = self.h.start(client); self.h.complete(client, complete)
            cancelled = self.h.start(client)
            paused = self.h.wait(client, cancelled, 'BLOCKED')
            service = client.app.state.service
            op = service.store.operation(paused['workflow_operation_id'])
            receipt = client.post(f'/api/missions/{cancelled}/operations/{op.id}/cancel', json={
                'request_id': 'cancel-before-restart', 'expected_run_id': op.run_id, 'expected_pause_id': op.pause_id})
            self.assertTrue(receipt.json()['accepted'])
            op = service.store.operation(op.id); op.cleanup_status = 'PENDING'; service.store.save_operation(op)
            with patch.object(service, '_enqueue'):
                accepted = service.submit(MessageRequest(request_id='timeout-terminal', conversation_id='conv_session', content='timeout'))
            with service.store.transaction():
                service._operation_failed(service.store.operation(accepted.operation_id), 'MODEL_TIMEOUT')
            original = {mid: service.store.get(mid).model_dump() for mid in [complete, cancelled, accepted.mission_id]}
            report = service.store.report(complete)
        with TestClient(self.app()) as client:
            service = client.app.state.service
            self.assertEqual({mid: service.store.get(mid).model_dump() for mid in original}, original)
            self.assertEqual(service.store.report(complete), report)
            op = service.store.operation(op.id)
            self.assertEqual((op.status, op.error, op.cleanup_status), ('CANCELLED', 'USER_CANCELLED', 'COMPLETE'))
            self.assertEqual(service.store.operation(accepted.operation_id).error, 'MODEL_TIMEOUT')
            self.assertFalse(service.executions)
            self.records.append({'completed_report_preserved': True, 'cancelled_not_failed': True,
                'pending_cleanup_closed': True, 'persisted_timeout_not_rewritten': True})

    def test_workspace_and_checkpoint_future_or_damaged_formats_are_not_repaired(self):
        for kind in ['future', 'schema', 'unknown_legacy']:
            path = self.root / (kind + '.sqlite3')
            if kind != 'unknown_legacy':
                Store(path).close()
            with closing(sqlite3.connect(path)) as db, db:
                if kind == 'future': db.execute('PRAGMA user_version=99')
                if kind == 'schema': db.execute('DROP TABLE events')
                if kind == 'unknown_legacy': db.execute('CREATE TABLE unrelated (id TEXT)')
            before = digest(path)
            with self.assertRaises(ValueError): Store(path)
            self.assertEqual(digest(path), before)
            read_root = self.root / ('readonly-' + kind); read_root.mkdir()
            (read_root / 'workspace.sqlite3').write_bytes(path.read_bytes())
            with self.assertRaises(ValueError): SessionRepository(read_root).list()
        with TestClient(self.app()) as client:
            mid = self.h.start(client)
            self.h.wait(client, mid, 'BLOCKED')
            checkpoint = client.app.state.service.checkpoint_path
        with closing(sqlite3.connect(checkpoint)) as db, db:
            from langgraph.checkpoint.sqlite import SqliteSaver
            saver = SqliteSaver(db); saved = saver.get_tuple({'configurable': {'thread_id': mid}})
            altered = {**saved.checkpoint, 'v': 99}
            saver.put(saved.config, altered, saved.metadata, {})
        before = digest(checkpoint)
        with self.assertRaisesRegex(ValueError, '版本'): inspect_checkpoint(checkpoint, mid)
        self.assertEqual(digest(checkpoint), before)
        with closing(sqlite3.connect(checkpoint)) as db, db: db.execute('PRAGMA user_version=99')
        before = digest(checkpoint)
        with self.assertRaisesRegex(ValueError, '版本'): inspect_checkpoint(checkpoint, mid)
        self.assertEqual(digest(checkpoint), before)
        self.records.append({'unsupported_formats_refused_without_rewrite': True})

    def test_old_pass_and_completed_flags_do_not_authorize_publication(self):
        from backend.call_limits import execution_scope
        from backend.runtime_contracts import ExecutionIdentity, OperationStatus
        with TestClient(self.app()) as client:
            service = client.app.state.service
            with patch.object(service, '_enqueue'):
                accepted = service.submit(MessageRequest(request_id='legacy-flags', conversation_id='conv_session', content='legacy candidate'))
            op = service.store.operation(accepted.operation_id); op.status = OperationStatus.RUNNING; service.store.save_operation(op)
            state = {'execution_outcome': 'SUCCEEDED', 'delivery_status': 'PUBLISHABLE', 'final_report': 'legacy candidate',
                'review_passed': True, 'review': {'decision': 'PASS'}, 'plan': {'subtasks': [{'id': 'x', 'status': 'completed'}]}}
            with execution_scope(service.policy, time.monotonic() + 10, identity=ExecutionIdentity(op.id, op.run_id)):
                service.complete(service.store.get(accepted.mission_id), 'legacy candidate', state=state)
            self.assertIsNone(service.store.report(accepted.mission_id))
            self.assertEqual(service.store.get(accepted.mission_id).status, 'FAILED')
            self.records.append({'legacy_labels_cannot_publish': True})

    def test_nonempty_history_memory_and_feedback_survive_both_pause_restarts(self):
        from evaluation import test_feedback_followups as feedback
        from evaluation.runtime_fixture import run_pending
        helper = feedback.FeedbackFollowupTests()
        helper.setUp(); self.addCleanup(helper.tearDown)
        with patch('backend.runtime.create_runtime', side_effect=helper.harness.runtime):
            with TestClient(helper.app()) as client:
                service = helper.prepare(client)
                response = client.post(f'/api/missions/{helper.identity}/career-memory/user-records', json={
                    'context': helper.context.to_dict(), 'request_id': 'fixed-goal', 'operation': 'create',
                    'confirmed': True, 'kind': 'goal', 'content': '速度训练每天20分钟'})
                self.assertEqual(response.status_code, 200, response.text)
                helper.harness.require_input = True
                with patch.object(service, '_enqueue'):
                    body = helper.body(); body['include_report'] = True
                    parent = inspect_checkpoint(service.checkpoint_path, helper.identity).values
                    body['selected_results'] = [{'subtask_id': 'training', 'version': parent['subtask_results']['training']['source_version']}]
                    response = client.post('/api/mission-continuations', json=body)
                    self.assertEqual(response.status_code, 202, response.text)
                mid = response.json()['mission_id']
                run_pending(service, mid)
                helper.harness.wait(client, mid, 'BLOCKED')
                fixed = service.store.inputs(mid)
                self.assertTrue(fixed['career_memory_context']['items'])
                self.assertTrue(fixed['continuation_context']['career_feedback'])
                self.assertTrue(fixed['continuation_context'])
            helper.add('new-profile', '2026-09-07', {'attributes': {'physical': {'speed': 99}}}, kind='profile_patch')
            for pause, values in [('missing_user_input', {'information': 'fixed answer'}), ('report_approval', {'approved': True})]:
                with TestClient(helper.app()) as client, patch.object(helper.repository, 'read_snapshot',
                        side_effect=AssertionError('No latest A during recovery')):
                    service = client.app.state.service
                    mission = service.view(service.store.get(mid))
                    self.assertEqual(mission.blocked.reason, pause)
                    self.assertIsNone(mission.resume_error)
                    self.assertEqual(service.store.inputs(mid), fixed)
                    service.input(mid, InputRequest(request_id='recover-' + pause, pause_id=mission.blocked.pause_id, values=values))
                    helper.harness.wait(client, mid, 'BLOCKED' if pause == 'missing_user_input' else 'COMPLETED')
                    self.assertEqual(service.store.inputs(mid), fixed)
            self.records.append({'nonempty_memory': True, 'nonempty_selected_history': True,
                'nonempty_feedback': True, 'both_pause_restarts_preserve_all_fixed_inputs': True,
                'latest_player_changed_without_reread': True})


class ProcessRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.environ['FAIT_M46_TEST_ROOT']) / self._testMethodName
        self.root.mkdir(parents=True, exist_ok=False)
        self.records = []
        self.addCleanup(lambda: dump(self.root / 'evidence.json', self.records))

    def wait_file(self, path, owner, seconds=110):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if path.exists(): return json.loads(path.read_text(encoding='utf-8'))
            if owner.process.poll() is not None: self.fail('Owner exited before lifecycle gate')
            time.sleep(.02)
        self.fail('Lifecycle gate timeout')

    def reopen(self, root):
        return MissionService(root / 'tasks', player_repository=FixtureRepository(root / 'player'),
            runner=ProcessRunner('evaluation.m46_worker'))

    def test_live_checkpoint_writer_refuses_restart_before_database_mutation(self):
        root = self.root
        write_fixture(root / 'player', speed=44)
        self.reopen(root).close()
        before = digest(root / 'tasks/workspace.sqlite3')
        with OwnedProcess('evaluation.m46_owner', root, 'lease', 'kill', root=REPO) as owner:
            self.wait_file(root / 'ready.json', owner)
            started = time.monotonic()
            with self.assertRaisesRegex(ValueError, 'checkpoint 写入者'):
                MissionService(root / 'tasks', player_repository=FixtureRepository(root / 'player'),
                    runtime_policy=replace(RuntimePolicy(), cleanup_seconds=.15))
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 1)
            self.assertEqual(digest(root / 'tasks/workspace.sqlite3'), before)
            owner.process.kill(); owner.wait(3)
        self.reopen(root).close()
        self.records.append({'refused_seconds': elapsed, 'database_unchanged': True, 'release_allows_startup': True})

    def test_real_owner_kill_restart_lifecycle_matrix(self):
        phases = ['created', 'queued', 'manager', 'coach', 'tool', 'reviewer', 'document',
            'publish_transaction', 'published', 'missing_user_input', 'report_approval', 'explanation']
        for phase in phases:
            with self.subTest(phase=phase):
                root = self.root / phase
                root.mkdir()
                with (root / 'owner.log').open('wb') as log, OwnedProcess('evaluation.m46_owner', root, phase, 'kill',
                        root=REPO, output=log) as owner:
                    ready = self.wait_file(root / 'ready.json', owner)
                    owner.process.kill(); owner.wait(3)  # keep outer Job open; inner owner must clean its own worker
                    with closing(self.reopen(root)) as service:
                        op = service.store.operation(ready['operation_id'])
                        mission = service.store.get(ready['mission_id'])
                        if phase in {'missing_user_input', 'report_approval'}:
                            self.assertEqual((op.status, mission.status), ('PAUSED', 'BLOCKED'))
                            self.assertIsNone(service.view(mission).resume_error)
                        elif phase in {'published', 'explanation'}:
                            self.assertEqual(mission.status, 'COMPLETED')
                            self.assertTrue(service.store.report(mission.id))
                            self.assertEqual(op.status, 'COMPLETED' if phase == 'published' else 'FAILED')
                        else:
                            self.assertEqual((op.status, mission.status), ('FAILED', 'FAILED'))
                            self.assertEqual(op.error, 'INTERRUPTED')
                            self.assertIsNone(service.store.report(mission.id))
                        self.assertFalse(service.active)
                        for item in service.store.operations(): self.assertNotIn(item.status, {'QUEUED', 'RUNNING'})
                        if ready['worker_pid']:
                            with process_handle(ready['worker_pid']) as dead: self.assertTrue(dead(1500))
                        before = service.store.inputs(mission.id)
                        replay = service.submit(MessageRequest(request_id='original-request', conversation_id='m46', content='m46:' + phase))
                        self.assertTrue(replay.replayed)
                        self.assertEqual(service.store.inputs(mission.id), before)
                        if phase in {'missing_user_input', 'report_approval'}:
                            write_fixture(root / 'player', speed=99)
                            while service.store.get(mission.id).status == 'BLOCKED':
                                paused = service.store.get(mission.id)
                                reason = paused.blocked.reason
                                service.input(mission.id, InputRequest(request_id='recovered-' + reason, pause_id=paused.blocked.pause_id,
                                    values={'approved': True} if reason == 'report_approval' else {'information': 'original answer'}))
                                end = time.monotonic() + 60
                                while service.active and time.monotonic() < end: time.sleep(.02)
                                self.assertFalse(service.active)
                            self.assertEqual(service.store.get(mission.id).status, 'COMPLETED')
                            self.assertIn('原球员速度：44', service.store.report(mission.id))
                            self.assertEqual(service.store.inputs(mission.id), before)
                            documents = [e for e in service.store.events(mission.id, 0)
                                if e['type'] == 'agent.started' and e['data'].get('agent') == 'Document']
                            self.assertEqual(len(documents), 1)
                        self.records.append({**ready, 'restart_mission': mission.status, 'restart_operation': op.status,
                            'error': op.error, 'worker_exited': True, 'no_automatic_replay': True})

    def test_normal_shutdown_is_separate_from_kill_and_preserves_pauses_and_delivery(self):
        for phase in ['tool', 'missing_user_input', 'report_approval', 'completed']:
            with self.subTest(phase=phase):
                root = self.root / phase; root.mkdir()
                with (root / 'owner.log').open('wb') as log, OwnedProcess('evaluation.m46_owner', root, phase, 'normal',
                        root=REPO, output=log) as owner:
                    ready = self.wait_file(root / 'ready.json', owner)
                    self.wait_file(root / 'closed.json', owner)
                    self.assertEqual(owner.wait(5), 0)
                with closing(self.reopen(root)) as service:
                    op = service.store.operation(ready['operation_id'])
                    expected = 'FAILED' if phase == 'tool' else 'COMPLETED' if phase == 'completed' else 'PAUSED'
                    self.assertEqual(op.status, expected)
                    if phase == 'tool': self.assertEqual(op.error, 'SERVICE_SHUTDOWN')
                    self.records.append({**ready, 'normal_shutdown_status': op.status, 'error': op.error})
