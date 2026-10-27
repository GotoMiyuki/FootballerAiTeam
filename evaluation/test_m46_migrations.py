"""Small, reproducible legacy workspace upgrades and refusal-before-write checks."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import unittest

from backend.models import MissionView
from backend.store import Store
from evaluation.m4_audit import digest, dump
from utils.sessions import SessionRepository


class LegacyLayoutTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.environ['FAIT_M46_TEST_ROOT']) / self._testMethodName
        self.root.mkdir(parents=True, exist_ok=False)
        self.records = []
        self.addCleanup(lambda: dump(self.root / 'evidence.json', self.records))

    def test_incompatible_existing_optional_tables_refuse_before_legacy_upgrade(self):
        for table in ['operations', 'request_receipts']:
            root = self.root / table; root.mkdir()
            path = root / 'workspace.sqlite3'
            Store(path).close()
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript(f'DROP TABLE {table}; CREATE TABLE {table}(id TEXT); PRAGMA user_version=0;')
            before = digest(path)
            with self.assertRaisesRegex(ValueError, '结构不兼容'): Store(path)
            self.assertEqual(digest(path), before)
            with self.assertRaisesRegex(ValueError, '结构不兼容'): SessionRepository(root).list()
            self.assertEqual(digest(path), before)
            self.records.append({'table': table, 'refused_before_upgrading': True, 'bytes_unchanged': True})

    def test_known_legacy_and_compatible_partial_layouts_upgrade_without_losing_rows(self):
        for keep in [[], ['operations'], ['request_receipts'], ['operations', 'request_receipts']]:
            path = self.root / ('keep-' + '-'.join(keep) + '.sqlite3')
            Store(path).close()
            mission = MissionView(id='legacy-complete', conversation_id='old', title='old', objective='old',
                created_at='2026-10-01T00:00:00+00:00', status='COMPLETED').model_dump_json()
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('INSERT INTO missions VALUES (?,?,?)', ('legacy-complete', mission, '# original report'))
                db.execute('INSERT INTO mission_inputs VALUES (?,?)', ('legacy-complete', json.dumps({'original': 'fixed'})))
                for table in ['operations', 'request_receipts']:
                    if table not in keep: db.execute(f'DROP TABLE {table}')
                db.execute('PRAGMA user_version=0')
            with closing(Store(path)) as store:
                self.assertEqual(store.db.execute('PRAGMA user_version').fetchone()[0], 1)
                self.assertEqual(store.get('legacy-complete').objective, 'old')
                self.assertEqual(store.report('legacy-complete'), '# original report')
                self.assertEqual(store.inputs('legacy-complete'), {'original': 'fixed'})
                self.assertEqual(store.operations(), [])
            self.records.append({'existing_optional_tables': keep, 'upgraded_to': 1, 'original_rows_preserved': True})
