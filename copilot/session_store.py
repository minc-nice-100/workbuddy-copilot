"""Session domain store: students, sessions, reports, analyses, prompts,
ai_summaries, raw_transcripts, messages, student_asks, prompt_configs.

All methods share the same SQLite connection factory provided by the parent
Store instance. Schema and migrations live in Store; this class only reads and
writes data.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

from .models import Conversation, Student, TimelineEntry

log = logging.getLogger("copilot.session_store")


class SessionStore:
    def __init__(self, store: "Store") -> None:  # noqa: F821
        self._store = store

    # ------------------------------------------------------------------
    # students
    # ------------------------------------------------------------------

    def upsert_student(self, student_id: str, display_name: str | None = None) -> None:
        now = time.time()
        with self._store._conn() as c:
            c.execute(
                """INSERT INTO students (student_id, display_name, token_hash, created_at)
                   VALUES (?, ?, NULL, ?)
                   ON CONFLICT(student_id) DO UPDATE SET
                     display_name = CASE
                       WHEN excluded.display_name IS NULL THEN students.display_name
                       ELSE excluded.display_name
                     END""",
                (student_id, display_name, now),
            )

    def delete_student(self, student_id: str) -> dict[str, int]:
        deleted: dict[str, int] = {}
        with self._store._conn() as c:
            cur = c.execute(
                """DELETE FROM analyses
                   WHERE student_id = ?
                      OR report_id IN (SELECT id FROM reports WHERE student_id = ?)""",
                (student_id, student_id),
            )
            deleted["analyses"] = cur.rowcount

            cur = c.execute(
                """DELETE FROM ai_summaries
                   WHERE student_id = ?
                      OR prompt_id IN (SELECT id FROM prompts WHERE student_id = ?)""",
                (student_id, student_id),
            )
            deleted["ai_summaries"] = cur.rowcount

            c.execute("DELETE FROM student_asks WHERE student_id = ?", (student_id,))

            for table in ["messages", "upload_requests"]:
                cur = c.execute(f"DELETE FROM {table} WHERE student_id = ?", (student_id,))
                if cur.rowcount:
                    deleted[table] = cur.rowcount

            for table in [
                "prompts",
                "raw_transcripts",
                "mentor_messages",
                "reports",
                "sessions",
                "students",
            ]:
                cur = c.execute(f"DELETE FROM {table} WHERE student_id = ?", (student_id,))
                deleted[table] = cur.rowcount
        return deleted

    def students_overview(self, limit: int = 50) -> list[Student]:
        with self._store._conn() as c:
            rows = c.execute(
                """SELECT
                     s.student_id,
                     s.display_name AS display_name,
                     COALESCE(COUNT(a.id), 0) AS analysis_count,
                     COALESCE(COUNT(DISTINCT ss.session_id), 0) AS session_count,
                     COALESCE(MAX(a.created_at), MAX(ss.last_activity_at), s.created_at, 0) AS last_ts,
                     COALESCE((
                       SELECT a2.topic
                       FROM analyses a2
                       WHERE a2.student_id = s.student_id
                       ORDER BY a2.created_at DESC, a2.id DESC
                       LIMIT 1
                     ), '') AS last_topic,
                     CASE COALESCE(MAX(CASE a.severity
                       WHEN 'error' THEN 3
                       WHEN 'warn' THEN 2
                       ELSE 1
                     END), 1)
                       WHEN 3 THEN 'error'
                       WHEN 2 THEN 'warn'
                       ELSE 'info'
                     END AS last_severity,
                     COALESCE(SUM(CASE
                       WHEN COALESCE(a.alert, '') != ''
                         OR a.understanding IN ('low','stuck')
                       THEN 1 ELSE 0 END), 0) AS alert_count,
                     COALESCE((
                       SELECT a2.diagnosis
                       FROM analyses a2
                       WHERE a2.student_id = s.student_id
                       ORDER BY a2.created_at DESC, a2.id DESC
                       LIMIT 1
                     ), '') AS last_diagnosis
                   FROM students s
                   LEFT JOIN sessions ss ON ss.student_id = s.student_id
                   LEFT JOIN analyses a ON a.student_id = s.student_id
                   GROUP BY s.student_id
                   ORDER BY COALESCE(MAX(a.created_at), MAX(ss.last_activity_at), s.created_at, 0) DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [
                Student(
                    student_id=r["student_id"],
                    display_name=r["display_name"] or f"学员 {r['student_id']}",
                    analysis_count=r["analysis_count"] or 0,
                    session_count=r["session_count"] or 0,
                    last_ts=r["last_ts"] or 0,
                    last_topic=r["last_topic"] or "",
                    last_severity=r["last_severity"] or "info",
                    alert_count=r["alert_count"] or 0,
                    last_diagnosis=r["last_diagnosis"] or "",
                )
                for r in rows
            ]

    # ------------------------------------------------------------------
    # sessions
    # ------------------------------------------------------------------

    def _ensure_session_owner_with_conn(
        self,
        c: sqlite3.Connection,
        session_id: str | None,
        student_id: str,
    ) -> None:
        if not session_id:
            return
        existing = c.execute(
            "SELECT student_id FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        existing_student = existing["student_id"] if existing else None
        if existing_student and existing_student != student_id:
            raise ValueError(
                f"session {session_id!r} belongs to {existing_student!r}, "
                f"not {student_id!r}"
            )

    def upsert_session(
        self,
        session_id: str,
        student_id: str,
        work_dir: str,
        title: str,
        created_at: float | None = None,
        last_activity_at: float | None = None,
        group_type: str | None = None,
        space_name: str | None = None,
    ) -> None:
        now = time.time()
        created = now if created_at is None else created_at
        last_activity = now if last_activity_at is None else last_activity_at
        with self._store._conn() as c:
            self._ensure_session_owner_with_conn(c, session_id, student_id)
            c.execute(
                """INSERT INTO sessions
                   (session_id, student_id, work_dir, title, group_type, space_name,
                    created_at, last_activity_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                     work_dir = CASE
                       WHEN sessions.work_dir IS NULL OR sessions.work_dir = ''
                       THEN excluded.work_dir
                       ELSE sessions.work_dir
                     END,
                     title = COALESCE(NULLIF(excluded.title, ''), sessions.title),
                     group_type = COALESCE(NULLIF(excluded.group_type, ''), sessions.group_type),
                     space_name = COALESCE(NULLIF(excluded.space_name, ''), sessions.space_name),
                     last_activity_at = excluded.last_activity_at""",
                (
                    session_id,
                    student_id,
                    work_dir,
                    title,
                    group_type,
                    space_name,
                    created,
                    last_activity,
                ),
            )

    def get_session_title(self, session_id: str) -> str:
        with self._store._conn() as c:
            row = c.execute(
                "SELECT title FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return (row["title"] or "") if row else ""

    def get_active_session_from_table(
        self,
        work_dir: str | None = None,
        student_id: str | None = None,
    ) -> dict | None:
        clauses = []
        params: list[Any] = []
        if work_dir:
            clauses.append("work_dir = ?")
            params.append(work_dir)
        if student_id:
            clauses.append("student_id = ?")
            params.append(student_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._store._conn() as c:
            row = c.execute(
                f"""SELECT
                      session_id,
                      student_id,
                      work_dir,
                      title,
                      created_at,
                      last_activity_at AS resumed_at
                    FROM sessions
                    {where}
                    ORDER BY last_activity_at DESC, created_at DESC
                    LIMIT 1""",
                params,
            ).fetchone()
            return dict(row) if row else None

    def list_sessions_from_table(
        self,
        work_dir: str | None = None,
        student_id: str | None = None,
        limit: int = 8,
    ) -> list[dict]:
        clauses = []
        params: list[Any] = []
        if work_dir:
            clauses.append("work_dir = ?")
            params.append(work_dir)
        if student_id:
            clauses.append("student_id = ?")
            params.append(student_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._store._conn() as c:
            rows = c.execute(
                f"""SELECT
                      session_id,
                      student_id,
                      work_dir,
                      title,
                      created_at,
                      last_activity_at AS resumed_at
                    FROM sessions
                    {where}
                    ORDER BY last_activity_at DESC, created_at DESC
                    LIMIT ?""",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def get_sessions_by_student(self, student_id: str, limit: int = 1000) -> list[Conversation]:
        return self.get_sessions_by_student_from_table(student_id, limit=limit)

    def get_sessions_by_student_from_table(self, student_id: str, limit: int = 1000) -> list[Conversation]:
        with self._store._conn() as c:
            rows = c.execute(
                """SELECT
                     s.session_id,
                     s.student_id,
                     s.title AS session_title,
                     s.work_dir,
                     s.group_type,
                     s.space_name,
                     s.created_at,
                     s.last_activity_at AS last_ts,
                     COUNT(a.id) AS analysis_count,
                     COALESCE(MAX(mc.c), 0) AS message_count,
                     COALESCE(SUM(CASE
                       WHEN a.alert != '' OR a.understanding IN ('low','stuck')
                       THEN 1 ELSE 0 END), 0) AS alert_count,
                     COALESCE((
                       SELECT a2.diagnosis
                       FROM analyses a2
                       WHERE a2.student_id = s.student_id
                         AND a2.session_id = s.session_id
                       ORDER BY a2.created_at DESC, a2.id DESC
                       LIMIT 1
                     ), '') AS last_diagnosis,
                     COALESCE((
                       SELECT a2.topic
                       FROM analyses a2
                       WHERE a2.student_id = s.student_id
                         AND a2.session_id = s.session_id
                       ORDER BY a2.created_at DESC, a2.id DESC
                       LIMIT 1
                     ), '') AS last_topic,
                     CASE MAX(CASE a.severity
                       WHEN 'error' THEN 3
                       WHEN 'warn' THEN 2
                       ELSE 1
                     END)
                       WHEN 3 THEN 'error'
                       WHEN 2 THEN 'warn'
                       ELSE 'info'
                     END AS last_severity,
                     COALESCE(MAX(a.is_technical), 0) AS last_is_technical
                   FROM sessions s
                   LEFT JOIN analyses a
                     ON a.student_id = s.student_id
                    AND a.session_id = s.session_id
                   LEFT JOIN (
                     SELECT session_id, COUNT(*) AS c
                     FROM messages
                     GROUP BY session_id
                   ) mc
                     ON mc.session_id = s.session_id
                   WHERE s.student_id = ?
                   GROUP BY s.session_id
                   ORDER BY s.last_activity_at DESC, s.created_at DESC
                   LIMIT ?""",
                (student_id, limit),
            ).fetchall()
            return [
                Conversation(
                    session_id=r["session_id"],
                    work_dir=r["work_dir"] or "",
                    title=r["session_title"] or "",
                    group_type=r["group_type"] or "",
                    space_name=r["space_name"] or "",
                    created_at=r["created_at"] or 0,
                    analysis_count=r["analysis_count"] or 0,
                    message_count=r["message_count"] or 0,
                    alert_count=r["alert_count"] or 0,
                    last_diagnosis=r["last_diagnosis"] or "",
                    last_topic=r["last_topic"] or "",
                    last_severity=r["last_severity"] or "info",
                    last_is_technical=r["last_is_technical"] or 0,
                    last_activity_at=r["last_ts"] or 0,
                )
                for r in rows
            ]

    def sessions_overview(self, student_id: str | None = None, limit: int = 10) -> list[Conversation]:
        sid = student_id or "student-1"
        return self.get_sessions_by_student(sid, limit=limit)

    def get_recent_sessions(self, limit: int = 2) -> list[dict]:
        with self._store._conn() as c:
            rows = c.execute(
                """SELECT *
                   FROM sessions
                   ORDER BY COALESCE(last_activity_at, created_at, 0) DESC,
                            created_at DESC,
                            session_id ASC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # reports / analyses
    # ------------------------------------------------------------------

    def add_report(
        self,
        student_id: str,
        session_id: str | None,
        event: str,
        prompt: str,
        transcript_path: str,
        msg_count: int,
        tool_calls: int,
    ) -> int:
        with self._store._conn() as c:
            return self._add_report_with_conn(
                c,
                student_id=student_id,
                session_id=session_id,
                event=event,
                prompt=prompt,
                transcript_path=transcript_path,
                msg_count=msg_count,
                tool_calls=tool_calls,
            )

    def _add_report_with_conn(
        self,
        c: sqlite3.Connection,
        *,
        student_id: str,
        session_id: str | None,
        event: str,
        transcript_path: str,
        msg_count: int,
        tool_calls: int,
        prompt: str = "",
    ) -> int:
        now = time.time()
        c.execute(
            """INSERT INTO students (student_id, display_name, token_hash, created_at)
               VALUES (?, ?, NULL, ?)
               ON CONFLICT(student_id) DO NOTHING""",
            (student_id, "", now),
        )
        cur = c.execute(
            """INSERT INTO reports
               (student_id, session_id, event, prompt, transcript_path,
                msg_count, tool_calls, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                student_id,
                session_id,
                event,
                prompt,
                transcript_path,
                msg_count,
                tool_calls,
                now,
            ),
        )
        return int(cur.lastrowid)

    def add_analysis(
        self,
        report_id: int,
        student_id: str,
        result: dict[str, Any],
        session_id: str | None = None,
        session_title: str | None = None,
    ) -> int:
        with self._store._conn() as c:
            return self._add_analysis_with_conn(
                c,
                report_id=report_id,
                student_id=student_id,
                result=result,
                session_id=session_id,
                session_title=session_title,
            )

    def _add_analysis_with_conn(
        self,
        c: sqlite3.Connection,
        *,
        report_id: int,
        student_id: str,
        result: dict[str, Any],
        session_id: str | None,
        session_title: str | None,
    ) -> int:
        cur = c.execute(
            """INSERT INTO analyses
               (report_id, student_id, session_id, session_title,
                topic, understanding, off_topic, stuck_at,
                is_technical, severity, diagnosis, suggestion,
                progress, guidance, alert, raw, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                report_id,
                student_id,
                session_id,
                session_title,
                result.get("topic", ""),
                result.get("understanding", "unknown"),
                1 if result.get("off_topic") else 0,
                result.get("stuck_at", ""),
                1 if result.get("is_technical") else 0,
                result.get("severity", "info"),
                result.get("diagnosis", ""),
                result.get("suggestion", ""),
                result.get("progress", ""),
                result.get("guidance", ""),
                result.get("alert", ""),
                json.dumps(result, ensure_ascii=False),
                time.time(),
            ),
        )
        return int(cur.lastrowid)

    def set_analysis_pending(self, report_id: int, pending: bool) -> int:
        with self._store._conn() as c:
            cur = c.execute(
                "UPDATE reports SET analysis_pending = ? WHERE id = ?",
                (1 if pending else 0, report_id),
            )
            return cur.rowcount

    def list_pending_reports(self) -> list[dict]:
        with self._store._conn() as c:
            rows = c.execute(
                """SELECT * FROM reports
                   WHERE analysis_pending = 1
                   ORDER BY id ASC"""
            ).fetchall()
            return [dict(r) for r in rows]

    def analysis_exists_for_report(self, report_id: int) -> bool:
        with self._store._conn() as c:
            row = c.execute(
                "SELECT 1 FROM analyses WHERE report_id = ? LIMIT 1",
                (report_id,),
            ).fetchone()
            return row is not None

    def recent_analyses(
        self,
        student_id: str | None,
        limit: int = 20,
        session_id: str | None = None,
    ) -> list[dict]:
        with self._store._conn() as c:
            clauses = []
            params: list = []
            if student_id:
                clauses.append("a.student_id = ?")
                params.append(student_id)
            if session_id:
                clauses.append("a.session_id = ?")
                params.append(session_id)
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            params.append(limit)
            rows = c.execute(
                f"""SELECT a.*, r.event, r.prompt, r.created_at AS report_at
                    FROM analyses a JOIN reports r ON a.report_id = r.id
                    {where}
                    ORDER BY a.created_at DESC LIMIT ?""",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def latest_for_student(self, student_id: str) -> dict | None:
        rows = self.recent_analyses(student_id, limit=1)
        return rows[0] if rows else None

    def unread_alerts(self, since_ts: float, student_id: str | None = None) -> list[dict]:
        with self._store._conn() as c:
            if student_id:
                rows = c.execute(
                    """SELECT * FROM analyses
                       WHERE created_at > ? AND student_id = ?
                         AND (alert != '' OR understanding IN ('low', 'stuck'))
                       ORDER BY created_at DESC LIMIT 50""",
                    (since_ts, student_id),
                ).fetchall()
            else:
                rows = c.execute(
                    """SELECT * FROM analyses
                       WHERE created_at > ?
                         AND (alert != '' OR understanding IN ('low', 'stuck'))
                       ORDER BY created_at DESC LIMIT 50""",
                    (since_ts,),
                ).fetchall()
            return [dict(r) for r in rows]

    def commit_bulk_analysis_if_current(
        self,
        *,
        student_id: str,
        session_id: str,
        content_sha256: str,
        result: dict[str, Any],
        session_title: str,
        msg_count: int,
    ) -> int | None:
        with self._store._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            raw_row = c.execute(
                """SELECT id, content_sha256
                   FROM raw_transcripts
                   WHERE student_id = ? AND session_id = ?
                     AND content_sha256 IS NOT NULL AND content_sha256 != ''
                   ORDER BY created_at DESC, id DESC
                   LIMIT 1""",
                (student_id, session_id),
            ).fetchone()
            if not raw_row or str(raw_row["content_sha256"]) != content_sha256:
                return None

            report_id = self._add_report_with_conn(
                c,
                student_id=student_id,
                session_id=session_id,
                event="BulkUpload",
                transcript_path="",
                msg_count=msg_count,
                tool_calls=0,
            )
            self._add_analysis_with_conn(
                c,
                report_id=report_id,
                student_id=student_id,
                result=result,
                session_id=session_id,
                session_title=session_title,
            )
            updated = c.execute(
                """UPDATE raw_transcripts
                   SET analysis_status = 'done', analysis_error = ''
                   WHERE id = ? AND content_sha256 = ?""",
                (raw_row["id"], content_sha256),
            ).rowcount
            if updated != 1:
                raise sqlite3.IntegrityError("bulk transcript changed during analysis commit")
            return report_id

    # ------------------------------------------------------------------
    # prompts
    # ------------------------------------------------------------------

    def add_prompt(
        self,
        session_id: str,
        seq_in_session: int,
        student_id: str,
        content: str,
    ) -> int:
        with self._store._conn() as c:
            cur = c.execute(
                """INSERT INTO prompts
                   (session_id, seq_in_session, student_id, content, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, seq_in_session, student_id, content, time.time()),
            )
            return cur.lastrowid

    def get_prompt(self, prompt_id: int) -> dict | None:
        with self._store._conn() as c:
            row = c.execute(
                "SELECT * FROM prompts WHERE id = ?",
                (prompt_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_prompts_by_session(self, session_id: str) -> list[dict]:
        with self._store._conn() as c:
            rows = c.execute(
                """SELECT * FROM prompts
                   WHERE session_id = ?
                   ORDER BY seq_in_session ASC, created_at ASC""",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_prompt_for_report(self, report_id: int) -> dict | None:
        with self._store._conn() as c:
            row = c.execute(
                "SELECT * FROM prompts WHERE report_id = ? LIMIT 1",
                (report_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_or_create_prompt_for_report(
        self,
        *,
        report_id: int,
        session_id: str,
        student_id: str,
        content: str,
    ) -> tuple[dict, bool]:
        with self._store._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            existing = c.execute(
                "SELECT * FROM prompts WHERE report_id = ? LIMIT 1",
                (report_id,),
            ).fetchone()
            if existing:
                return dict(existing), False

            seq_row = c.execute(
                """SELECT COALESCE(MAX(seq_in_session), -1) + 1 AS next_seq
                   FROM prompts WHERE session_id = ?""",
                (session_id,),
            ).fetchone()
            seq = int(seq_row["next_seq"])
            cur = c.execute(
                """INSERT INTO prompts
                   (report_id, session_id, seq_in_session, student_id, content, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (report_id, session_id, seq, student_id, content, time.time()),
            )
            row = c.execute(
                "SELECT * FROM prompts WHERE id = ?",
                (cur.lastrowid,),
            ).fetchone()
            return dict(row), True

    # ------------------------------------------------------------------
    # ai_summaries
    # ------------------------------------------------------------------

    def add_ai_summary(
        self,
        prompt_id: int | None,
        session_id: str,
        student_id: str,
        content: str,
    ) -> int:
        return self.upsert_ai_summary(prompt_id, session_id, student_id, content)

    def upsert_ai_summary(
        self,
        prompt_id: int | None,
        session_id: str,
        student_id: str,
        content: str,
    ) -> int:
        now = time.time()
        with self._store._conn() as c:
            if prompt_id is not None:
                existing = c.execute(
                    """SELECT id FROM ai_summaries
                       WHERE prompt_id = ?
                       ORDER BY created_at ASC, id ASC
                       LIMIT 1""",
                    (prompt_id,),
                ).fetchone()
                if existing:
                    summary_id = int(existing["id"])
                    c.execute(
                        """UPDATE ai_summaries
                           SET session_id = ?, student_id = ?, content = ?, created_at = ?
                           WHERE id = ?""",
                        (session_id, student_id, content, now, summary_id),
                    )
                    c.execute(
                        "DELETE FROM ai_summaries WHERE prompt_id = ? AND id != ?",
                        (prompt_id, summary_id),
                    )
                    return summary_id
            cur = c.execute(
                """INSERT INTO ai_summaries
                   (prompt_id, session_id, student_id, content, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (prompt_id, session_id, student_id, content, now),
            )
            return cur.lastrowid

    def get_ai_summaries_by_session(self, session_id: str) -> list[dict]:
        with self._store._conn() as c:
            rows = c.execute(
                """SELECT * FROM ai_summaries
                   WHERE session_id = ?
                   ORDER BY created_at ASC""",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # raw_transcripts
    # ------------------------------------------------------------------

    def add_raw_transcript(
        self,
        session_id: str,
        student_id: str,
        content: str,
        content_sha256: str | None = None,
    ) -> int:
        with self._store._conn() as c:
            self._ensure_session_owner_with_conn(c, session_id, student_id)
            cur = c.execute(
                """INSERT INTO raw_transcripts
                   (session_id, student_id, content, content_sha256,
                    analysis_status, analysis_error, created_at)
                   VALUES (?, ?, ?, ?, '', '', ?)""",
                (session_id, student_id, content, content_sha256, time.time()),
            )
            return cur.lastrowid

    def get_raw_transcript(self, session_id: str) -> dict | None:
        with self._store._conn() as c:
            row = c.execute(
                """SELECT * FROM raw_transcripts
                   WHERE session_id = ?
                   ORDER BY created_at DESC, id DESC
                   LIMIT 1""",
                (session_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_raw_transcript_for_student_session(self, student_id: str, session_id: str) -> dict | None:
        with self._store._conn() as c:
            row = c.execute(
                """SELECT * FROM raw_transcripts
                   WHERE student_id = ? AND session_id = ?
                   ORDER BY created_at DESC, id DESC
                   LIMIT 1""",
                (student_id, session_id),
            ).fetchone()
            return dict(row) if row else None

    def get_raw_transcript_for_student_session_sha(
        self,
        student_id: str,
        session_id: str,
        content_sha256: str,
    ) -> dict | None:
        with self._store._conn() as c:
            row = c.execute(
                """SELECT * FROM raw_transcripts
                   WHERE student_id = ? AND session_id = ? AND content_sha256 = ?
                   ORDER BY created_at DESC, id DESC
                   LIMIT 1""",
                (student_id, session_id, content_sha256),
            ).fetchone()
            return dict(row) if row else None

    def get_raw_transcript_for_report(
        self,
        session_id: str,
        report_created_at: float | None,
    ) -> dict | None:
        if report_created_at is None:
            return self.get_raw_transcript(session_id)
        with self._store._conn() as c:
            row = c.execute(
                """SELECT * FROM raw_transcripts
                   WHERE session_id = ? AND created_at >= ?
                   ORDER BY created_at ASC, id ASC
                   LIMIT 1""",
                (session_id, report_created_at),
            ).fetchone()
            if row:
                return dict(row)
            return None
        return self.get_raw_transcript(session_id)

    def set_raw_transcript_analysis_status(
        self,
        session_id: str,
        student_id: str,
        *,
        status: str,
        error_message: str | None = None,
        content_sha256: str | None = None,
    ) -> int:
        where = "session_id = ? AND student_id = ?"
        params: list[Any] = [session_id, student_id]
        if content_sha256:
            where += " AND content_sha256 = ?"
            params.append(content_sha256)
        with self._store._conn() as c:
            row = c.execute(
                f"""SELECT id FROM raw_transcripts
                    WHERE {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1""",
                params,
            ).fetchone()
            if not row:
                return 0
            cur = c.execute(
                """UPDATE raw_transcripts
                   SET analysis_status = ?, analysis_error = ?
                   WHERE id = ?""",
                (status, error_message or "", row["id"]),
            )
            return cur.rowcount

    def replace_session_messages(
        self,
        session_id: str,
        student_id: str,
        turns: list[dict[str, Any]],
        raw: str,
        sha: str,
    ) -> int:
        now = time.time()
        with self._store._conn() as c:
            self._ensure_session_owner_with_conn(c, session_id, student_id)
            c.execute(
                """INSERT INTO students (student_id, display_name, token_hash, created_at)
                   VALUES (?, '', NULL, ?)
                   ON CONFLICT(student_id) DO NOTHING""",
                (student_id, now),
            )
            c.execute(
                """INSERT INTO sessions
                   (session_id, student_id, work_dir, title, created_at, last_activity_at)
                   VALUES (?, ?, '', '', ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                     last_activity_at = CASE
                       WHEN sessions.last_activity_at IS NULL
                         OR sessions.last_activity_at < excluded.last_activity_at
                       THEN excluded.last_activity_at
                       ELSE sessions.last_activity_at
                    END""",
                (session_id, student_id, now, now),
            )
            preserved_summaries: dict[tuple[int, str], str] = {}
            if sha:
                rows = c.execute(
                    """SELECT seq, text, summary
                       FROM messages
                       WHERE session_id = ?
                         AND source = 'bulk'
                         AND role = 'user'
                         AND content_sha256 = ?
                         AND COALESCE(summary, '') != ''""",
                    (session_id, sha),
                ).fetchall()
                preserved_summaries = {
                    (int(row["seq"]), str(row["text"] or "")): str(row["summary"] or "")
                    for row in rows
                }
            c.execute(
                "DELETE FROM messages WHERE session_id = ? AND source = 'bulk'",
                (session_id,),
            )

            inserted = 0
            for idx, turn in enumerate(turns):
                role = str(turn.get("role") or "")
                if role not in {"user", "assistant"}:
                    continue
                text = str(turn.get("text") or "")
                if not text:
                    continue
                seq = int(turn.get("seq") or 0)
                ts = turn.get("ts")
                created_at = ts if isinstance(ts, (int, float)) else now + (idx / 1000.0)
                summary = preserved_summaries.get((seq, text)) if role == "user" else None
                c.execute(
                    """INSERT INTO messages
                       (session_id, student_id, seq, role, text, summary, source,
                        content_sha256, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'bulk', ?, ?)""",
                    (session_id, student_id, seq, role, text, summary, sha, float(created_at)),
                )
                inserted += 1

            raw_row = c.execute(
                """SELECT id FROM raw_transcripts
                   WHERE session_id = ? AND student_id = ?
                   ORDER BY created_at DESC, id DESC
                   LIMIT 1""",
                (session_id, student_id),
            ).fetchone()
            if raw_row:
                c.execute(
                    """UPDATE raw_transcripts
                       SET content = ?, content_sha256 = ?, created_at = ?,
                           analysis_status = '', analysis_error = ''
                       WHERE id = ?""",
                    (raw, sha, now, raw_row["id"]),
                )
            else:
                c.execute(
                    """INSERT INTO raw_transcripts
                       (session_id, student_id, content, content_sha256,
                        analysis_status, analysis_error, created_at)
                       VALUES (?, ?, ?, ?, '', '', ?)""",
                    (session_id, student_id, raw, sha, now),
                )
            return inserted

    # ------------------------------------------------------------------
    # student_asks
    # ------------------------------------------------------------------

    def add_student_ask(
        self,
        student_id: str,
        session_id: str | None,
        question: str,
        answer: str,
    ) -> int:
        self.upsert_student(student_id)
        with self._store._conn() as c:
            cur = c.execute(
                """INSERT INTO student_asks
                   (student_id, session_id, question, answer, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (student_id, session_id, question, answer, time.time()),
            )
            return cur.lastrowid

    def list_student_asks(self, student_id: str, session_id: str | None = None) -> list[dict]:
        with self._store._conn() as c:
            params: list[Any] = [student_id]
            where = "student_id = ?"
            if session_id is not None:
                where += " AND session_id = ?"
                params.append(session_id)
            rows = c.execute(
                f"""SELECT * FROM student_asks
                    WHERE {where}
                    ORDER BY created_at DESC, id DESC""",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # prompt_configs
    # ------------------------------------------------------------------

    def set_prompt_config(
        self,
        key: str,
        prompt: str,
        updated_by: str | None = None,
    ) -> str:
        now = time.time()
        normalized_key = str(key or "").strip()
        if not normalized_key:
            raise ValueError("prompt config key is required")
        with self._store._conn() as c:
            c.execute(
                """INSERT INTO prompt_configs
                   (key, prompt, updated_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                     prompt = excluded.prompt,
                     updated_by = excluded.updated_by,
                     updated_at = excluded.updated_at""",
                (normalized_key, prompt, updated_by or "", now, now),
            )
        return normalized_key

    def get_prompt_config(self, key: str) -> dict | None:
        normalized_key = str(key or "").strip()
        if not normalized_key:
            return None
        with self._store._conn() as c:
            row = c.execute(
                "SELECT * FROM prompt_configs WHERE key = ?",
                (normalized_key,),
            ).fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # messages (bulk round queries)
    # ------------------------------------------------------------------

    def _get_prompt_reply_with_conn(
        self,
        c: sqlite3.Connection,
        session_id: str,
        prompt_seq: int,
    ) -> str:
        user_rows = c.execute(
            """SELECT id, seq, created_at
               FROM messages
               WHERE session_id = ? AND role = 'user'
               ORDER BY seq ASC, created_at ASC, id ASC""",
            (session_id,),
        ).fetchall()
        if not user_rows:
            return ""

        anchor_index = next(
            (idx for idx, row in enumerate(user_rows) if int(row["seq"]) == int(prompt_seq)),
            None,
        )
        if anchor_index is None:
            if prompt_seq < 0 or prompt_seq >= len(user_rows):
                return ""
            anchor_index = prompt_seq

        anchor = user_rows[anchor_index]
        next_user = user_rows[anchor_index + 1] if anchor_index + 1 < len(user_rows) else None

        where = [
            "session_id = ?",
            "role = 'assistant'",
            "(seq > ? OR (seq = ? AND created_at >= ?))",
        ]
        params: list[Any] = [
            session_id,
            int(anchor["seq"]),
            int(anchor["seq"]),
            float(anchor["created_at"] or 0),
        ]
        if next_user is not None:
            where.append("(seq < ? OR (seq = ? AND created_at < ?))")
            params.extend([
                int(next_user["seq"]),
                int(next_user["seq"]),
                float(next_user["created_at"] or 0),
            ])

        rows = c.execute(
            f"""SELECT text
                FROM messages
                WHERE {' AND '.join(where)}
                ORDER BY seq ASC, created_at ASC, id ASC""",
            params,
        ).fetchall()
        return "\n\n".join(str(row["text"]).strip() for row in rows if str(row["text"]).strip())

    def get_prompt_reply(self, session_id: str, prompt_seq: int) -> str:
        with self._store._conn() as c:
            return self._get_prompt_reply_with_conn(c, session_id, int(prompt_seq))

    def get_prompt_reply_by_id(self, prompt_id: int) -> str:
        prompt = self.get_prompt(prompt_id)
        if not prompt:
            return ""
        return self.get_prompt_reply(
            str(prompt.get("session_id") or ""),
            int(prompt.get("seq_in_session") or 0),
        )

    def _message_reply_rows_with_conn(
        self,
        c: sqlite3.Connection,
        message_id: int,
    ) -> list[sqlite3.Row]:
        anchor = c.execute(
            """SELECT id, session_id, seq, created_at
               FROM messages
               WHERE id = ? AND role = 'user'""",
            (int(message_id),),
        ).fetchone()
        if not anchor:
            return []

        next_user = c.execute(
            """SELECT id, seq, created_at
               FROM messages
               WHERE session_id = ?
                 AND role = 'user'
                 AND (seq > ? OR (seq = ? AND created_at > ?) OR (seq = ? AND created_at = ? AND id > ?))
               ORDER BY seq ASC, created_at ASC, id ASC
               LIMIT 1""",
            (
                anchor["session_id"],
                int(anchor["seq"]),
                int(anchor["seq"]),
                float(anchor["created_at"] or 0),
                int(anchor["seq"]),
                float(anchor["created_at"] or 0),
                int(anchor["id"]),
            ),
        ).fetchone()

        where = [
            "session_id = ?",
            "role = 'assistant'",
            "(seq > ? OR (seq = ? AND created_at >= ?))",
        ]
        params: list[Any] = [
            anchor["session_id"],
            int(anchor["seq"]),
            int(anchor["seq"]),
            float(anchor["created_at"] or 0),
        ]
        if next_user is not None:
            where.append("(seq < ? OR (seq = ? AND created_at < ?))")
            params.extend([
                int(next_user["seq"]),
                int(next_user["seq"]),
                float(next_user["created_at"] or 0),
            ])

        return c.execute(
            f"""SELECT id, text, created_at
                FROM messages
                WHERE {' AND '.join(where)}
                ORDER BY seq ASC, created_at ASC, id ASC""",
            params,
        ).fetchall()

    def get_message_reply_by_id(self, message_id: int) -> str:
        with self._store._conn() as c:
            rows = self._message_reply_rows_with_conn(c, int(message_id))
            return "\n\n".join(str(row["text"]).strip() for row in rows if str(row["text"]).strip())

    def get_message_rounds_by_session(self, session_id: str) -> list[dict]:
        with self._store._conn() as c:
            rows = c.execute(
                """SELECT id, session_id, student_id, seq, text, summary,
                          content_sha256, created_at
                   FROM messages
                   WHERE session_id = ? AND role = 'user'
                   ORDER BY seq ASC, created_at ASC, id ASC""",
                (session_id,),
            ).fetchall()
            rounds: list[dict[str, Any]] = []
            for row in rows:
                reply_rows = self._message_reply_rows_with_conn(c, int(row["id"]))
                rounds.append({
                    "id": int(row["id"]),
                    "session_id": row["session_id"],
                    "student_id": row["student_id"],
                    "seq": int(row["seq"]),
                    "content": row["text"],
                    "summary": row["summary"] or "",
                    "content_sha256": row["content_sha256"],
                    "created_at": row["created_at"],
                    "reply": "\n\n".join(
                        str(reply_row["text"]).strip()
                        for reply_row in reply_rows
                        if str(reply_row["text"]).strip()
                    ),
                    "reply_created_at": (
                        float(reply_rows[0]["created_at"] or 0)
                        if reply_rows else None
                    ),
                })
            return rounds

    def set_message_summary(self, message_id: int, summary: str) -> int:
        with self._store._conn() as c:
            cur = c.execute(
                """UPDATE messages
                   SET summary = ?
                   WHERE id = ? AND role = 'user'""",
                (summary, int(message_id)),
            )
            return cur.rowcount

    # ------------------------------------------------------------------
    # timeline
    # ------------------------------------------------------------------

    def _analysis_timeline_rows(self, c: sqlite3.Connection, session_id: str) -> list[dict]:
        rows = c.execute(
            """SELECT a.id, a.session_id, a.student_id,
                      COALESCE(a.diagnosis, '') AS content, a.created_at,
                      'analysis' AS type,
                      NULL AS seq_in_session,
                      NULL AS prompt_id,
                      NULL AS reply_ref,
                      a.report_id,
                      a.severity,
                      a.understanding,
                      a.suggestion,
                      a.is_technical,
                      a.topic,
                      NULL AS has_summary,
                      NULL AS has_full_reply
               FROM analyses a
               WHERE a.session_id = ?""",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def _prompt_timeline_rows(self, c: sqlite3.Connection, session_id: str) -> list[dict]:
        prompts = c.execute(
            """SELECT *
               FROM prompts
               WHERE session_id = ?
               ORDER BY seq_in_session ASC, created_at ASC, id ASC""",
            (session_id,),
        ).fetchall()
        events: list[dict[str, Any]] = []
        for prompt in prompts:
            prompt_id = int(prompt["id"])
            prompt_seq = int(prompt["seq_in_session"] or 0)
            has_full_reply = 1 if self._get_prompt_reply_with_conn(c, session_id, prompt_seq) else 0
            events.append({
                "id": prompt_id,
                "session_id": prompt["session_id"],
                "student_id": prompt["student_id"],
                "content": prompt["content"],
                "created_at": prompt["created_at"],
                "type": "prompt",
                "seq_in_session": prompt_seq,
                "prompt_id": prompt_id,
                "reply_ref": None,
                "report_id": None,
                "severity": None,
                "understanding": None,
                "suggestion": None,
                "is_technical": None,
                "topic": None,
                "has_summary": None,
                "has_full_reply": has_full_reply,
            })
            summary = c.execute(
                """SELECT *
                   FROM ai_summaries
                   WHERE prompt_id = ?
                     AND COALESCE(content, '') != ''
                   ORDER BY created_at DESC, id DESC
                   LIMIT 1""",
                (prompt_id,),
            ).fetchone()
            if summary:
                events.append({
                    "id": summary["id"],
                    "session_id": summary["session_id"],
                    "student_id": summary["student_id"],
                    "content": summary["content"],
                    "created_at": summary["created_at"],
                    "type": "ai_summary",
                    "seq_in_session": prompt_seq,
                    "prompt_id": prompt_id,
                    "reply_ref": f"prompt:{prompt_id}",
                    "report_id": None,
                    "severity": None,
                    "understanding": None,
                    "suggestion": None,
                    "is_technical": None,
                    "topic": None,
                    "has_summary": 1,
                    "has_full_reply": has_full_reply,
                })
        return events

    def _bulk_message_timeline_rows(self, c: sqlite3.Connection, session_id: str) -> list[dict]:
        users = c.execute(
            """SELECT id, session_id, student_id, seq, text, summary, created_at
               FROM messages
               WHERE session_id = ? AND source = 'bulk' AND role = 'user'
               ORDER BY seq ASC, created_at ASC, id ASC""",
            (session_id,),
        ).fetchall()
        events: list[dict[str, Any]] = []
        for row in users:
            text = str(row["text"] or "").strip()
            if not text:
                continue
            prompt_seq = int(row["seq"])
            message_id = int(row["id"])
            reply_rows = self._message_reply_rows_with_conn(c, message_id)
            has_full_reply = 1 if any(str(reply["text"] or "").strip() for reply in reply_rows) else 0
            summary = str(row["summary"] or "").strip()
            events.append({
                "id": message_id,
                "session_id": row["session_id"],
                "student_id": row["student_id"],
                "content": text,
                "created_at": row["created_at"],
                "type": "prompt",
                "seq_in_session": prompt_seq,
                "prompt_id": None,
                "reply_ref": None,
                "report_id": None,
                "severity": None,
                "understanding": None,
                "suggestion": None,
                "is_technical": None,
                "topic": None,
                "has_summary": None,
                "has_full_reply": has_full_reply,
            })
            if has_full_reply:
                events.append({
                    "id": message_id,
                    "session_id": row["session_id"],
                    "student_id": row["student_id"],
                    "content": summary,
                    "created_at": float(reply_rows[0]["created_at"] or 0),
                    "type": "ai_summary",
                    "seq_in_session": prompt_seq,
                    "prompt_id": None,
                    "reply_ref": f"msg:{message_id}",
                    "report_id": None,
                    "severity": None,
                    "understanding": None,
                    "suggestion": None,
                    "is_technical": None,
                    "topic": None,
                    "has_summary": 1 if summary else 0,
                    "has_full_reply": has_full_reply,
                })
        return events

    def _mentor_message_timeline_rows(self, c: sqlite3.Connection, session_id: str) -> list[dict]:
        # mentor_messages 按 student_id 关联，因为发送时可能不绑定具体 session
        student_row = c.execute(
            "SELECT student_id FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        student_id = student_row["student_id"] if student_row else ""
        rows = c.execute(
            """SELECT id, session_id, student_id, text AS content, created_at,
                      'mentor_message' AS type,
                      NULL AS seq_in_session,
                      NULL AS prompt_id,
                      NULL AS reply_ref,
                      NULL AS report_id,
                      NULL AS severity,
                      NULL AS understanding,
                      NULL AS suggestion,
                      NULL AS is_technical,
                      NULL AS topic,
                      NULL AS has_summary,
                      NULL AS has_full_reply,
                      mentor_id AS mentor_id,
                      message_id AS message_id,
                      delivered_at AS delivered_at
               FROM mentor_messages
               WHERE student_id = ?""",
            (student_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_timeline_by_session(self, session_id: str) -> list[TimelineEntry]:
        with self._store._conn() as c:
            events = self._prompt_timeline_rows(c, session_id)
            if not events:
                events = self._bulk_message_timeline_rows(c, session_id)
            events.extend(self._analysis_timeline_rows(c, session_id))
            events.extend(self._mentor_message_timeline_rows(c, session_id))
            if events:
                priority = {"prompt": 0, "ai_summary": 1, "analysis": 2}
                events.sort(key=lambda item: (
                    float(item.get("created_at") or 0),
                    priority.get(str(item.get("type") or ""), 99),
                    int(item.get("id") or 0),
                ))
            return [
                TimelineEntry(
                    type=r.get("type", ""),
                    content=r.get("content", ""),
                    created_at=r.get("created_at", 0),
                    session_id=r.get("session_id", session_id),
                    seq_in_session=r.get("seq_in_session"),
                    prompt_id=r.get("prompt_id"),
                    reply_ref=r.get("reply_ref"),
                    has_summary=bool(r.get("has_summary", False)),
                    has_full_reply=bool(r.get("has_full_reply", False)),
                    suggestion=r.get("suggestion", ""),
                    severity=r.get("severity", ""),
                    understanding=r.get("understanding", "") or "",
                    topic=r.get("topic", "") or "",
                    is_technical=bool(r.get("is_technical", 0)),
                    delivered_at=r.get("delivered_at"),
                    mentor_id=r.get("mentor_id", ""),
                    message_id=r.get("message_id", ""),
                )
                for r in events
            ]