"""Fail-fast, reentrant, cross-process ownership of actual root writes.

Lock order: actual root -> existing per-state/import lock -> file replacement.
Readers do not acquire or create this lock. The lock file is never deleted:
removing it could split ownership across two filesystem objects.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import threading
import weakref

from player_data.models import PlayerDataError


class ActualWriterBusy(PlayerDataError):
    def __init__(self):
        super().__init__('writer_busy', 'Actual root already has a writer; retry after that operation finishes')


class _State:
    def __init__(self):
        self.lock = threading.RLock()
        self.depth = 0
        self.file = None


_guard = threading.Lock()
_states = weakref.WeakValueDictionary()


@contextmanager
def actual_writer(root):
    root = Path(root).resolve()
    key = (os.getpid(), os.path.normcase(str(root)))
    with _guard:
        state = _states.get(key)
        if state is None:
            state = _State()
            _states[key] = state
    if not state.lock.acquire(blocking=False):
        raise ActualWriterBusy()
    entered = False
    try:
        if state.depth == 0:
            root.mkdir(parents=True, exist_ok=True)
            stream = (root / '.actual-writer.lock').open('a+b')
            try:
                if stream.seek(0, os.SEEK_END) == 0:
                    stream.write(b'0')
                    stream.flush()
                stream.seek(0)
                try:
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise ActualWriterBusy() from exc
            except BaseException:
                stream.close()
                raise
            state.file = stream
        state.depth += 1
        entered = True
        yield
    finally:
        try:
            if entered:
                state.depth -= 1
                if state.depth == 0:
                    stream, state.file = state.file, None
                    try:
                        stream.seek(0)
                        if os.name == 'nt':
                            import msvcrt
                            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                    finally:
                        stream.close()
        finally:
            state.lock.release()
