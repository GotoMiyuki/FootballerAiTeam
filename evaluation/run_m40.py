"""M4.0 reproducible contract and Windows isolation gate, entirely offline."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

from backend.runtime_contracts import RuntimePolicy
from evaluation.m4_audit import digest, dump, protected_hashes

REPO = Path(__file__).resolve().parents[1]


def protected(root):
    return {k: v for k, v in protected_hashes(root).items()
            if not k.replace('\\', '/').startswith('local_data/game_imports/m4-implementation/')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    root = Path(parser.parse_args().root).resolve()
    if not root.is_relative_to(REPO / 'local_data/game_imports/m4-implementation'):
        raise ValueError('Use a new isolated M4 implementation root')
    before = protected(root)
    root.mkdir(parents=True, exist_ok=False)
    (root / 'temp').mkdir()
    os.environ.update(TEMP=str(root / 'temp'), TMP=str(root / 'temp'), PYTHONUTF8='1',
        HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', FAIT_M40_TEST_ROOT=str(root / 'tests'))
    dump(root / 'baseline.json', {'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
        'status': subprocess.check_output(['git', 'status', '--porcelain'], cwd=REPO, text=True),
        'python': sys.version, 'protected': before, 'policy': asdict(RuntimePolicy.from_env())})
    records = []
    class Recorded(unittest.TextTestResult):
        def startTest(self, test):
            self.started = time.monotonic()
            super().startTest(test)
        def stopTest(self, test):
            records.append({'test': test.id(), 'seconds': time.monotonic() - self.started})
            super().stopTest(test)
    suite = unittest.defaultTestLoader.loadTestsFromName('evaluation.test_m40_runtime')
    started = time.monotonic()
    with (root / 'unittest.log').open('w', encoding='utf-8') as log:
        result = unittest.TextTestRunner(stream=log, resultclass=Recorded, verbosity=2).run(suite)
    after = protected(root)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    summary = {'scope': 'M4.0 contracts and Windows isolation only; live executor unchanged',
        'tests': result.testsRun, 'passed_gate': result.wasSuccessful() and not result.skipped and not changed,
        'failures': [(t.id(), s) for t, s in result.failures], 'errors': [(t.id(), s) for t, s in result.errors],
        'skips': [(t.id(), s) for t, s in result.skipped], 'cases': records,
        'seconds': time.monotonic() - started, 'protected_file_count': len(before), 'protected_changed': changed,
        'source_sha256': {str(p.relative_to(REPO)): digest(p) for p in [REPO / 'backend/runtime_contracts.py',
            REPO / 'backend/process_supervisor.py', REPO / 'evaluation/m4_process_probe.py',
            REPO / 'evaluation/test_m40_runtime.py', REPO / 'evaluation/run_m40.py']}}
    dump(root / 'summary.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k not in ['cases', 'source_sha256']}, ensure_ascii=False), flush=True)
    return int(not summary['passed_gate'])


if __name__ == '__main__':
    raise SystemExit(main())
