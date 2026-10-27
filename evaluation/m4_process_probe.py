"""M4.0 child fixture: no work before its owner admits it to a Windows Job."""
import argparse
import json
import os
from pathlib import Path
import threading
import time

from backend.process_supervisor import OwnedProcess, await_start


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['normal', 'hold', 'checkpoint', 'resume', 'parent'])
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    repo = Path(__file__).resolve().parents[1]
    if not root.is_relative_to(repo / 'local_data/game_imports/m4-implementation'):
        raise ValueError('Probe writes require the isolated M4 implementation root')
    await_start()
    root.mkdir(parents=True, exist_ok=True)
    admitted = time.monotonic()
    if args.mode == 'parent':
        with (root / 'child.log').open('w', encoding='utf-8') as log:
            with OwnedProcess('evaluation.m4_process_probe', 'checkpoint', root / 'child', root=repo, output=log) as child:
                end = time.monotonic() + 60
                while not (root / 'child/ready.json').exists():
                    if child.process.poll() is not None or time.monotonic() > end:
                        raise RuntimeError('Own checkpoint child failed to become ready')
                    time.sleep(.02)
                write(root / 'ready.json', {'pid': os.getpid(), 'child_pid': child.pid,
                    'child_command': child.command, 'stage': 'owner-holding-private-job'})
                threading.Event().wait()  # controller kills only this owned parent handle
        return
    if args.mode == 'hold':
        write(root / 'ready.json', {'pid': os.getpid(), 'stage': 'permanent-block-after-admission'})
        threading.Event().wait()
        return
    from evaluation.m4_audit import harness
    from graph import create_initial_state
    from execution_contracts import publishable
    h = harness(root)
    graph, db = h.runtime(root / 'checkpoints.sqlite3', lambda *_: None)
    config = {'configurable': {'thread_id': 'm40-probe'}, 'recursion_limit': 100}
    try:
        if args.mode != 'resume':
            graph.invoke(create_initial_state('M4 checkpoint probe', snapshot=h.repository.read_snapshot()), config)
        paused = graph.get_state(config)
        if tuple(paused.next) != ('document',):
            raise AssertionError('Expected the existing graph report-approval pause')
        if args.mode == 'checkpoint':
            # A committed real graph checkpoint followed by an uncommitted write
            # proves both checkpoint survival and SQLite lock/rollback on kill.
            db.execute('CREATE TABLE IF NOT EXISTS m40_uncommitted (writer_pid INTEGER)')
            db.commit()
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO m40_uncommitted VALUES (?)', (os.getpid(),))
            write(root / 'ready.json', {'pid': os.getpid(), 'stage': 'checkpoint-committed-write-lock-held',
                'next': paused.next, 'version': paused.values['player_snapshot']['metadata']['state_version'],
                'startup_seconds': round(time.monotonic() - admitted, 3)})
            threading.Event().wait()
        else:
            result = graph.invoke(None, config)
            if not publishable(result):
                raise AssertionError('Existing graph did not produce a publishable deterministic report')
            write(root / 'result.json', {'pid': os.getpid(), 'status': 'COMPLETED',
                'report': result['final_report'], 'nodes': [n for n, _ in h.calls],
                'version': result['player_snapshot']['metadata']['state_version'],
                'elapsed_seconds': round(time.monotonic() - admitted, 3), 'mode': args.mode})
    finally:
        db.close()


if __name__ == '__main__':
    main()
