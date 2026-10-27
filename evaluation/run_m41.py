"""M4.1 isolated writer tests and related A/D regressions; no game/API calls."""
import argparse
import json
import os
from pathlib import Path
import time
import unittest

from evaluation.m4_audit import dump, digest
from evaluation.run_m40 import protected, REPO


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    root = p.parse_args().root.resolve()
    if not root.is_relative_to(REPO / 'local_data/game_imports/m4-implementation'):
        raise ValueError('Use a new isolated implementation root')
    before = protected(root)
    root.mkdir(parents=True, exist_ok=False)
    (root / 'temp').mkdir()
    os.environ.update(TEMP=str(root / 'temp'), TMP=str(root / 'temp'), PYTHONUTF8='1',
        HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', FAIT_M41_TEST_ROOT=str(root / 'tests'))
    dump(root / 'baseline.json', {'protected': before})
    records = []
    class Recorded(unittest.TextTestResult):
        def startTest(self, test):
            self.started = time.monotonic()
            super().startTest(test)
        def stopTest(self, test):
            records.append({'test': test.id(), 'seconds': time.monotonic() - self.started})
            super().stopTest(test)
    suite = unittest.defaultTestLoader.loadTestsFromNames(['evaluation.test_m41_writer_lock',
        'evaluation.test_player_imports', 'evaluation.test_player_repository', 'evaluation.test_player_data_boundary',
        'game_adapters.test_pes2021_import'])
    with (root / 'unittest.log').open('w', encoding='utf-8') as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=Recorded).run(suite)
    after = protected(root)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    summary = {'scope': 'M4.1 root writer isolation + A/D regressions', 'tests': result.testsRun,
        'passed_gate': result.wasSuccessful() and not result.skipped and not changed,
        'failures': [(t.id(), s) for t, s in result.failures], 'errors': [(t.id(), s) for t, s in result.errors],
        'skips': [(t.id(), s) for t, s in result.skipped], 'cases': records,
        'protected_file_count': len(before), 'protected_changed': changed,
        'source_sha256': {str(p.relative_to(REPO)): digest(p) for p in [REPO / 'player_data/writer_lock.py',
            REPO / 'player_data/imports.py', REPO / 'game_adapters/pes2021_import.py',
            REPO / 'evaluation/m41_lock_probe.py', REPO / 'evaluation/test_m41_writer_lock.py', REPO / 'evaluation/run_m41.py']}}
    dump(root / 'summary.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k not in ['cases', 'source_sha256']}, ensure_ascii=False), flush=True)
    return int(not summary['passed_gate'])


if __name__ == '__main__':
    raise SystemExit(main())
