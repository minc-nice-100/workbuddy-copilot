"""读取 WorkBuddy 的会话状态，判断当前激活的对话。

数据源优先级：
1. ~/.workbuddy/workbuddy.db（权威，205 条完整会话，含标题）
2. ~/.workbuddy/app/sessions.json（运行时缓存，仅 7 条，无标题）

DB 有完整数据时用 DB；DB 不可用时降级到 sessions.json。
resumedAt / last_activity_at 最新的那个就是用户当前正在用的对话。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from . import wb_db

log = logging.getLogger("copilot.wb_sessions")

SESSIONS_FILE = Path.home() / ".workbuddy" / "app" / "sessions.json"


def _parse_iso(ts: str) -> float:
    """ISO8601 -> unix timestamp（秒）。兼容带/不带 Z、带毫秒。"""
    if not ts:
        return 0.0
    s = ts.rstrip("Z").replace("+00:00", "")
    try:
        if "." in s:
            dt = datetime.fromisoformat(s)
        else:
            dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
        return dt.replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return 0.0


def _read_from_db() -> list[dict]:
    """从 workbuddy.db 读取会话（权威数据源）。"""
    rows = wb_db.list_sessions(include_deleted=False, limit=200)
    if not rows:
        return []
    out = []
    for r in rows:
        out.append({
            "session_id": r["session_id"],
            "work_dir": r["work_dir"],
            "started_at": r["created_at"],
            "resumed_at": r["last_activity_at"],
            "title": r["title"],
            "status": r["status"],
            "mode": r["mode"],
        })
    out.sort(key=lambda x: x["resumed_at"], reverse=True)
    return out


def _read_from_json() -> list[dict]:
    """从 app/sessions.json 读取会话（降级方案）。"""
    if not SESSIONS_FILE.exists():
        return []
    try:
        data = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("读取 sessions.json 失败: %s", e)
        return []
    sessions = data.get("sessions", []) if isinstance(data, dict) else []
    out = []
    for s in sessions:
        if not isinstance(s, dict):
            continue
        cid = s.get("conversationId")
        if not cid:
            continue
        out.append({
            "session_id": cid,
            "work_dir": s.get("workDir", ""),
            "started_at": _parse_iso(s.get("startedAt", "")),
            "resumed_at": _parse_iso(s.get("resumedAt", "")),
            "title": "",  # sessions.json 无标题字段
            "status": "",
            "mode": "",
        })
    out.sort(key=lambda x: x["resumed_at"], reverse=True)
    return out


def list_workbuddy_sessions() -> list[dict]:
    """读取会话列表，按 resumed_at / last_activity_at 降序返回。

    优先读 workbuddy.db（权威，含标题），降级到 app/sessions.json（缓存）。
    """
    # 优先 DB
    db_sessions = _read_from_db()
    if db_sessions:
        return db_sessions
    # 降级 sessions.json
    log.info("workbuddy.db 无数据，降级到 sessions.json")
    return _read_from_json()


def get_active_session(work_dir: str | None = None) -> dict | None:
    """返回当前激活的对话（resumed_at 最新）。

    若提供 work_dir，则只在该工作目录下的会话里找（用于按项目过滤）。
    返回 {session_id, work_dir, resumed_at, title} 或 None。
    """
    sessions = list_workbuddy_sessions()
    if not sessions:
        return None
    if work_dir:
        filtered = [s for s in sessions if s["work_dir"] == work_dir]
        if filtered:
            return filtered[0]
    return sessions[0]


def get_session_title(session_id: str) -> str:
    """获取会话标题。直接查 DB，不扫 JSONL。"""
    return wb_db.get_session_title(session_id)


class WorkBuddySessionRepository:
    """AgentSessionRepository 端口的 WorkBuddy 实现。

    封装 wb_db + wb_sessions 模块，实现 ports.AgentSessionRepository 接口。
    切换到其他 Agent 框架时，只需提供新的 AgentSessionRepository 实现。
    """

    def list_sessions(
        self, cwd: str | None = None,
        include_deleted: bool = False, limit: int = 200,
    ) -> list[dict]:
        return list_workbuddy_sessions()

    def get_session_title(self, session_id: str) -> str:
        return wb_db.get_session_title(session_id)

    def get_active_session(self, work_dir: str | None = None) -> dict | None:
        return get_active_session(work_dir)
