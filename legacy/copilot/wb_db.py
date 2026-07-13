"""只读访问 WorkBuddy 核心数据库（~/.workbuddy/workbuddy.db）。

WorkBuddy 的权威数据存储在 SQLite 数据库 workbuddy.db 中，包含：
- sessions 表：全部会话（含 title / custom_title / status / mode / deleted_at）
- workspaces 表：项目目录索引
- session_usage 表：会话用量

本模块只读访问（mode=ro），绝不写入。所有查询封装在此，供 wb_sessions.py
和 store.py 调用。

数据库 schema 见 docs/workbuddy-file-structure.md。
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

log = logging.getLogger("copilot.wb_db")

DB_PATH = Path.home() / ".workbuddy" / "workbuddy.db"


def _connect() -> sqlite3.Connection:
    """以只读模式连接 workbuddy.db。"""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"WorkBuddy 数据库不存在: {DB_PATH}")
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def list_sessions(
    cwd: str | None = None,
    include_deleted: bool = False,
    limit: int = 200,
) -> list[dict]:
    """查询会话列表。

    Args:
        cwd: 按工作目录过滤（None = 所有项目）
        include_deleted: 是否包含已删除会话（deleted_at IS NOT NULL）
        limit: 最多返回条数

    Returns:
        每条会话 dict，字段：
        - session_id, title (custom_title 优先), raw_title, custom_title
        - work_dir, status, mode, created_at, last_activity_at
        - deleted (bool)
    """
    try:
        conn = _connect()
    except Exception as e:
        log.warning("连接 workbuddy.db 失败: %s", e)
        return []

    try:
        where_parts = []
        params: list = []
        if cwd:
            where_parts.append("cwd = ?")
            params.append(cwd)
        if not include_deleted:
            where_parts.append("deleted_at IS NULL")
        where_clause = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

        rows = conn.execute(
            f"""SELECT id, cwd, title, custom_title, status, mode,
                      created_at, last_activity_at, deleted_at
               FROM sessions{where_clause}
               ORDER BY last_activity_at DESC
               LIMIT ?""",
            (*params, limit),
        ).fetchall()

        return [
            {
                "session_id": r["id"],
                "work_dir": r["cwd"],
                "title": r["custom_title"] or r["title"] or "",
                "raw_title": r["title"] or "",
                "custom_title": r["custom_title"] or "",
                "status": r["status"],
                "mode": r["mode"],
                "created_at": (r["created_at"] or 0) / 1000,
                "last_activity_at": (r["last_activity_at"] or 0) / 1000,
                "deleted": r["deleted_at"] is not None,
            }
            for r in rows
        ]
    except Exception as e:
        log.warning("查询 sessions 失败: %s", e)
        return []
    finally:
        conn.close()


def get_session(session_id: str) -> dict | None:
    """查询单个会话详情。"""
    try:
        conn = _connect()
    except Exception as e:
        log.warning("连接 workbuddy.db 失败: %s", e)
        return None

    try:
        r = conn.execute(
            """SELECT id, cwd, title, custom_title, status, mode,
                      created_at, last_activity_at, deleted_at, user_id
               FROM sessions WHERE id = ?""",
            (session_id,),
        ).fetchone()
        if not r:
            return None
        return {
            "session_id": r["id"],
            "work_dir": r["cwd"],
            "title": r["custom_title"] or r["title"] or "",
            "raw_title": r["title"] or "",
            "custom_title": r["custom_title"] or "",
            "status": r["status"],
            "mode": r["mode"],
            "created_at": (r["created_at"] or 0) / 1000,
            "last_activity_at": (r["last_activity_at"] or 0) / 1000,
            "deleted": r["deleted_at"] is not None,
            "user_id": r["user_id"],
        }
    except Exception as e:
        log.warning("查询会话 %s 失败: %s", session_id, e)
        return None
    finally:
        conn.close()


def get_session_title(session_id: str) -> str:
    """获取会话标题（custom_title 优先，其次 title）。"""
    s = get_session(session_id)
    return s["title"] if s else ""


def list_workspaces(limit: int = 50) -> list[dict]:
    """查询工作区（项目目录）列表。"""
    try:
        conn = _connect()
    except Exception as e:
        log.warning("连接 workbuddy.db 失败: %s", e)
        return []

    try:
        rows = conn.execute(
            """SELECT path, last_opened_at FROM workspaces
               ORDER BY last_opened_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            {
                "path": r["path"],
                "last_opened_at": (r["last_opened_at"] or 0) / 1000,
            }
            for r in rows
        ]
    except Exception as e:
        log.warning("查询 workspaces 失败: %s", e)
        return []
    finally:
        conn.close()


def get_user_id() -> str | None:
    """获取当前用户的 user_id（从 sessions 表取 DISTINCT user_id）。"""
    try:
        conn = _connect()
    except Exception as e:
        log.warning("连接 workbuddy.db 失败: %s", e)
        return None

    try:
        row = conn.execute("SELECT DISTINCT user_id FROM sessions LIMIT 1").fetchone()
        return row["user_id"] if row else None
    except Exception as e:
        log.warning("查询 user_id 失败: %s", e)
        return None
    finally:
        conn.close()
