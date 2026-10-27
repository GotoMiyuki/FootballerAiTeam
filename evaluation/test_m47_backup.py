"""M4.7 stopped-writer snapshots, candidate restore and real-format source relocation."""
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.backup import backup, restore, verify, inventory, load, save
from backend.main import create_app
from backend.models import MessageRequest, InputRequest
from backend.storage_contracts import checkpoint_writer
from backend.writer_lock import WorkspaceWriterLock
from evaluation import test_session_semantics as semantics
from evaluation.runtime_fixture import InlineRunner
from evaluation.player_fixtures import write_fixture
from evaluation.m4_audit import dump
from game_adapters.source_paths import directory_files, file_hash, tree_hash, SourceMap
from player_data.imports import Observation, ObservationImporter
from player_data.json_repository import JsonPlayerRepository, EMPTY_VERSION
from player_data.models import PlayerContext, content_hash
from player_data.writer_lock import actual_writer

REPO = Path(__file__).resolve().parents[1]


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.environ['FAIT_M47_TEST_ROOT']) / self._testMethodName
        self.root.mkdir(parents=True, exist_ok=False)
        self.source = self.root / 'source'; self.source.mkdir()
        self.h = semantics.SessionSemanticsTests()
        self.h.root, self.h.fixture = self.source, self.source / 'fixture'
        write_fixture(self.h.fixture, speed=44)
        self.context = PlayerContext('m47-career', 'main', 'player')
        self.other = PlayerContext('m47-career', 'other', 'other-player')
        self.actual = self.source / 'actual'
        self.repo = JsonPlayerRepository(self.actual, fixture_root=self.h.fixture, context=self.context)
        self.raw = self.source / 'raw.json'; save(self.raw, {'source': 'offline fixture', 'speed': 44})
        self.importer = ObservationImporter(self.repo, source_type='game_observation', provider='m47')
        for context, speed in [(self.context, 44), (self.other, 55)]:
            result = self.importer.import_observation(Observation(context, 'profile', 'game_observation', 'm47',
                '2026-10-27T00:00:00+00:00', '2026-10-01', 'game', 'day', 'profile_patch',
                {'name': 'Backup fixture', 'attributes': {'physical': {'speed': speed}}}, EMPTY_VERSION,
                source_record_id='profile', game_version='fixture-v1', adapter_version='fixture-v1', raw_reference='capture:m47'))
            self.assertEqual(result.status, 'APPLIED')
        self.h.repository, self.h.calls, self.h.require_input = self.repo, [], False
        self.h.wait = self.settled
        self.runtime = patch('backend.runtime.create_runtime', side_effect=self.h.runtime); self.runtime.start(); self.addCleanup(self.runtime.stop)
        self.web, self.cli = self.source / 'web', self.source / 'cli'
        self.records = []
        self.addCleanup(lambda: dump(self.root / 'evidence.json', self.records))
        self.prepare()
        self.spec = {'schema_version': 1, 'actual_root': str(self.actual),
            'tasks': {'web': str(self.web), 'cli': str(self.cli)}, 'file_sources': {'capture:m47': str(self.raw)},
            'settings': {'MODEL_NAME': 'offline'}, 'legacy_metadata': {'sessions': str(self.source / 'legacy.json')}}
        save(self.source / 'legacy.json', [{'thread_id': 'old-index', 'first_input': 'metadata only'}])
        knowledge = self.source / 'knowledge'; knowledge.mkdir(); (knowledge / 'training.txt').write_text('fixture corpus', encoding='utf-8')
        self.spec['assets'] = {'knowledge': str(knowledge)}

    def app(self, tasks=None, repository=None, context=None):
        return create_app(data_dir=tasks or self.web, player_repository=repository or self.repo,
            player_context=context or self.context, demo=False, runner=InlineRunner())

    def good(self, response, status=200):
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def settled(self, client, identity, status):
        end = time.monotonic() + 60
        while time.monotonic() < end:
            if identity not in client.app.state.service.active:
                value = client.get(f'/api/missions/{identity}').json()
                if value['status'] == status: return value
                if value['status'] == 'FAILED' and status != 'FAILED': self.fail(value['error'])
            time.sleep(.01)
        self.fail('Offline graph did not reach ' + status)

    def prepare(self):
        with TestClient(self.app()) as client:
            self.complete = self.h.start(client); self.h.complete(client, self.complete)
            rec = self.good(client.get(f'/api/missions/{self.complete}/recommendations'))['items'][0]
            base = f'/api/missions/{self.complete}/recommendations/{rec["recommendation_id"]}'
            common = {'context': self.context.to_dict(), 'recommendation_revision': rec['revision']}
            self.good(client.post(base + '/decisions', json={**common, 'request_id': 'choice', 'status': 'ADOPTED', 'previous_decision_id': None}))
            self.good(client.post(base + '/executions', json={**common, 'request_id': 'executed', 'status': 'UNKNOWN',
                'actual_action': 'synthetic user report', 'window': {'time_domain': 'unknown'}}))
            plan = self.good(client.post(base + '/comparison-plans', json={**common, 'request_id': 'plan', 'confirmed': True,
                'metric': 'speed', 'window': {'kind': 'game_dates', 'start': '2026-10-02', 'end': '2026-10-03'}}))
            self.good(client.post(base + '/comparison-plans/' + plan['record']['plan_id'] + '/assessments', json={
                'context': self.context.to_dict(), 'request_id': 'assessment', 'expected_revision': 0}))
            self.good(client.post(f'/api/missions/{self.complete}/career-memory/user-records', json={
                'context': self.context.to_dict(), 'request_id': 'memory', 'operation': 'create',
                'confirmed': True, 'kind': 'goal', 'content': '速度训练每天20分钟'}))
            self.good(client.post(f'/api/missions/{self.complete}/career-memory/sync'))
            self.h.require_input = True
            self.missing = self.h.start(client); self.h.wait(client, self.missing, 'BLOCKED')
            self.h.require_input = False
            self.approval = self.h.start(client); self.h.wait(client, self.approval, 'BLOCKED')
            self.fixed = {mid: client.app.state.service.store.inputs(mid) for mid in [self.complete, self.missing, self.approval]}
        with TestClient(self.app(self.cli, context=self.other)) as client:
            mid = self.h.start(client); self.h.complete(client, mid)

    def archive(self, name='backup'):
        path = self.root / name
        result = backup(self.spec, path)
        self.assertEqual(result['status'], 'BACKUP_READY')
        return path

    def reseal(self, path):
        from backend.backup import archive_files
        manifest = load(path / 'manifest.json'); manifest['files'] = archive_files(path)
        save(path / 'manifest.json', manifest)
        save(path / 'READY.json', {'schema_version': 1, 'manifest_sha256': file_hash(path / 'manifest.json')})

    def test_complete_unit_preserves_contexts_reports_ec_and_both_pause_resumes(self):
        before = inventory(self.actual, {'web': self.web, 'cli': self.cli})[0]
        source_bytes = directory_files(self.actual, skip_suffixes=('.lock',))
        archive = self.archive(); manifest = verify(archive)
        self.assertEqual(len(manifest['inventory']['contexts']), 2)
        candidate = self.root / 'candidate'
        result = restore(archive, candidate)
        self.assertFalse(result['activated'])
        self.assertEqual((candidate / 'assets/knowledge/training.txt').read_text(), 'fixture corpus')
        tasks = {name: candidate / relative for name, relative in manifest['tasks'].items()}
        self.assertEqual(inventory(candidate / 'actual', tasks)[0], before)
        restored = JsonPlayerRepository(candidate / 'actual', fixture_root=self.h.fixture, context=self.context)
        version = restored.read_snapshot().metadata['state_version']
        changed = Observation(self.context, 'later', 'game_observation', 'm47', '2026-10-27T01:00:00+00:00',
            '2026-10-02', 'game', 'day', 'profile_patch', {'attributes': {'physical': {'speed': 99}}}, version,
            source_record_id='later', game_version='fixture-v1', adapter_version='fixture-v1', raw_reference='capture:m47')
        self.assertEqual(ObservationImporter(restored, source_type='game_observation', provider='m47').import_observation(changed).status, 'APPLIED')
        with TestClient(self.app(tasks['web'], restored)) as client, patch.object(restored, 'read_snapshot', side_effect=AssertionError('No latest during resume')):
            service = client.app.state.service
            for mid in [self.missing, self.approval]:
                while service.store.get(mid).status == 'BLOCKED':
                    mission = service.store.get(mid); reason = mission.blocked.reason
                    request = InputRequest(request_id='restore-' + mid + '-' + reason, pause_id=mission.blocked.pause_id,
                        values={'approved': True} if reason == 'report_approval' else {'information': 'original recovery'})
                    service.input(mid, request)
                    self.h.wait(client, mid, 'BLOCKED' if reason == 'missing_user_input' else 'COMPLETED')
                    self.assertTrue(service.input(mid, request).replayed)
                self.assertIn('原球员速度：44', service.store.report(mid))
                self.assertEqual(service.store.inputs(mid), self.fixed[mid])
        self.assertEqual(directory_files(self.actual, skip_suffixes=('.lock',)), source_bytes)
        second = self.root / 'candidate-two'; restore(archive, second)
        self.assertEqual(inventory(second / 'actual', {n: second / p for n,p in manifest['tasks'].items()})[0], before)
        with self.assertRaises(ValueError): restore(archive, candidate)
        self.records.append({'contexts': manifest['inventory']['contexts'], 'tables_preserved': before['tasks'],
            'both_pauses_resumed_original_44': True, 'repeat_input_deduplicated': True,
            'repeat_restore_new_directory_same_records': True, 'source_actual_unchanged': True})

    def test_busy_writers_are_refused_without_backup_or_source_mutation(self):
        for kind in ['workspace', 'checkpoint', 'actual']:
            with self.subTest(kind=kind):
                holder = {'workspace': lambda: closing(WorkspaceWriterLock(self.web)),
                    'checkpoint': lambda: checkpoint_writer(self.web / 'checkpoints.sqlite3'),
                    'actual': lambda: actual_writer(self.actual)}[kind]
                # Actual lock is deliberately held in another thread; same-thread nesting is legal.
                entered, release = threading.Event(), threading.Event()
                def own():
                    with holder(): entered.set(); release.wait(5)
                thread = threading.Thread(target=own); thread.start(); self.assertTrue(entered.wait(5))
                target = self.root / ('refused-' + kind)
                try:
                    started = time.monotonic()
                    with self.assertRaises(ValueError): backup(self.spec, target)
                    self.assertLess(time.monotonic() - started, 2)
                    self.assertFalse(target.exists())
                finally:
                    release.set(); thread.join(5)
                self.records.append({'held_lock': kind, 'refused': True, 'output_absent': True})

    def test_fault_candidates_refuse_before_activation_even_with_resealed_checksums(self):
        original = self.archive()
        for kind in ['missing-checkpoint', 'bad-sqlite', 'wrong-context', 'input-version', 'checkpoint-version',
                'missing-source', 'wrong-map', 'traversal', 'dependency-version', 'secret-setting', 'invalid-budget', 'ready-version']:
            with self.subTest(kind=kind):
                archive = self.root / kind; shutil.copytree(original, archive)
                manifest = load(archive / 'manifest.json')
                if kind == 'missing-checkpoint': (archive / 'tasks/web/checkpoints.sqlite3').unlink()
                if kind == 'bad-sqlite': (archive / 'tasks/web/workspace.sqlite3').write_bytes(b'bad SQLite')
                if kind == 'wrong-context':
                    state = next((archive / 'actual').glob('contexts/*/*/*/state.json'))
                    value = load(state); value['context']['branch_id'] = 'wrong'; save(state, value)
                if kind in {'input-version', 'checkpoint-version'}:
                    database = archive / ('tasks/web/workspace.sqlite3' if kind == 'input-version' else 'tasks/web/checkpoints.sqlite3')
                    with closing(sqlite3.connect(database)) as db, db:
                        if kind == 'checkpoint-version': db.execute('PRAGMA user_version=99')
                        else:
                            row = db.execute('SELECT payload FROM mission_inputs WHERE mission_id=?', (self.missing,)).fetchone()
                            value = json.loads(row[0]); value.pop('content_hash'); value['schema_version'] = 99
                            value['content_hash'] = content_hash(value)
                            db.execute('UPDATE mission_inputs SET payload=? WHERE mission_id=?', (json.dumps(value), self.missing))
                if kind == 'missing-source': (archive / manifest['file_sources'][0]['path']).unlink()
                if kind == 'wrong-map':
                    manifest['file_sources'][0]['path'] = 'files/absent'; save(archive / 'manifest.json', manifest)
                if kind == 'traversal':
                    manifest['tasks']['web'] = '../source/web'; save(archive / 'manifest.json', manifest)
                if kind == 'dependency-version':
                    manifest['dependencies']['langgraph'] = 'future'; save(archive / 'manifest.json', manifest)
                if kind == 'secret-setting':
                    manifest['settings']['OPENAI_API_KEY'] = 'dummy'; save(archive / 'manifest.json', manifest)
                if kind == 'invalid-budget':
                    manifest['settings']['FAIT_RUNTIME_QUEUE_CAPACITY'] = -1; save(archive / 'manifest.json', manifest)
                self.reseal(archive)
                if kind == 'ready-version':
                    ready = load(archive / 'READY.json'); ready['schema_version'] = 99; save(archive / 'READY.json', ready)
                target = self.root / ('candidate-' + kind)
                with self.assertRaises((ValueError, OSError, sqlite3.Error, KeyError)): restore(archive, target)
                self.assertFalse((target / 'RESTORE_READY.json').exists())
                self.records.append({'fault': kind, 'not_activated': True})

    def test_wal_is_included_using_sqlite_backup_not_raw_pair_copy(self):
        source = sqlite3.connect(self.web / 'workspace.sqlite3')
        try:
            source.execute('PRAGMA journal_mode=WAL'); source.execute('PRAGMA wal_autocheckpoint=0')
            source.execute('CREATE TABLE wal_probe (value TEXT)'); source.execute("INSERT INTO wal_probe VALUES ('committed in WAL')"); source.commit()
            self.assertGreater((self.web / 'workspace.sqlite3-wal').stat().st_size, 0)
            archive = self.archive()
            with closing(sqlite3.connect(archive / 'tasks/web/workspace.sqlite3')) as db:
                self.assertEqual(db.execute('SELECT value FROM wal_probe').fetchone()[0], 'committed in WAL')
            self.assertFalse((archive / 'tasks/web/workspace.sqlite3-wal').exists())
        finally: source.close()
        self.records.append({'nonempty_committed_wal_preserved': True, 'archive_standalone_sqlite': True})

    def test_cli_new_output_and_nonsecret_settings_boundary(self):
        spec = self.source / 'spec.json'; save(spec, self.spec)
        target = self.root / 'cli-backup'
        def run(*args):
            return subprocess.run([sys.executable, '-m', 'backend.backup', *map(str,args)], capture_output=True,
                text=True, encoding='utf-8', timeout=90, cwd=REPO)
        result = run('create', '--spec', spec, '--output', target)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(run('verify', target).returncode, 0)
        self.assertEqual(run('restore', target, '--output', self.root / 'cli-candidate').returncode, 0)
        self.assertNotEqual(run('restore', target, '--output', self.source).returncode, 0)
        secret = deepcopy(self.spec); secret['settings']['OPENAI_API_KEY'] = 'not-a-real-secret'
        with self.assertRaises(ValueError): backup(secret, self.root / 'secret-refused')
        with self.assertRaises(ValueError): restore(target, self.actual / 'nested-restore')
        with self.assertRaises(ValueError): backup(self.spec, self.source / 'knowledge/nested-backup')
        self.records.append({'cli_create_verify_restore': True, 'existing_target_refused': True, 'secret_setting_refused': True})

    def test_independent_process_writers_hold_the_entire_backup_boundary(self):
        from backend.process_supervisor import OwnedProcess
        for kind in ['workspace', 'checkpoint', 'actual']:
            root = self.actual if kind == 'actual' else self.web
            marker = self.root / ('ready-' + kind + '.json')
            with OwnedProcess('evaluation.m47_lock_probe', kind, root, marker, root=REPO) as owner:
                end = time.monotonic() + 30
                while not marker.exists() and time.monotonic() < end: time.sleep(.02)
                self.assertTrue(marker.exists())
                with self.assertRaises(ValueError): backup(self.spec, self.root / ('process-refused-' + kind))
                self.assertIsNone(owner.process.poll())
                self.records.append({'kind': kind, 'pid': owner.pid, 'other_writer_not_terminated': True})


class NativeArchiveTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.environ['FAIT_M47_TEST_ROOT']) / self._testMethodName
        self.root.mkdir(parents=True, exist_ok=False)
        # This initial read-only fixture clone is not the product backup implementation.
        # The historical M3 files and native originals must remain byte-for-byte untouched.
        self.historical = REPO / 'local_data/game_imports/m3-20261025'
        self.native = REPO / 'game_adapters/raw/real_test/m3-keyboard-resume-20261025'
        self.before = {'actual': directory_files(self.historical / 'actual'), 'tasks': directory_files(self.historical / 'tasks'),
            'native': directory_files(self.native)}
        self.seed = self.root / 'seed'
        shutil.copytree(self.historical / 'actual', self.seed / 'actual')
        shutil.copytree(self.historical / 'tasks', self.seed / 'tasks')
        shutil.copytree(self.native, self.seed / 'sources/real_test/native')
        mapping = self.seed / 'source-map.json'
        save(mapping, {'schema_version': 1, 'raw_root': 'sources', 'datasets': [{'original': str(self.native),
            'path': 'sources/real_test/native', 'tree_sha256': tree_hash(self.seed / 'sources/real_test/native')}]})
        self.spec = {'schema_version': 1, 'actual_root': str(self.seed / 'actual'), 'tasks': {'web': str(self.seed / 'tasks')},
            'source_map': str(mapping)}
        self.records = []; self.addCleanup(lambda: dump(self.root / 'evidence.json', self.records))
        self.addCleanup(self.originals_unchanged)

    def originals_unchanged(self):
        self.assertEqual(directory_files(self.historical / 'actual'), self.before['actual'])
        self.assertEqual(directory_files(self.historical / 'tasks'), self.before['tasks'])
        self.assertEqual(directory_files(self.native), self.before['native'])

    def test_real_native_sources_relocate_without_rewriting_observation_hashes(self):
        from game_adapters.pes2021_import import Collector
        from game_adapters.pes2021_native_package import read_package
        archive, candidate = self.root / 'archive', self.root / 'candidate'
        backup(self.spec, archive); manifest = verify(archive); restore(archive, candidate)
        repository = JsonPlayerRepository(candidate / 'actual', fixture_root=self.root / 'unused-fixture')
        state = next((candidate / 'actual').glob('contexts/*/*/*/state.json'))
        context = PlayerContext(**load(state)['context'])
        original_state, original_bindings = file_hash(state), file_hash(candidate / 'actual/collectors/pes2021_native/bindings.json')
        collector = Collector(repository, source_map=candidate / 'source-map.json')
        seen = []
        def mapped_read(path, **kwargs):
            self.assertTrue(Path(path).resolve().is_relative_to(candidate / 'sources'))
            seen.append(str(path))
            return read_package(path, **kwargs)
        with patch('game_adapters.pes2021_import.read_package', side_effect=mapped_read):
            preview = collector.preview(self.native)
            result = collector.commit(self.native, preview['preview_token'])
            self.assertEqual(result['status'], 'COMPLETED')
            self.assertTrue(all(r['status'] == 'DUPLICATE' for r in result['results']))
        self.assertEqual(file_hash(state), original_state)
        self.assertEqual(file_hash(candidate / 'actual/collectors/pes2021_native/bindings.json'), original_bindings)
        rebuilt = self.root / 'rebuilt-backup'
        backup({'schema_version': 1, 'actual_root': str(candidate / 'actual'),
            'tasks': {'web': str(candidate / 'tasks/web')}, 'source_map': str(candidate / 'source-map.json')}, rebuilt)
        verify(rebuilt)
        self.records.append({'contexts': manifest['inventory']['contexts'], 'files': len(manifest['files']),
            'mapped_native_reads': len(seen), 'observations': len(repository.read_state(context)['observations']),
            'original_observation_state_bytes_preserved': True, 'binding_bytes_preserved': True,
            'collector_replay_all_duplicate': True, 'restored_unit_can_be_backed_up_again': True})

    def test_missing_or_wrong_native_mapping_prevents_restore_and_collector_use(self):
        from game_adapters.pes2021_import import Collector
        archive, candidate = self.root / 'archive', self.root / 'candidate'
        backup(self.spec, archive); restore(archive, candidate)
        mapping = load(candidate / 'source-map.json')
        mapping['datasets'][0]['original'] = str(self.root / 'wrong-logical-name')
        save(candidate / 'source-map.json', mapping)
        repo = JsonPlayerRepository(candidate / 'actual', fixture_root=self.root / 'unused-fixture')
        with self.assertRaises(ValueError): Collector(repo, source_map=candidate / 'source-map.json').preview(self.native)
        manifest = load(archive / 'manifest.json'); manifest['native_datasets'][0]['tree_sha256'] = '0' * 64
        save(archive / 'manifest.json', manifest)
        save(archive / 'READY.json', {'schema_version': 1, 'manifest_sha256': file_hash(archive / 'manifest.json')})
        refused = self.root / 'refused-candidate'
        with self.assertRaises(ValueError): restore(archive, refused)
        self.assertFalse((refused / 'RESTORE_READY.json').exists())
        self.records.append({'wrong_logical_mapping_refused': True, 'wrong_native_digest_refused': True,
            'no_candidate_activation': True})
