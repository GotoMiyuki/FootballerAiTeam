"""Immutable content revisions, source-event deduplication and user CAS in task SQLite."""
from career_memory.models import MemoryRecord
from player_data.models import content_hash


def context_key(context):
    return content_hash(context.model_dump() if hasattr(context, 'model_dump') else context.to_dict())


class CareerMemoryRepository:
    def __init__(self, store):
        self.store = store
        with store.lock, store.db:
            store.db.executescript('''
                CREATE TABLE IF NOT EXISTS career_memories (
                    memory_id TEXT NOT NULL, revision INTEGER NOT NULL, context_key TEXT NOT NULL,
                    source_key TEXT UNIQUE, payload TEXT NOT NULL, status TEXT NOT NULL,
                    PRIMARY KEY(memory_id, revision));
                CREATE INDEX IF NOT EXISTS career_memories_context ON career_memories(context_key);
                CREATE TABLE IF NOT EXISTS career_memory_events (
                    event_id TEXT PRIMARY KEY, context_key TEXT NOT NULL, event_hash TEXT NOT NULL,
                    payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS career_memory_requests (
                    request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
                    memory_id TEXT NOT NULL, revision INTEGER NOT NULL);
            ''')

    def records(self, context):
        with self.store.lock:
            rows = self.store.db.execute('''SELECT payload,status FROM career_memories
                WHERE context_key=? ORDER BY rowid''', (context_key(context),)).fetchall()
        return [(MemoryRecord.model_validate_json(payload), status) for payload, status in rows]

    def latest(self, memory_id, context):
        with self.store.lock:
            row = self.store.db.execute('''SELECT payload,status FROM career_memories
                WHERE memory_id=? AND context_key=? ORDER BY revision DESC LIMIT 1''',
                (memory_id, context_key(context))).fetchone()
        if not row:
            raise ValueError('记忆不属于该生涯、分支或球员，或记录不存在')
        return MemoryRecord.model_validate_json(row[0]), row[1]

    def request_result(self, request_id, fingerprint):
        row = self.store.db.execute('''SELECT r.request_hash,m.payload FROM career_memory_requests r
            JOIN career_memories m ON m.memory_id=r.memory_id AND m.revision=r.revision
            WHERE r.request_id=?''', (request_id,)).fetchone()
        if not row:
            return None
        if row[0] != fingerprint:
            raise ValueError('同一request_id不能用于不同记忆操作')
        return MemoryRecord.model_validate_json(row[1])

    def append(self, record, status, source_key=None):
        if source_key:
            existing = self.store.db.execute('SELECT payload FROM career_memories WHERE source_key=?', (source_key,)).fetchone()
            if existing:
                if MemoryRecord.model_validate_json(existing[0]) != record:
                    raise ValueError('同一来源的记忆投影冲突，不能覆盖')
                return False
        self.store.db.execute('INSERT INTO career_memories VALUES (?,?,?,?,?,?)',
            (record.memory_id, record.revision, context_key(record.context), source_key, record.model_dump_json(), status))
        if record.corrects_revision:
            self.store.db.execute('''UPDATE career_memories SET status='superseded'
                WHERE memory_id=? AND revision=?''', (record.memory_id, record.corrects_revision))
        if record.supersedes_memory_id:
            self.store.db.execute("UPDATE career_memories SET status='superseded' WHERE memory_id=? AND context_key=?",
                (record.supersedes_memory_id, context_key(record.context)))
        return True

    def consume_event(self, event):
        fingerprint = content_hash(event.model_dump())
        existing = self.store.db.execute('SELECT event_hash FROM career_memory_events WHERE event_id=?', (event.event_id,)).fetchone()
        if existing:
            if existing[0] != fingerprint:
                raise ValueError('同一E事件id内容冲突，未同步记忆')
            return False
        self.store.db.execute('INSERT INTO career_memory_events VALUES (?,?,?,?)',
            (event.event_id, context_key(event.context), fingerprint, event.model_dump_json()))
        return True
