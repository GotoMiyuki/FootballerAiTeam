"""SQLite persistence of public snapshots, reports, conversations and replayable events."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from backend.models import MissionView, MissionEvent, MessageView, MessageAccepted, OperationView
from backend.call_limits import current_identity, ExecutionFailure
from backend.runtime_contracts import ExecutionIdentity, OperationStatus, may_commit_result


class RequestConflict(ValueError):
    pass

class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        from backend.storage_contracts import validate_database
        try:
            validate_database(self.db, 'workspace')
        except Exception:
            self.db.close()
            raise
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        self.db.execute('PRAGMA journal_mode=WAL')
        if version == 0:
            self.db.executescript('''
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS missions (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, report TEXT);
            CREATE TABLE IF NOT EXISTS events (mission_id TEXT, sequence INTEGER, payload TEXT NOT NULL, PRIMARY KEY(mission_id, sequence));
            CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, conversation_id TEXT, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS mission_inputs (mission_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS creation_requests (request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, mission_id TEXT NOT NULL, message_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS request_receipts (request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, payload TEXT NOT NULL);
            PRAGMA user_version=1;
            COMMIT;
        ''')
        self.db.commit()

    @contextmanager
    def transaction(self):
        """Nested application writes share one outer SQLite commit/rollback."""
        with self.lock:
            outer = not self.db.in_transaction
            if outer:
                self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                if outer:
                    self.db.commit()
            except BaseException:
                if outer:
                    self.db.rollback()
                raise

    def require_execution(self, mission_id, identity):
        operation = self.operation(identity.operation_id)
        if (operation.mission_id != mission_id or not may_commit_result(operation.status,
                ExecutionIdentity(operation.id, operation.run_id), identity)):
            raise ExecutionFailure('RESULT_REVOKED')
        return operation

    @contextmanager
    def execution(self, mission_id, identity):
        with self.transaction():
            yield self.require_execution(mission_id, identity)

    def creation_result(self, request_id, request_hash):
        with self.lock:
            row = self.db.execute('SELECT request_hash,mission_id,message_id FROM creation_requests WHERE request_id=?', (request_id,)).fetchone()
        if not row:
            return None
        if row[0] != request_hash:
            raise ValueError('同一 request_id 不能用于不同的创建请求')
        return row[2], row[1], False

    def create(self, mission, inputs, message, event, *, request_id=None, request_hash=None, operation=None, receipt=None):
        """Commit identity, immutable inputs, initial message/event and deduplication together."""
        with self.transaction():
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
            if operation:
                self._save_operation(operation)
            if receipt:
                self.db.execute('INSERT INTO request_receipts VALUES (?,?,?)', (request_id, request_hash, receipt.model_dump_json()))
        return message.id, mission.id, True

    def request_result(self, request_id, request_hash, response_model=MessageAccepted):
        with self.lock:
            row = self.db.execute('SELECT request_hash,payload FROM request_receipts WHERE request_id=?', (request_id,)).fetchone()
            legacy = None if row else self.db.execute('SELECT 1 FROM creation_requests WHERE request_id=?', (request_id,)).fetchone()
        if row:
            if row[0] != request_hash:
                raise RequestConflict('同一request_id不能用于不同内容或操作')
            return response_model.model_validate_json(row[1]).model_copy(update={'replayed': True})
        if legacy:
            raise RequestConflict('request_id已用于旧关联创建；请使用原关联入口查询回执')
        return None

    def save_receipt(self, request_id, request_hash, receipt):
        with self.transaction():
            self.db.execute('INSERT INTO request_receipts VALUES (?,?,?)', (request_id, request_hash, receipt.model_dump_json()))

    def _save_operation(self, operation):
        row = self.db.execute('SELECT payload FROM operations WHERE id=?', (operation.id,)).fetchone()
        operation.revision = (json.loads(row[0]).get('revision', 0) if row else 0) + 1
        self.db.execute('INSERT INTO operations VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
            (operation.id, operation.mission_id, operation.model_dump_json()))

    def save_operation(self, operation):
        with self.transaction():
            self._save_operation(operation)

    def operations(self, mission_id=None):
        with self.lock:
            rows = self.db.execute('SELECT payload FROM operations' + (' WHERE mission_id=?' if mission_id else '') + ' ORDER BY rowid',
                (mission_id,) if mission_id else ()).fetchall()
        return [OperationView.model_validate_json(row[0]) for row in rows]

    def operation(self, operation_id):
        with self.lock:
            row = self.db.execute('SELECT payload FROM operations WHERE id=?', (operation_id,)).fetchone()
        if not row:
            raise KeyError(operation_id)
        return OperationView.model_validate_json(row[0])

    def accept(self, mission, message, event, operation, request_id, request_hash):
        receipt = MessageAccepted(message_id=message.id, mission_id=mission.id, operation_id=operation.id)
        with self.transaction():
            self.db.execute('UPDATE missions SET snapshot=? WHERE id=?', (mission.model_dump_json(), mission.id))
            self.db.execute('INSERT INTO messages VALUES (?,?,?)', (message.id, mission.conversation_id, message.model_dump_json()))
            self.db.execute('INSERT INTO events VALUES (?,?,?)', (mission.id, event.sequence, event.model_dump_json()))
            self._save_operation(operation)
            self.db.execute('INSERT INTO request_receipts VALUES (?,?,?)', (request_id, request_hash, receipt.model_dump_json()))
        return receipt

    def inputs(self, mission_id):
        with self.lock:
            row = self.db.execute('SELECT payload FROM mission_inputs WHERE mission_id=?', (mission_id,)).fetchone()
        if not row:
            raise ValueError('任务固定输入缺失，不能用当前球员数据代替。请创建新任务。')
        return json.loads(row[0])

    def save(self, mission: MissionView, event: MissionEvent | None = None):
        with self.transaction():
            if current_identity():
                self.require_execution(mission.id, current_identity())
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
        with self.transaction():
            if current_identity():
                self.require_execution(message.mission_id, current_identity())
            self.db.execute('INSERT INTO messages VALUES (?,?,?)', (message.id, conversation_id, message.model_dump_json()))

    def messages(self, conversation_id: str):
        with self.lock:
            rows = self.db.execute('SELECT payload FROM messages WHERE conversation_id=? ORDER BY rowid', (conversation_id,)).fetchall()
        return [MessageView.model_validate_json(row[0]) for row in rows]

    def set_report(self, mission_id: str, content: str):
        with self.transaction():
            if current_identity():
                self.require_execution(mission_id, current_identity())
            self.db.execute('UPDATE missions SET report=? WHERE id=?', (content, mission_id))

    def report(self, mission_id: str):
        with self.lock:
            row = self.db.execute('SELECT report FROM missions WHERE id=?', (mission_id,)).fetchone()
        if not row:
            raise KeyError(mission_id)
        return row[0]

    def close(self):
        self.db.close()
