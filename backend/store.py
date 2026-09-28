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
        ''')
        self.db.commit()

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
