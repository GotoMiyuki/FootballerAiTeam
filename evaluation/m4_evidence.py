"""Export compact, reviewable evidence; no live databases opened for writing."""
import json
from pathlib import Path
import subprocess
import sys
from evaluation.m4_audit import REPO, digest, dump, protected_hashes


def main():
    base = REPO / 'local_data/game_imports/m4-audit'
    def read(name):
        return json.loads((base / name).read_text(encoding='utf-8'))
    original = read('20261026-01/baseline.json')
    current = protected_hashes(base)
    core = read('20261026-01/core/core.json')
    process = read('20261026-03/processes/processes.json')
    restore = read('20261026-restore-01/restore.json')
    transport = read('20261026-http-01/transport.json')
    reg = read('20261026-01/regressions/regressions.json')
    extra = read('20261026-extra-01/regressions-extra/regressions.json')
    tracked = subprocess.check_output(['git', 'ls-files'], cwd=REPO, text=True).splitlines()
    source_hashes = {n: digest(REPO / n) for n in tracked if (REPO / n).is_file() and
        (n.endswith(('.py', '.ts', '.tsx')) or n in ['frontend/package.json', 'frontend/package-lock.json', 'requirements.txt', 'requirements-web.txt'])}
    protected_changed = sorted(k for k in set(current) | set(original['protected_hashes']) if current.get(k) != original['protected_hashes'].get(k))
    from config import config
    config_values = {k: getattr(config, k) for k in ['PLAYER_DATA_MODE', 'PLAYER_DATA_ROOT', 'CAREER_ID', 'BRANCH_ID', 'PLAYER_ID', 'CLI_DATA_DIR', 'MODEL_NAME']}
    config_values.update(OPENAI_API_KEY_present=bool(config.OPENAI_API_KEY), TAVILY_API_KEY_present=bool(config.TAVILY_API_KEY))
    frontend_versions = {}
    for name in ['@playwright/test', 'vite', 'vitest', 'react', '@tanstack/react-query', 'typescript']:
        frontend_versions[name] = json.loads((REPO / 'frontend/node_modules' / name / 'package.json').read_text(encoding='utf-8'))['version']
    data = {'audit_date': '2026-10-26', 'verdict': '补齐明确缺口后可收尾', 'm4_pass': False,
        'baseline': {k: v for k, v in original.items() if k != 'protected_hashes'},
        'effective_non_secret_config': config_values, 'frontend_installed_versions': frontend_versions,
        'source_sha256': source_hashes,
        'audit_script_sha256': {p.name: digest(p) for p in (REPO / 'evaluation').glob('m4_*') if p.is_file()},
        'protected': {'files_checked': len(current), 'changed': protected_changed,
            'original_manifest': 'local_data/game_imports/m4-audit/20261026-01/baseline.json',
            'player_sha256': current.get('memory\\player.json'), 'sessions_sha256': current.get('memory\\sessions.json')},
        'sdk': read('20261026-sdk-02/sdk/sdk.json'),
        'slow_and_cancel': {k: {'ids': [v['first'], v['second']], 'pre_release_status': [s['status'] for s in v['before_release']],
            'cancel_http': v['cancel_http'], 'queue_depth': v['queue_depth'], 'released_status': v['after_release_status'],
            'final_status': v['final']['status'], 'report_present': v['final']['report'] is not None,
            'recommendation_count': len(v['recommendations'].get('items', [])), 'restart_status': v['restart_status'],
            'controlled_calls': v['controlled_calls'], 'graph_calls': v['graph_calls']} for k, v in core.items() if k in ['model', 'tool']},
        'duplicate_and_explanation': core['duplicates'],
        'process_faults': {k: {'pid': v['own_pid'], 'exit_code': v['exit_code'], 'boundary': v['fault_boundary'],
            'before': [{'id': i['id'], 'status': i['snapshot']['status'], 'report_present': i['report_present']} for i in v['pre_restart']],
            'after_status': [i['status'] for i in v['after_restart']], 'report_http': v['public_report_http'],
            'restart_graph_calls': v['restart_model_calls'], 'shutdown': v.get('shutdown')} for k, v in process.items() if k in ['created', 'running', 'report', 'shutdown']},
        'writer_lock': process['writer-lock'], 'actual_process_race': process['actual-process-race'],
        'restore': {k: v for k, v in restore.items() if k != 'broken_restore_candidates'},
        'broken_restore': {k: {'health': v.get('health'), 'player_http': v.get('player_http'), 'resume_http': v.get('resume_http'),
            'resume_error': v.get('paused_view', {}).get('resume_error'), 'available_operations': v.get('paused_view', {}).get('available_operations'),
            'exception_type': v.get('exception_type')} for k, v in restore['broken_restore_candidates'].items()},
        'http_sse': {k: v for k, v in transport.items() if k not in ['first_event', 'after_disconnect', 'final', 'events', 'replay']},
        'frontend': read('20261026-frontend-03/frontend.json'),
        'frontend_e2e_stats': read('20261026-frontend-03/playwright.json')['stats'],
        'frontend_unit_stats': {k: v for k, v in read('20261026-frontend-01/vitest-rerun.json').items() if k.startswith('num') or k == 'success'},
        'regressions': [reg, extra],
        'invalid_attempts': [
            {'run': '20261026-01/processes', 'category': 'harness', 'reason': 'Windows venv launcher PID differs from actual child; corrected base executable + venv PYTHONPATH; original child self-exited on audit caps, PID confirmed absent.'},
            {'run': '20261026-02/processes', 'category': 'harness', 'reason': 'GBK read of UTF-8 JSON; corrected explicit encoding. Use completed 20261026-03.'},
            {'run': '20261026-frontend-01/vitest.json', 'category': 'environment', 'reason': 'sandbox default TEMP rename EPERM; rerun with new isolated TEMP passed 7.'},
            {'run': '20261026-frontend-01', 'category': 'harness', 'reason': 'Selected demo test needed absent API; 8 mocked tests passed, demo deliberately excluded on rerun. Custom API glob intercepted src/api/client.ts.'},
            {'run': '20261026-frontend-02', 'category': 'harness', 'reason': '8 mocked browser tests passed; custom API glob intercepted source module, corrected pathname predicate; use frontend-03.'}],
        'not_run': ['real provider/model quality', 'real game/M3 ten matches', 'provider billing',
            'accepted cancel/timeout lifecycle (mechanism absent)', 'kill at every graph stage',
            'older external checkpoint release corpus', 'non-empty hot WAL backup', 'real native binding restore/path relocation',
            'crash at every E/C SQLite write', 'arbitrary later pause same-field stale input replay', 'browser full demo API end-to-end this round']}
    dump(REPO / 'docs/reports/M4_运行可靠性审计_2026-10-26_evidence.json', data)
    print(json.dumps({'protected_changed': protected_changed, 'backend_tests': reg['tests_run'] + extra['tests_run'],
        'backend_success': reg['successful'] and extra['successful'], 'frontend_e2e': data['frontend_e2e_stats'],
        'config': config_values, 'frontend_versions': frontend_versions}, ensure_ascii=False))


if __name__ == '__main__':
    main()
