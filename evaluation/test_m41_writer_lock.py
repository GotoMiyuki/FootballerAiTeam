"""M4.1 true process contention: no lost observations and collector batch ownership."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import time
import unittest

from backend.process_supervisor import OwnedProcess
from player_data.imports import ObservationImporter
from player_data.writer_lock import actual_writer
from evaluation import test_player_imports as import_fixtures

REPO = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt', 'This process fixture uses the verified Windows Job supervisor')
class ActualWriterProcessTests(unittest.TestCase):
    def setUp(self):
        base = os.environ.get('FAIT_M41_TEST_ROOT')
        if not base:
            self.skipTest('Run using evaluation.run_m41 for isolated persistent evidence')
        self.root = Path(base).resolve() / self._testMethodName
        self.root.mkdir(parents=True, exist_ok=False)
        self.log = (self.root / 'process.log').open('w', encoding='utf-8')
        self.records = []
        from player_data.json_repository import JsonPlayerRepository
        from player_data.models import PlayerContext
        self.context = PlayerContext('m41-career', 'main', 'player')
        self.repo = JsonPlayerRepository(self.root / 'actual', fixture_root=self.root / 'fixture', context=self.context)
        self.addCleanup(self.finish)

    def finish(self):
        (self.root / 'evidence.json').write_text(json.dumps(self.records, ensure_ascii=False, indent=2), encoding='utf-8')
        self.log.close()

    def launch(self, mode, label, expected='EMPTY', **options):
        arguments = [mode, self.root, label, '--expected', expected]
        for key, value in options.items():
            arguments.extend(['--' + key, str(value)])
        worker = OwnedProcess('evaluation.m41_lock_probe', *arguments, root=REPO, output=self.log)
        self.records.append({'pid': worker.pid, 'mode': mode, 'label': label, 'command': worker.command})
        return worker

    def read_result(self, worker, label):
        self.assertEqual(worker.wait(30), 0)
        result = json.loads((self.root / (label + '.result.json')).read_text(encoding='utf-8'))
        self.assertEqual(worker.pid, result['pid'])
        self.records.append(result)
        return result['result']

    def run_child(self, mode, label, expected='EMPTY', **options):
        with self.launch(mode, label, expected, **options) as worker:
            return self.read_result(worker, label)

    def ready(self, worker, label):
        end = time.monotonic() + 30
        while time.monotonic() < end:
            p = self.root / (label + '.ready.json')
            if p.exists():
                result = json.loads(p.read_text(encoding='utf-8'))
                self.assertEqual(result['pid'], worker.pid)
                return
            if worker.process.poll() is not None:
                self.fail('Own worker exited; see process.log')
            time.sleep(.02)
        self.fail('Own writer readiness exceeded 30s')

    def test_two_processes_cannot_lose_confirmed_observations(self):
        seed = self.run_child('import', 'seed')
        with self.launch('hold-import', 'writer_a', seed['state_version']) as first:
            self.ready(first, 'writer_a')
            rejected = self.run_child('import', 'writer_b', seed['state_version'])
            self.assertEqual((rejected['status'], rejected['reason']), ('CONFLICT', 'writer_busy'))
            reader = self.run_child('read', 'reader')
            self.assertEqual(reader['snapshot']['metadata']['state_version'], seed['state_version'])
            (self.root / 'writer_a.release').touch()
            applied = self.read_result(first, 'writer_a')
        stale = self.run_child('import', 'writer_b', seed['state_version'])
        self.assertEqual((stale['status'], stale['reason']), ('CONFLICT', 'stale_state_version'))
        final = self.run_child('import', 'writer_b', applied['state_version'])
        self.assertEqual(final['status'], 'APPLIED')
        ids = {o['observation_id'] for o in self.repo.read_raw()['observations']}
        self.assertEqual(ids, {'seed', 'writer_a', 'writer_b'})
        self.records.append({'observations': sorted(ids), 'lost_update': False, 'reader_during_write': True})

    def test_different_task_dirs_and_contexts_still_share_actual_lock(self):
        seed = self.run_child('import', 'seed')
        with self.launch('hold-import', 'owner', seed['state_version'], tasks='web-tasks') as owner:
            self.ready(owner, 'owner')
            other = self.run_child('import', 'other_branch', branch='other', tasks='cli-tasks')
            self.assertEqual(other['reason'], 'writer_busy')
            self.assertTrue((self.root / 'web-tasks/workspace.sqlite3').is_file())
            self.assertTrue((self.root / 'cli-tasks/workspace.sqlite3').is_file())
            (self.root / 'owner.release').touch()
            self.assertEqual(self.read_result(owner, 'owner')['status'], 'APPLIED')
        self.assertEqual(self.run_child('import', 'other_branch', branch='other', tasks='cli-tasks')['status'], 'APPLIED')

    def test_collector_keeps_lock_through_receipt_and_nested_imports(self):
        with self.launch('collector-hold', 'collector') as owner:
            self.ready(owner, 'collector')
            current = self.repo.read_snapshot().metadata['state_version']
            self.assertEqual(self.run_child('import', 'external', current)['reason'], 'writer_busy')
            self.assertEqual(self.run_child('bind', 'second_binding')['reason'], 'writer_busy')
            (self.root / 'collector.release').touch()
            result = self.read_result(owner, 'collector')
        self.assertEqual(result['status'], 'COMPLETED')
        self.assertEqual(len(self.repo.read_raw()['observations']), 5)
        self.assertEqual(len(list((self.root / 'actual/collectors/pes2021_native/receipts').glob('*.json'))), 1)
        self.assertEqual(self.run_child('bind', 'second_binding')['status'], 'DUPLICATE')

    def test_killed_writer_releases_root_and_task_locks(self):
        seed = self.run_child('import', 'seed')
        owner = self.launch('hold-import', 'interrupted', seed['state_version'], tasks='tasks')
        try:
            self.ready(owner, 'interrupted')
        finally:
            owner.close()
        self.assertEqual(self.repo.read_snapshot().metadata['state_version'], seed['state_version'])
        self.assertEqual(self.run_child('import', 'after_kill', seed['state_version'], tasks='tasks')['status'], 'APPLIED')
        ids = {o['observation_id'] for o in self.repo.read_raw()['observations']}
        self.assertEqual(ids, {'seed', 'after_kill'})
        self.records.append({'interrupted_not_committed': True, 'root_and_task_locks_reacquired': True})


class ActualWriterReentrancyTests(unittest.TestCase):
    def test_collector_style_nested_owner_and_competing_thread(self):
        h = import_fixtures.PlayerImportTests()
        h.setUp()
        try:
            with actual_writer(h.repository.root), actual_writer(h.repository.root / '.'):
                self.assertEqual(h.importer.import_observation(h.observation()).status, 'APPLIED')
                def contender():
                    return h.importer.import_observation(h.observation('second')).reason
                with ThreadPoolExecutor(max_workers=1) as pool:
                    self.assertEqual(pool.submit(contender).result(timeout=2), 'writer_busy')
            snapshot = h.repository.read_snapshot()
            self.assertEqual(snapshot.profile['name'], 'Actual Test Player')
            next_observation = replace(h.observation('second'), expected_state_version=snapshot.metadata['state_version'])
            self.assertEqual(ObservationImporter(h.repository, source_type='game_observation', provider='collector')
                             .import_observation(next_observation).status, 'RECORDED_ONLY')
        finally:
            h.tearDown()


if __name__ == '__main__':
    unittest.main()
