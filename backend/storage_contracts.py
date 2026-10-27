"""The small set of on-disk formats this application can read and execute."""
from contextlib import contextmanager
from contextlib import closing
from pathlib import Path
import sqlite3
import time

from backend.writer_lock import WorkspaceWriterLock

WORKSPACE_TABLES = {
    'missions': {'id', 'snapshot', 'report'}, 'events': {'mission_id', 'sequence', 'payload'},
    'messages': {'id', 'conversation_id', 'payload'}, 'mission_inputs': {'mission_id', 'payload'},
    'creation_requests': {'request_id', 'request_hash', 'mission_id', 'message_id'},
}
OPERATION_TABLES = {'operations': {'id', 'mission_id', 'payload'},
    'request_receipts': {'request_id', 'request_hash', 'payload'}}
CHECKPOINT_TABLES = {
    'checkpoints': {'thread_id', 'checkpoint_ns', 'checkpoint_id', 'parent_checkpoint_id', 'type', 'checkpoint', 'metadata'},
    'writes': {'thread_id', 'checkpoint_ns', 'checkpoint_id', 'task_id', 'idx', 'channel', 'type', 'value'},
}


def validate_database(db, kind):
    version = db.execute('PRAGMA user_version').fetchone()[0]
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if kind == 'workspace':
        if version not in {0, 1}:
            raise ValueError('任务库版本不受支持，不能写入；请使用对应版本或恢复副本')
        required = WORKSPACE_TABLES | {name: columns for name, columns in OPERATION_TABLES.items()
            if version == 1 or name in tables}
    else:
        if version != 0:
            raise ValueError('checkpoint 数据库版本不兼容，请使用对应版本或恢复副本')
        required = CHECKPOINT_TABLES
    if not tables and version == 0:
        return  # a new SQLite file; never infer this for an existing nonempty schema
    for table, columns in required.items():
        actual = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
        if not columns <= actual:
            raise ValueError(f'{kind} 数据库结构不兼容，不能自动补建或覆盖')


def check_checkpoint_file(path):
    path = Path(path)
    if not path.is_file():
        return
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        validate_database(db, 'checkpoint')


@contextmanager
def checkpoint_writer(path, *, wait_seconds=0):
    """Confirm the previous checkpoint writer released its OS lease; never kill a PID."""
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            lease = WorkspaceWriterLock(Path(path).parent, name='.checkpoint-writer.lock')
            break
        except ValueError as error:
            if time.monotonic() >= deadline:
                raise ValueError('checkpoint 写入者尚未退出，不能启动新的写入；请等待原执行清理后重试') from error
            time.sleep(min(.02, max(0, deadline - time.monotonic())))
    try:
        yield
    finally:
        lease.close()
