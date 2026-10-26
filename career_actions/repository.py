"""Immutable recommendation content and transactional business events in the task SQLite."""
import json
from uuid import uuid4
from backend.events import now
from career_actions.models import (Recommendation, RecommendationEvent, DecisionRecord,
                                  ExecutionRecord, RecommendationActivity)


class RecommendationRepository:
    def __init__(self, store):
        self.store = store
        with store.lock, store.db:
            store.db.executescript('''
                CREATE TABLE IF NOT EXISTS recommendations (
                    id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, subtask_id TEXT NOT NULL,
                    result_version INTEGER NOT NULL, source_key TEXT UNIQUE NOT NULL,
                    payload TEXT NOT NULL, validity TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS recommendation_batches (
                    mission_id TEXT PRIMARY KEY, availability TEXT NOT NULL, reason TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS recommendation_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL,
                    payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS recommendation_actions (
                    id TEXT PRIMARY KEY, recommendation_id TEXT NOT NULL, kind TEXT NOT NULL,
                    previous_id TEXT, payload TEXT NOT NULL, UNIQUE(kind, previous_id));
                CREATE INDEX IF NOT EXISTS recommendation_actions_record
                    ON recommendation_actions(recommendation_id, kind);
                CREATE TABLE IF NOT EXISTS recommendation_action_heads (
                    recommendation_id TEXT NOT NULL, kind TEXT NOT NULL, record_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS recommendation_action_requests (
                    request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, record_id TEXT NOT NULL);
            ''')

    def records(self, mission_id):
        with self.store.lock:
            rows = self.store.db.execute('SELECT payload,validity FROM recommendations WHERE mission_id=? ORDER BY rowid', (mission_id,)).fetchall()
        return [(Recommendation.model_validate_json(row[0]), row[1]) for row in rows]

    def batch(self, mission_id):
        with self.store.lock:
            row = self.store.db.execute('SELECT availability,reason FROM recommendation_batches WHERE mission_id=?', (mission_id,)).fetchone()
        return row or ('NOT_PROJECTED', '该任务尚未建立可验证的训练焦点建议记录')

    def _event(self, record, kind, **references):
        cursor = self.store.db.execute('INSERT INTO recommendation_events(mission_id,payload) VALUES (?,?)', (record.source.mission_id, '{}'))
        event = RecommendationEvent(event_id=f'evt_{uuid4().hex}', sequence=cursor.lastrowid,
            type=kind, recommendation_id=record.recommendation_id, revision=record.revision,
            context=record.context, source=record.source, timestamp=now(), **references)
        self.store.db.execute('UPDATE recommendation_events SET payload=? WHERE sequence=?', (event.model_dump_json(), event.sequence))

    def save_projection(self, mission_id, records):
        with self.store.lock, self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            current_keys = {record.recommendation_id for record in records}
            for record, validity in self.records(mission_id):
                if record.recommendation_id not in current_keys and validity == 'current':
                    self.store.db.execute("UPDATE recommendations SET validity='superseded' WHERE id=?", (record.recommendation_id,))
                    self._event(record, 'recommendation.superseded')
            for record in records:
                source_key = json.dumps([mission_id, record.source.subtask_id, record.source.result_version,
                    record.source.payload_position, record.source.mapping_version])
                existing = self.store.db.execute('SELECT payload FROM recommendations WHERE source_key=?', (source_key,)).fetchone()
                if existing:
                    original = Recommendation.model_validate_json(existing[0])
                    if (original.source != record.source
                            or original.revision != record.revision
                            or original.context != record.context or original.content != record.content
                            or original.applicability != record.applicability
                            or original.execution_support != record.execution_support
                            or original.evaluation_spec != record.evaluation_spec):
                        raise ValueError('同一来源版本的建议内容发生冲突，不能覆盖原记录')
                    continue
                self.store.db.execute('INSERT INTO recommendations VALUES (?,?,?,?,?,?,?)',
                    (record.recommendation_id, mission_id, record.source.subtask_id, record.source.result_version,
                     source_key, record.model_dump_json(), 'current'))
                self._event(record, 'recommendation.created')
            self._batch(mission_id, 'AVAILABLE', '训练焦点建议已按专业成果版本保存' if records else '当前任务没有可投影的训练焦点')

    def _batch(self, mission_id, availability, reason):
        if self.batch(mission_id) != (availability, reason):
            self.store.db.execute('INSERT INTO recommendation_batches VALUES (?,?,?) ON CONFLICT(mission_id) DO UPDATE SET availability=excluded.availability,reason=excluded.reason',
                (mission_id, availability, reason))

    def unavailable(self, mission_id, reason):
        with self.store.lock, self.store.db:
            for record, validity in self.records(mission_id):
                if validity == 'current':
                    self.store.db.execute("UPDATE recommendations SET validity='withdrawn' WHERE id=?", (record.recommendation_id,))
                    self._event(record, 'recommendation.withdrawn')
            self._batch(mission_id, 'UNAVAILABLE', reason)

    def events(self, mission_id, after=0):
        with self.store.lock:
            rows = self.store.db.execute('SELECT payload FROM recommendation_events WHERE mission_id=? AND sequence>? ORDER BY sequence LIMIT 200', (mission_id, after)).fetchall()
        return [RecommendationEvent.model_validate_json(row[0]) for row in rows]

    def event(self, mission_id, event_id):
        """Read one authoritative event for source-checking consumers such as C2."""
        with self.store.lock:
            row = self.store.db.execute('SELECT payload FROM recommendation_events WHERE mission_id=? AND json_extract(payload,\'$.event_id\')=?',
                (mission_id, event_id)).fetchone()
        return RecommendationEvent.model_validate_json(row[0]) if row else None

    def action_result(self, request_id, request_hash, kind):
        with self.store.lock:
            row = self.store.db.execute('''SELECT r.request_hash,a.kind,a.payload
                FROM recommendation_action_requests r JOIN recommendation_actions a ON a.id=r.record_id
                WHERE r.request_id=?''', (request_id,)).fetchone()
        if not row:
            return None
        if row[0] != request_hash or row[1] != kind:
            raise ValueError('同一 request_id 不能用于不同的选择或执行请求')
        model = DecisionRecord if kind == 'decision' else ExecutionRecord
        return model.model_validate_json(row[2])

    def activity(self, recommendation):
        with self.store.lock:
            rows = self.store.db.execute('''SELECT kind,payload FROM recommendation_actions
                WHERE recommendation_id=? ORDER BY rowid''', (recommendation.recommendation_id,)).fetchall()
            heads = self.store.db.execute('''SELECT kind,record_id FROM recommendation_action_heads
                WHERE recommendation_id=? ORDER BY rowid''', (recommendation.recommendation_id,)).fetchall()
        return RecommendationActivity(recommendation_id=recommendation.recommendation_id,
            recommendation_revision=recommendation.revision, context=recommendation.context.model_dump(),
            decisions=[DecisionRecord.model_validate_json(payload) for kind, payload in rows if kind == 'decision'],
            executions=[ExecutionRecord.model_validate_json(payload) for kind, payload in rows if kind == 'execution'],
            current_decision_id=next((identity for kind, identity in heads if kind == 'decision'), None),
            current_execution_ids=[identity for kind, identity in heads if kind == 'execution'])

    def save_action(self, recommendation, record, kind, request_hash):
        """Caller holds Store.lock through applicability validation; all writes commit together."""
        identity = record.decision_id if kind == 'decision' else record.execution_id
        previous = record.previous_decision_id if kind == 'decision' else record.corrects_execution_id
        with self.store.lock, self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            duplicate = self.action_result(record.request_id, request_hash, kind)
            if duplicate:
                return duplicate, False
            activity = self.activity(recommendation)
            if kind == 'decision' and activity.current_decision_id != previous:
                raise ValueError('选择已变化，请刷新后关联当前选择再提交')
            if kind == 'execution' and previous and previous not in activity.current_execution_ids:
                raise ValueError('原执行记录不属于该建议或已被更正，请刷新后重试')
            self.store.db.execute('INSERT INTO recommendation_actions VALUES (?,?,?,?,?)',
                (identity, recommendation.recommendation_id, kind, previous, record.model_dump_json()))
            if previous:
                self.store.db.execute('DELETE FROM recommendation_action_heads WHERE record_id=?', (previous,))
            self.store.db.execute('INSERT INTO recommendation_action_heads VALUES (?,?,?)',
                (recommendation.recommendation_id, kind, identity))
            self.store.db.execute('INSERT INTO recommendation_action_requests VALUES (?,?,?)',
                (record.request_id, request_hash, identity))
            event_type = 'decision.recorded' if kind == 'decision' else (
                'execution.corrected' if previous else 'execution.recorded')
            self._event(recommendation, event_type, object_id=identity, object_revision=record.revision,
                previous_record_id=previous)
        return record, True
