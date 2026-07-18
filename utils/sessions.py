"""
FootballAI Career Agent - 会话管理

多轮对话的 session 持久化与恢复。
"""
import json
import os
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional
from config import config

SESSIONS_FILE = os.path.join(config.MEMORY_DIR, "sessions.json")


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
