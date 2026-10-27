"""Owned independent holders of the three production backup locks."""
from contextlib import closing
import json
from pathlib import Path
import sys
import threading

from backend.process_supervisor import await_start
from backend.storage_contracts import checkpoint_writer
from backend.writer_lock import WorkspaceWriterLock
from player_data.writer_lock import actual_writer


def main():
    await_start()
    kind, root, marker = sys.argv[1], Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
    workspace = Path(__file__).resolve().parents[1] / 'local_data/game_imports/m4-implementation'
    if not root.is_relative_to(workspace) or not marker.is_relative_to(workspace): raise ValueError('Isolated roots required')
    lease = {'workspace': lambda: closing(WorkspaceWriterLock(root)),
        'checkpoint': lambda: checkpoint_writer(root / 'checkpoints.sqlite3'), 'actual': lambda: actual_writer(root)}[kind]()
    with lease:
        marker.write_text(json.dumps({'kind': kind}), encoding='utf-8')
        threading.Event().wait()


if __name__ == '__main__': main()
