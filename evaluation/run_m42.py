"""M4.2 reproducible HTTP/SQLite/checkpoint/CLI request identity verification."""
import argparse
import os
from pathlib import Path
import time
import unittest

from evaluation.m4_audit import dump, digest
from evaluation.run_m40 import protected, REPO


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--full', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_relative_to(REPO / 'local_data/game_imports/m4-implementation'):
        raise ValueError('Use a new isolated implementation root')
    before = protected(root)
    root.mkdir(parents=True, exist_ok=False)
    (root / 'temp').mkdir()
    os.environ.update(TEMP=str(root / 'temp'), TMP=str(root / 'temp'), PYTHONUTF8='1',
        HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    dump(root / 'baseline.json', {'protected': before})
    modules = ['test_m42_requests']
    if args.full:
        modules += ['test_session_semantics', 'test_reevaluation', 'test_web', 'test_memory_retrieval',
            'test_cli_persistence', 'test_recommendation_actions', 'test_recommendations', 'test_execution_outcomes',
            'test_career_memory', 'test_assessments', 'test_finishing_assessment', 'test_feedback_followups',
            'test_player_imports', 'test_player_repository', 'test_player_data_boundary', 'test_result_invalidation',
            'test_dependency_context', 'test_looping_plan', 'test_agent_output_validation', 'test_stage3_audit']
    records = []
    class Recorded(unittest.TextTestResult):
        def startTest(self, test):
            self.started = time.monotonic()
            super().startTest(test)
        def stopTest(self, test):
            records.append({'test': test.id(), 'seconds': time.monotonic() - self.started})
            super().stopTest(test)
    suite = unittest.defaultTestLoader.loadTestsFromNames(['evaluation.' + m for m in modules])
    with (root / 'unittest.log').open('w', encoding='utf-8') as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=Recorded).run(suite)
    after = protected(root)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    sources = ['backend/models.py', 'backend/store.py', 'backend/service.py', 'backend/events.py', 'backend/demo.py',
        'backend/main.py', 'backend/cli.py', 'app.py', 'utils/sessions.py', 'evaluation/request_fixtures.py',
        'evaluation/test_m42_requests.py', 'evaluation/cli_process_fixture.py', 'evaluation/run_m42.py']
    summary = {'scope': 'M4.2 request identities, operations, pause versions and existing regressions',
        'full': args.full, 'tests': result.testsRun, 'passed_gate': result.wasSuccessful() and not result.skipped and not changed,
        'failures': [(t.id(), s) for t, s in result.failures], 'errors': [(t.id(), s) for t, s in result.errors],
        'skips': [(t.id(), s) for t, s in result.skipped], 'cases': records,
        'protected_file_count': len(before), 'protected_changed': changed,
        'source_sha256': {p: digest(REPO / p) for p in sources}}
    dump(root / 'summary.json', summary)
    print({k: v for k, v in summary.items() if k not in ['cases', 'source_sha256']}, flush=True)
    return int(not summary['passed_gate'])


if __name__ == '__main__':
    raise SystemExit(main())
