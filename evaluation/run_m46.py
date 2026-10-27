"""M4.6 restart and format compatibility gate and optional full offline regressions."""
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
        HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', FAIT_M43_TEST_ROOT=str(root / 'm43'),
        FAIT_M40_TEST_ROOT=str(root / 'm40'), FAIT_M43_EXTRA_ROOT=str(root / 'admission'), FAIT_M44_TEST_ROOT=str(root / 'm44'), FAIT_M45_TEST_ROOT=str(root / 'm45'), FAIT_M46_TEST_ROOT=str(root / 'm46'))
    dump(root / 'baseline.json', {'protected': before})
    modules = ['test_m46_recovery', 'test_m46_migrations', 'test_m46_finalization']
    if args.full:
        modules += ['test_m45_metering', 'test_m44_cancellation', 'test_m44_cli', 'test_reevaluation', 'test_m43_runtime', 'test_m40_runtime', 'test_m43_admission', 'test_m42_requests', 'test_session_semantics', 'test_web', 'test_memory_retrieval',
            'test_cli_persistence', 'test_recommendation_actions', 'test_recommendations', 'test_execution_outcomes',
            'test_career_memory', 'test_assessments', 'test_finishing_assessment', 'test_feedback_followups',
            'test_player_imports', 'test_player_repository', 'test_player_data_boundary', 'test_result_invalidation',
            'test_dependency_context', 'test_looping_plan', 'test_agent_output_validation', 'test_stage3_audit',
            'test_m41_writer_lock']
        os.environ['FAIT_M41_TEST_ROOT'] = str(root / 'm41')
    records = []
    class Recorded(unittest.TextTestResult):
        def startTest(self, test):
            self.started = time.monotonic()
            super().startTest(test)
        def stopTest(self, test):
            records.append({'test': test.id(), 'seconds': time.monotonic() - self.started})
            super().stopTest(test)
    suite = unittest.defaultTestLoader.loadTestsFromNames(['evaluation.' + name for name in modules])
    started = time.monotonic()
    with (root / 'unittest.log').open('w', encoding='utf-8') as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=Recorded).run(suite)
    after = protected(root)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    sources = ['backend/storage_contracts.py', 'backend/task_context.py', 'backend/runtime.py', 'backend/writer_lock.py', 'evaluation/m46_worker.py', 'evaluation/m46_owner.py', 'evaluation/test_m46_recovery.py', 'evaluation/test_m46_migrations.py', 'evaluation/test_m46_finalization.py', 'evaluation/run_m46.py', 'backend/operation_metrics.py', 'utils/telemetry.py', 'evaluation/m45_worker.py', 'evaluation/test_m45_metering.py', 'evaluation/run_m45.py', 'backend/continuations.py', 'backend/store.py', 'backend/events.py', 'app.py', 'utils/sessions.py', 'career_actions/application.py', 'evaluation/test_m44_cancellation.py', 'evaluation/test_m44_cli.py', 'evaluation/m44_api_fixture.py', 'evaluation/run_m44.py', 'evaluation/test_memory_retrieval.py', 'evaluation/test_feedback_followups.py', 'evaluation/test_reevaluation.py', 'backend/call_limits.py', 'backend/execution_worker.py', 'backend/process_runner.py',
        'backend/process_supervisor.py', 'backend/runtime_contracts.py', 'backend/task_queue.py', 'backend/service.py',
        'backend/main.py', 'backend/models.py', 'backend/demo.py', 'agents/base.py', 'utils/helpers.py', 'tools/search.py',
        'evaluation/runtime_fixture.py', 'evaluation/m43_worker.py', 'evaluation/m43_owner_probe.py',
        'evaluation/test_m43_runtime.py', 'evaluation/test_session_semantics.py', 'evaluation/test_web.py',
        'evaluation/cli_process_fixture.py', 'evaluation/run_m43.py', 'evaluation/test_m43_admission.py']
    summary = {'scope': 'M4.6 restart policy and current-format compatibility; M4 overall not passed',
        'full': args.full, 'tests': result.testsRun, 'passed_gate': result.wasSuccessful() and not result.skipped and not changed,
        'seconds': time.monotonic() - started, 'failures': [(t.id(), s) for t, s in result.failures],
        'errors': [(t.id(), s) for t, s in result.errors], 'skips': [(t.id(), s) for t, s in result.skipped], 'cases': records,
        'protected_file_count': len(before), 'protected_changed': changed,
        'source_sha256': {name: digest(REPO / name) for name in sources}}
    dump(root / 'summary.json', summary)
    print({k: v for k, v in summary.items() if k not in ['cases', 'source_sha256']}, flush=True)
    return int(not summary['passed_gate'])


if __name__ == '__main__':
    raise SystemExit(main())
