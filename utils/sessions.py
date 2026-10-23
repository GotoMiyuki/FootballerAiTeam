"""
FootballAI Career Agent - 会话管理

旧 JSON 元数据索引保留兼容；新 CLI 索引只读投影任务库，不推导 checkpoint。
"""
import json
import os
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional
from config import config
from pathlib import Path
import sqlite3

SESSIONS_FILE = os.path.join(config.MEMORY_DIR, "sessions.json")


class SessionRepository:
    """Read-only index over the shared Mission format in an independent CLI root."""
    storage_format = 'mission-sqlite-v1'

    def __init__(self, root=None):
        self.root = Path(root or config.CLI_DATA_DIR).resolve()
        self.workspace_path = self.root / 'workspace.sqlite3'
        self.checkpoint_path = self.root / 'checkpoints.sqlite3'

    def _read(self, statement, parameters=()):
        if not self.workspace_path.is_file():
            return []
        try:
            db = sqlite3.connect(self.workspace_path.as_uri() + '?mode=ro', uri=True)
            try:
                return db.execute(statement, parameters).fetchall()
            finally:
                db.close()
        except sqlite3.Error as error:
            raise ValueError('CLI 任务索引无法读取或格式不兼容，不会初始化旧数据') from error

    def mission(self, thread_id):
        from backend.models import MissionView
        rows = self._read('SELECT snapshot FROM missions WHERE id=?', (thread_id,))
        return MissionView.model_validate_json(rows[0][0]) if rows else None

    def list(self):
        from backend.models import MissionView
        records = []
        for (raw,) in self._read('SELECT snapshot FROM missions ORDER BY rowid DESC'):
            mission = MissionView.model_validate_json(raw)
            records.append({'thread_id': mission.id, 'conversation_id': mission.conversation_id,
                'status': mission.status, 'first_input': mission.objective, 'created_at': mission.created_at,
                'storage_format': self.storage_format, 'workspace_path': str(self.workspace_path),
                'checkpoint_path': str(self.checkpoint_path), 'input_reference': mission.input_reference.model_dump()})
        return records

    def report(self, mission):
        if mission.status != 'COMPLETED' or mission.delivery_status != 'PUBLISHABLE' or not mission.report:
            return None
        rows = self._read('SELECT report FROM missions WHERE id=?', (mission.id,))
        return rows[0][0] if rows else None


def _read_sessions() -> List[Dict[str, Any]]:
    if not os.path.exists(SESSIONS_FILE):
        return []
    with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def _write_sessions(sessions: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(SESSIONS_FILE), exist_ok=True)
    with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)


def list_sessions() -> List[Dict[str, Any]]:
    """列出所有历史会话（按时间倒序）。"""
    sessions = _read_sessions()
    sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
    return sessions


def create_session(user_input: str) -> str:
    """创建新会话，返回 thread_id。"""
    thread_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + str(uuid.uuid4())[:6]
    sessions = _read_sessions()
    sessions.append({
        "thread_id": thread_id,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "first_input": user_input[:120],
        "rounds": 1,
    })
    _write_sessions(sessions)
    return thread_id


def update_session(thread_id: str, user_input: str) -> None:
    """更新会话记录（追加一轮对话）。"""
    sessions = _read_sessions()
    for s in sessions:
        if s["thread_id"] == thread_id:
            s["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            s["rounds"] = s.get("rounds", 1) + 1
            s["last_input"] = user_input[:120]
            _write_sessions(sessions)
            return
    create_session(user_input)


def get_session(thread_id: str) -> Optional[Dict[str, Any]]:
    """获取指定会话信息。"""
    for s in _read_sessions():
        if s["thread_id"] == thread_id:
            return s
    return None


def delete_session(thread_id: str) -> bool:
    """删除指定会话。"""
    sessions = _read_sessions()
    new_sessions = [s for s in sessions if s["thread_id"] != thread_id]
    if len(new_sessions) < len(sessions):
        _write_sessions(new_sessions)
        return True
    return False
