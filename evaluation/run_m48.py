"""Fresh formal acceptance wrapper; individual results remain separately inspectable."""
import argparse
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from evaluation.m4_audit import dump, digest
from evaluation.run_m40 import REPO


def source_inventory():
    paths = ['agents', 'backend', 'career_actions', 'career_memory', 'player_data',
             'tools', 'utils', 'evaluation', 'frontend/src', 'frontend/e2e']
    files = {p for folder in paths for p in (REPO / folder).rglob('*')
             if p.is_file() and p.suffix in {'.py', '.ts', '.tsx', '.css', '.mjs'}
             and '__pycache__' not in p.parts}
    files.update((REPO / 'game_adapters').glob('*.py'))
    files.update(REPO.glob('*.py'))
    files.update(p for name in ['requirements.txt', 'frontend/package.json',
        'frontend/package-lock.json', 'frontend/tsconfig.json', 'frontend/tsconfig.app.json',
        'frontend/tsconfig.node.json', 'frontend/vite.config.ts'] if (p := REPO / name).is_file())
    return {p.relative_to(REPO).as_posix(): digest(p) for p in sorted(files)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_relative_to(REPO / 'local_data/game_imports/m4-implementation'):
        raise ValueError('A new isolated implementation root is required')
    root.mkdir(parents=True, exist_ok=False)
    before = source_inventory()
    baseline = {'revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
        'source_sha256': before, 'python': sys.version,
        'dependencies': {p: version(p) for p in ['langgraph', 'langgraph-checkpoint-sqlite',
            'langchain-openai', 'openai', 'httpx', 'fastapi']}}
    dump(root / 'source-baseline.json', baseline)
    started = time.monotonic()
    env = {**os.environ, 'PYTHONUTF8': '1'}
    with (root / 'backend-command.log').open('w', encoding='utf-8') as log:
        completed = subprocess.run([sys.executable, '-m', 'evaluation.run_m47', '--full',
            '--root', str(root / 'backend')], cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT,
            timeout=3600)
    result = json.loads((root / 'backend/summary.json').read_text(encoding='utf-8'))
    after = source_inventory()
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    summary = {'scope': 'M4.8 fresh backend acceptance, final milestone decision requires the criterion matrix and frontend gates',
        'revision': baseline['revision'], 'backend_exit_code': completed.returncode,
        'tests': result['tests'], 'passed_gate': result['passed_gate'] and completed.returncode == 0 and not changed,
        'seconds': time.monotonic() - started, 'source_files': len(before), 'source_changed': changed,
        'protected_file_count': result['protected_file_count'], 'protected_changed': result['protected_changed']}
    dump(root / 'backend-acceptance.json', summary)
    print(json.dumps(summary, ensure_ascii=True), flush=True)
    return int(not summary['passed_gate'])


if __name__ == '__main__':
    raise SystemExit(main())
