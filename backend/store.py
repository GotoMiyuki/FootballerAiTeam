"""SQLite persistence of public snapshots, reports, conversations and replayable events."""
import json
import sqlite3
import threading
from pathlib import Path

from backend.models import MissionView, MissionEvent, MessageView

class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS missions (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, report TEXT);
            CREATE TABLE IF NOT EXISTS events (mission_id TEXT, sequence INTEGER, payload TEXT NOT NULL, PRIMARY KEY(mission_id, sequence));
            CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, conversation_id TEXT, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS mission_inputs (mission_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS creation_requests (request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, mission_id TEXT NOT NULL, message_id TEXT NOT NULL);
        ''')
        self.db.commit()

    def creation_result(self, request_id, request_hash):
        with self.lock:
            row = self.db.execute('SELECT request_hash,mission_id,message_id FROM creation_requests WHERE request_id=?', (request_id,)).fetchone()
        if not row:
            return None
        if row[0] != request_hash:
            raise ValueError('同一 request_id 不能用于不同的创建请求')
        return row[2], row[1], False

    def create(self, mission, inputs, message, event, *, request_id=None, request_hash=None):
        """Commit identity, immutable inputs, initial message/event and deduplication together."""
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if request_id:
                existing = self.creation_result(request_id, request_hash)
                if existing:
                    return existing
            self.db.execute('INSERT INTO missions(id,snapshot) VALUES (?,?)', (mission.id, mission.model_dump_json()))
            self.db.execute('INSERT INTO mission_inputs VALUES (?,?)', (mission.id, json.dumps(inputs, ensure_ascii=False, allow_nan=False)))
            self.db.execute('INSERT INTO messages VALUES (?,?,?)', (message.id, mission.conversation_id, message.model_dump_json()))
            self.db.execute('INSERT INTO events VALUES (?,?,?)', (mission.id, event.sequence, event.model_dump_json()))
            if request_id:
                self.db.execute('INSERT INTO creation_requests VALUES (?,?,?,?)', (request_id, request_hash, mission.id, message.id))
        return message.id, mission.id, True

    def inputs(self, mission_id):
        with self.lock:
            row = self.db.execute('SELECT payload FROM mission_inputs WHERE mission_id=?', (mission_id,)).fetchone()
        if not row:
            raise ValueError('任务固定输入缺失，不能用当前球员数据代替。请创建新任务。')
        return json.loads(row[0])

    def save(self, mission: MissionView, event: MissionEvent | None = None):
        with self.lock, self.db:
            self.db.execute('INSERT INTO missions(id,snapshot) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET snapshot=excluded.snapshot', (mission.id, mission.model_dump_json()))
            if event:
                self.db.execute('INSERT INTO events VALUES (?,?,?)', (mission.id, event.sequence, event.model_dump_json()))

    def get(self, mission_id: str) -> MissionView:
        with self.lock:
            row = self.db.execute('SELECT snapshot FROM missions WHERE id=?', (mission_id,)).fetchone()
        if not row:
            raise KeyError(mission_id)
        return MissionView.model_validate_json(row[0])

    def missions(self):
        with self.lock:
            rows = self.db.execute('SELECT snapshot FROM missions ORDER BY rowid DESC').fetchall()
        return [MissionView.model_validate_json(row[0]) for row in rows]

    def events(self, mission_id: str, after: int):
        with self.lock:
            rows = self.db.execute('SELECT payload FROM events WHERE mission_id=? AND sequence>? ORDER BY sequence', (mission_id, after)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def add_message(self, conversation_id: str, message: MessageView):
        with self.lock, self.db:
            self.db.execute('INSERT INTO messages VALUES (?,?,?)', (message.id, conversation_id, message.model_dump_json()))

    def messages(self, conversation_id: str):
        with self.lock:
            rows = self.db.execute('SELECT payload FROM messages WHERE conversation_id=? ORDER BY rowid', (conversation_id,)).fetchall()
        return [MessageView.model_validate_json(row[0]) for row in rows]

    def set_report(self, mission_id: str, content: str):
        with self.lock, self.db:
            self.db.execute('UPDATE missions SET report=? WHERE id=?', (content, mission_id))

    def report(self, mission_id: str):
        with self.lock:
            row = self.db.execute('SELECT report FROM missions WHERE id=?', (mission_id,)).fetchone()
        if not row:
            raise KeyError(mission_id)
        return row[0]

    def close(self):
        self.db.close()
