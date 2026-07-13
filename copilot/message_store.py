"""Message domain store: mentor_messages table.

All methods share the same SQLite connection factory provided by the parent
Store instance. Schema and migrations live in Store; this class only reads and
writes data.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any


class MessageStore:
    def __init__(self, store: "Store") -> None:  # noqa: F821
        self._store = store

    def _message_cursor_id(
        self,
        c: sqlite3.Connection,
        student_id: str,
        message_id: int | str | None,
    ) -> int:
        if message_id is None:
            return 0
        if isinstance(message_id, int):
            row = c.execute(
                "SELECT id FROM mentor_messages WHERE id = ? AND student_id = ?",
                (message_id, student_id),
            ).fetchone()
            return int(row["id"]) if row else 0
        try:
            numeric_id = int(message_id)
        except ValueError:
            row = c.execute(
                "SELECT id FROM mentor_messages WHERE message_id = ? AND student_id = ?",
                (message_id, student_id),
            ).fetchone()
            return int(row["id"]) if row else 0
        row = c.execute(
            "SELECT id FROM mentor_messages WHERE id = ? AND student_id = ?",
            (numeric_id, student_id),
        ).fetchone()
        return int(row["id"]) if row else 0

    def add_mentor_message(
        self,
        student_id: str,
        mentor_id: str,
        session_id: str,
        text: str,
        message_id: str,
    ) -> int:
        with self._store._conn() as c:
            cur = c.execute(
                """INSERT INTO mentor_messages
                   (student_id, mentor_id, session_id, text, message_id,
                    created_at, delivered_at, read_at)
                   VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)""",
                (student_id, mentor_id, session_id, text, message_id, time.time()),
            )
            return cur.lastrowid

    def list_undelivered_messages(
        self,
        student_id: str,
        after_message_id: int | str | None = None,
        *,
        limit: int | None = None,
    ) -> list[dict]:
        with self._store._conn() as c:
            cursor_id = self._message_cursor_id(c, student_id, after_message_id)
            query = """SELECT * FROM mentor_messages
                       WHERE student_id = ? AND delivered_at IS NULL AND id > ?
                       ORDER BY id ASC"""
            params: list[Any] = [student_id, cursor_id]
            if limit is not None:
                query += " LIMIT ?"
                params.append(max(0, int(limit)))
            rows = c.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def list_pending_message_receipts(
        self,
        student_id: str,
        *,
        limit: int,
        after_id: int = 0,
    ) -> list[dict]:
        with self._store._conn() as c:
            rows = c.execute(
                """SELECT * FROM mentor_messages
                   WHERE student_id = ? AND delivered_at IS NULL AND id > ?
                   ORDER BY id ASC LIMIT ?""",
                (student_id, max(0, int(after_id)), max(1, int(limit))),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_messages_since(
        self,
        student_id: str,
        last_seen_message_id: int | str | None,
        *,
        limit: int | None = None,
    ) -> list[dict]:
        with self._store._conn() as c:
            cursor_id = self._message_cursor_id(c, student_id, last_seen_message_id)
            query = """SELECT * FROM mentor_messages
                       WHERE student_id = ? AND id > ?
                       ORDER BY id ASC"""
            params: list[Any] = [student_id, cursor_id]
            if limit is not None:
                query += " LIMIT ?"
                params.append(max(0, int(limit)))
            rows = c.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def mark_message_delivered(self, message_id: str, student_id: str | None = None) -> int:
        with self._store._conn() as c:
            if student_id is not None:
                cur = c.execute(
                    """UPDATE mentor_messages
                       SET delivered_at = COALESCE(delivered_at, ?)
                       WHERE message_id = ? AND student_id = ?""",
                    (time.time(), message_id, student_id),
                )
            else:
                cur = c.execute(
                    """UPDATE mentor_messages
                       SET delivered_at = COALESCE(delivered_at, ?)
                       WHERE message_id = ?""",
                    (time.time(), message_id),
                )
            return cur.rowcount

    def mark_message_read(self, message_id: str, student_id: str | None = None) -> int:
        with self._store._conn() as c:
            if student_id is not None:
                cur = c.execute(
                    """UPDATE mentor_messages
                       SET read_at = COALESCE(read_at, ?)
                       WHERE message_id = ? AND student_id = ?""",
                    (time.time(), message_id, student_id),
                )
            else:
                cur = c.execute(
                    """UPDATE mentor_messages
                       SET read_at = COALESCE(read_at, ?)
                       WHERE message_id = ?""",
                    (time.time(), message_id),
                )
            return cur.rowcount