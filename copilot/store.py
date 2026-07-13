"""SQLite persistence: schema, migrations, and facade over domain sub-stores.

The Store class owns the database connection, schema creation, and migration
logic. It exposes three domain sub-stores (sessions, messages, uploads) and
delegates all public read/write methods to them for backward compatibility.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from .models import Conversation, Student, TimelineEntry
from .session_store import SessionStore
from .message_store import MessageStore
from .upload_store import UploadStore, UploadRetryClaimConflict

log = logging.getLogger("copilot.store")

LEGACY_PROMPT_BACKFILL_WINDOW_SECONDS = 30.0


SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    session_id TEXT,
    event TEXT,
    prompt TEXT,
    transcript_path TEXT,
    msg_count INTEGER,
    tool_calls INTEGER,
    analysis_pending INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    student_id TEXT NOT NULL,
    session_id TEXT,
    session_title TEXT,
    topic TEXT,
    understanding TEXT,
    off_topic INTEGER,
    stuck_at TEXT,
    is_technical INTEGER DEFAULT 0,
    severity TEXT DEFAULT 'info',
    diagnosis TEXT,
    suggestion TEXT,
    progress TEXT,
    guidance TEXT,
    alert TEXT,
    raw TEXT,
    created_at REAL NOT NULL,
    FOREIGN KEY (report_id) REFERENCES reports(id)
);

CREATE INDEX IF NOT EXISTS idx_reports_student ON reports(student_id, created_at);
CREATE INDEX IF NOT EXISTS idx_analyses_student ON analyses(student_id, created_at);

CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER,
    session_id TEXT,
    seq_in_session INTEGER,
    student_id TEXT,
    content TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_id INTEGER,
    session_id TEXT,
    student_id TEXT,
    content TEXT,
    created_at REAL NOT NULL,
    FOREIGN KEY (prompt_id) REFERENCES prompts(id)
);

CREATE TABLE IF NOT EXISTS students (
    student_id TEXT PRIMARY KEY,
    display_name TEXT,
    token_hash TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    student_id TEXT,
    work_dir TEXT,
    title TEXT,
    group_type TEXT,
    space_name TEXT,
    created_at REAL,
    last_activity_at REAL
);

CREATE TABLE IF NOT EXISTS mentor_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT,
    mentor_id TEXT,
    session_id TEXT,
    text TEXT,
    message_id TEXT UNIQUE,
    created_at REAL,
    delivered_at REAL,
    read_at REAL,
    FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS raw_transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    student_id TEXT,
    content TEXT,
    content_sha256 TEXT,
    analysis_status TEXT DEFAULT '',
    analysis_error TEXT DEFAULT '',
    created_at REAL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    summary TEXT,
    source TEXT DEFAULT 'bulk',
    content_sha256 TEXT,
    created_at REAL NOT NULL,
    UNIQUE(session_id, seq, role)
);

CREATE TABLE IF NOT EXISTS upload_requests (
    request_id TEXT PRIMARY KEY,
    mentor_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    session_id TEXT,
    status TEXT NOT NULL,
    transfer_status TEXT NOT NULL DEFAULT 'pending',
    analysis_status TEXT NOT NULL DEFAULT 'not_requested',
    error_message TEXT DEFAULT '',
    transfer_error TEXT DEFAULT '',
    analysis_error TEXT DEFAULT '',
    result_json TEXT,
    updated_at REAL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS upload_request_sessions (
    request_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    sha TEXT NOT NULL,
    analysis_status TEXT NOT NULL DEFAULT 'pending',
    analysis_error TEXT DEFAULT '',
    updated_at REAL NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (request_id, session_id),
    FOREIGN KEY (request_id) REFERENCES upload_requests(request_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS student_asks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    session_id TEXT,
    question TEXT,
    answer TEXT,
    created_at REAL NOT NULL,
    FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS prompt_configs (
    key TEXT PRIMARY KEY,
    prompt TEXT NOT NULL,
    updated_by TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""

_MIGRATIONS = [
    ("reports", "analysis_pending", "INTEGER DEFAULT 0"),
    ("analyses", "session_id", "TEXT"),
    ("analyses", "session_title", "TEXT"),
    ("analyses", "is_technical", "INTEGER DEFAULT 0"),
    ("analyses", "severity", "TEXT DEFAULT 'info'"),
    ("analyses", "diagnosis", "TEXT"),
    ("analyses", "suggestion", "TEXT"),
    ("prompts", "report_id", "INTEGER"),
    ("sessions", "group_type", "TEXT"),
    ("sessions", "space_name", "TEXT"),
    ("raw_transcripts", "content_sha256", "TEXT"),
    ("raw_transcripts", "analysis_status", "TEXT DEFAULT ''"),
    ("raw_transcripts", "analysis_error", "TEXT DEFAULT ''"),
    ("messages", "summary", "TEXT"),
    ("upload_requests", "error_message", "TEXT DEFAULT ''"),
    ("upload_requests", "result_json", "TEXT"),
    ("upload_requests", "updated_at", "REAL"),
    ("upload_requests", "transfer_status", "TEXT NOT NULL DEFAULT 'pending'"),
    ("upload_requests", "analysis_status", "TEXT NOT NULL DEFAULT 'not_requested'"),
    ("upload_requests", "transfer_error", "TEXT DEFAULT ''"),
    ("upload_requests", "analysis_error", "TEXT DEFAULT ''"),
]

_POST_MIGRATION_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_analyses_session ON analyses(session_id, created_at)",
    "UPDATE analyses SET session_id = (SELECT session_id FROM reports WHERE reports.id = analyses.report_id) WHERE analyses.session_id IS NULL",
    "CREATE INDEX IF NOT EXISTS idx_prompts_session ON prompts(session_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_prompts_student ON prompts(student_id, created_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_prompts_report_id_unique ON prompts(report_id) WHERE report_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_ai_summaries_session ON ai_summaries(session_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_ai_summaries_prompt ON ai_summaries(prompt_id)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_student ON sessions(student_id)",
    "CREATE INDEX IF NOT EXISTS idx_mentor_messages_student_delivered ON mentor_messages(student_id, delivered_at)",
    "CREATE INDEX IF NOT EXISTS idx_raw_transcripts_session ON raw_transcripts(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_raw_transcripts_student_sha ON raw_transcripts(student_id, content_sha256)",
    "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_messages_student ON messages(student_id, session_id)",
    "CREATE INDEX IF NOT EXISTS idx_upload_requests_student_status ON upload_requests(student_id, status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_upload_request_sessions_status ON upload_request_sessions(request_id, analysis_status)",
    "CREATE INDEX IF NOT EXISTS idx_student_asks_student ON student_asks(student_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_student_asks_session ON student_asks(session_id, created_at)",
    """INSERT OR IGNORE INTO sessions
       (session_id, student_id, work_dir, title, created_at, last_activity_at)
       SELECT
         a.session_id,
         (SELECT a_student.student_id
          FROM analyses a_student
          WHERE a_student.session_id = a.session_id
          ORDER BY a_student.created_at DESC, a_student.id DESC
          LIMIT 1),
         '',
         COALESCE((
           SELECT a_title.session_title
           FROM analyses a_title
           WHERE a_title.session_id = a.session_id
           ORDER BY a_title.created_at DESC, a_title.id DESC
           LIMIT 1
         ), ''),
         MIN(a.created_at),
         MAX(a.created_at)
       FROM analyses a
       WHERE a.session_id IS NOT NULL AND a.session_id != ''
       GROUP BY a.session_id""",
]


class Store:
    """Central persistence facade that owns schema and delegates to sub-stores."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self.sessions = SessionStore(self)
        self.messages = MessageStore(self)
        self.uploads = UploadStore(self)

    # ------------------------------------------------------------------
    # connection / schema / migration (owned by Store)
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(SCHEMA)
        self._migrate()
        self._backfill_upload_request_axes()
        self._backfill_legacy_prompt_report_ids()
        with self._conn() as c:
            for sql in _POST_MIGRATION_SQL:
                c.execute(sql)

    def _migrate(self) -> None:
        with self._conn() as c:
            for table, col, coltype in _MIGRATIONS:
                cols = {row[1] for row in c.execute(f"PRAGMA table_info({table})").fetchall()}
                if col not in cols:
                    try:
                        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
                    except sqlite3.OperationalError as exc:
                        if "duplicate column name" not in str(exc).lower():
                            raise
                        log.info("migration: %s.%s already exists, skipping", table, col)
                    else:
                        log.info("migration: %s.%s added", table, col)

    def _backfill_upload_request_axes(self) -> None:
        with self._conn() as c:
            c.execute(
                """UPDATE upload_requests
                   SET transfer_status = CASE status
                       WHEN 'running' THEN 'running'
                       WHEN 'done' THEN 'stored'
                       WHEN 'failed' THEN 'failed'
                       ELSE 'pending'
                   END
                   WHERE transfer_status IS NULL
                      OR transfer_status = ''
                      OR (transfer_status = 'pending' AND status != 'pending')"""
            )
            c.execute(
                """UPDATE upload_requests
                   SET analysis_status = 'not_requested'
                   WHERE analysis_status IS NULL OR analysis_status = ''"""
            )
            c.execute(
                """UPDATE upload_requests
                   SET transfer_error = error_message
                   WHERE (transfer_error IS NULL OR transfer_error = '')
                     AND error_message IS NOT NULL
                     AND error_message != ''"""
            )
            c.execute(
                """UPDATE upload_requests
                   SET status = CASE transfer_status
                       WHEN 'running' THEN 'running'
                       WHEN 'stored' THEN 'done'
                       WHEN 'failed' THEN 'failed'
                       ELSE 'pending'
                   END"""
            )
            c.execute(
                """UPDATE upload_requests
                   SET transfer_error = '' WHERE transfer_error IS NULL"""
            )
            c.execute(
                """UPDATE upload_requests
                   SET analysis_error = '' WHERE analysis_error IS NULL"""
            )

    def _backfill_legacy_prompt_report_ids(self) -> None:
        with self._conn() as c:
            reports = [
                dict(row)
                for row in c.execute(
                    """SELECT id, student_id, session_id, prompt, created_at
                       FROM reports
                       WHERE event = 'Stop'
                         AND analysis_pending = 1
                         AND NOT EXISTS (
                           SELECT 1 FROM prompts
                           WHERE prompts.report_id = reports.id
                         )
                       ORDER BY created_at ASC, id ASC"""
                ).fetchall()
            ]
            prompts = [
                dict(row)
                for row in c.execute(
                    """SELECT id, student_id, session_id, content, created_at
                       FROM prompts
                       WHERE report_id IS NULL
                       ORDER BY created_at ASC, id ASC"""
                ).fetchall()
            ]
            report_candidates: dict[int, set[int]] = {}
            prompt_candidates: dict[int, set[int]] = {}
            for report in reports:
                report_prompt = str(report.get("prompt") or "")
                for prompt in prompts:
                    if prompt.get("student_id") != report.get("student_id"):
                        continue
                    if prompt.get("session_id") != report.get("session_id"):
                        continue
                    if report_prompt:
                        if str(prompt.get("content") or "") != report_prompt:
                            continue
                    else:
                        delta = float(prompt["created_at"]) - float(report["created_at"])
                        if not 0 <= delta <= LEGACY_PROMPT_BACKFILL_WINDOW_SECONDS:
                            continue
                    report_id = int(report["id"])
                    prompt_id = int(prompt["id"])
                    report_candidates.setdefault(report_id, set()).add(prompt_id)
                    prompt_candidates.setdefault(prompt_id, set()).add(report_id)

            for report_id, matching_prompts in sorted(report_candidates.items()):
                if len(matching_prompts) != 1:
                    continue
                prompt_id = next(iter(matching_prompts))
                if prompt_candidates.get(prompt_id) != {report_id}:
                    continue
                updated = c.execute(
                    """UPDATE prompts SET report_id = ?
                       WHERE id = ? AND report_id IS NULL""",
                    (report_id, prompt_id),
                ).rowcount
                if updated != 1:
                    log.warning("backfill: prompt %s couldn't pair with report %s", prompt_id, report_id)

    # ------------------------------------------------------------------
    # session domain (delegated to SessionStore)
    # ------------------------------------------------------------------

    def upsert_student(self, *args, **kwargs):
        return self.sessions.upsert_student(*args, **kwargs)

    def delete_student(self, *args, **kwargs):
        return self.sessions.delete_student(*args, **kwargs)

    def students_overview(self, *args, **kwargs):
        return self.sessions.students_overview(*args, **kwargs)

    def _ensure_session_owner_with_conn(self, *args, **kwargs):
        return self.sessions._ensure_session_owner_with_conn(*args, **kwargs)

    def upsert_session(self, *args, **kwargs):
        return self.sessions.upsert_session(*args, **kwargs)

    def get_session_title(self, *args, **kwargs):
        return self.sessions.get_session_title(*args, **kwargs)

    def get_active_session_from_table(self, *args, **kwargs):
        return self.sessions.get_active_session_from_table(*args, **kwargs)

    def list_sessions_from_table(self, *args, **kwargs):
        return self.sessions.list_sessions_from_table(*args, **kwargs)

    def get_sessions_by_student(self, *args, **kwargs):
        return self.sessions.get_sessions_by_student(*args, **kwargs)

    def get_sessions_by_student_from_table(self, *args, **kwargs):
        return self.sessions.get_sessions_by_student_from_table(*args, **kwargs)

    def sessions_overview(self, *args, **kwargs):
        return self.sessions.sessions_overview(*args, **kwargs)

    def get_recent_sessions(self, *args, **kwargs):
        return self.sessions.get_recent_sessions(*args, **kwargs)

    def add_report(self, *args, **kwargs):
        return self.sessions.add_report(*args, **kwargs)

    def _add_report_with_conn(self, *args, **kwargs):
        return self.sessions._add_report_with_conn(*args, **kwargs)

    def add_analysis(self, *args, **kwargs):
        return self.sessions.add_analysis(*args, **kwargs)

    def _add_analysis_with_conn(self, *args, **kwargs):
        return self.sessions._add_analysis_with_conn(*args, **kwargs)

    def set_analysis_pending(self, *args, **kwargs):
        return self.sessions.set_analysis_pending(*args, **kwargs)

    def list_pending_reports(self, *args, **kwargs):
        return self.sessions.list_pending_reports(*args, **kwargs)

    def analysis_exists_for_report(self, *args, **kwargs):
        return self.sessions.analysis_exists_for_report(*args, **kwargs)

    def recent_analyses(self, *args, **kwargs):
        return self.sessions.recent_analyses(*args, **kwargs)

    def latest_for_student(self, *args, **kwargs):
        return self.sessions.latest_for_student(*args, **kwargs)

    def unread_alerts(self, *args, **kwargs):
        return self.sessions.unread_alerts(*args, **kwargs)

    def commit_bulk_analysis_if_current(self, *args, **kwargs):
        return self.sessions.commit_bulk_analysis_if_current(*args, **kwargs)

    def add_prompt(self, *args, **kwargs):
        return self.sessions.add_prompt(*args, **kwargs)

    def get_prompt(self, *args, **kwargs):
        return self.sessions.get_prompt(*args, **kwargs)

    def get_prompts_by_session(self, *args, **kwargs):
        return self.sessions.get_prompts_by_session(*args, **kwargs)

    def get_prompt_for_report(self, *args, **kwargs):
        return self.sessions.get_prompt_for_report(*args, **kwargs)

    def get_or_create_prompt_for_report(self, *args, **kwargs):
        return self.sessions.get_or_create_prompt_for_report(*args, **kwargs)

    def add_ai_summary(self, *args, **kwargs):
        return self.sessions.add_ai_summary(*args, **kwargs)

    def upsert_ai_summary(self, *args, **kwargs):
        return self.sessions.upsert_ai_summary(*args, **kwargs)

    def get_ai_summaries_by_session(self, *args, **kwargs):
        return self.sessions.get_ai_summaries_by_session(*args, **kwargs)

    def add_raw_transcript(self, *args, **kwargs):
        return self.sessions.add_raw_transcript(*args, **kwargs)

    def get_raw_transcript(self, *args, **kwargs):
        return self.sessions.get_raw_transcript(*args, **kwargs)

    def get_raw_transcript_for_student_session(self, *args, **kwargs):
        return self.sessions.get_raw_transcript_for_student_session(*args, **kwargs)

    def get_raw_transcript_for_student_session_sha(self, *args, **kwargs):
        return self.sessions.get_raw_transcript_for_student_session_sha(*args, **kwargs)

    def get_raw_transcript_for_report(self, *args, **kwargs):
        return self.sessions.get_raw_transcript_for_report(*args, **kwargs)

    def set_raw_transcript_analysis_status(self, *args, **kwargs):
        return self.sessions.set_raw_transcript_analysis_status(*args, **kwargs)

    def replace_session_messages(self, *args, **kwargs):
        return self.sessions.replace_session_messages(*args, **kwargs)

    def add_student_ask(self, *args, **kwargs):
        return self.sessions.add_student_ask(*args, **kwargs)

    def list_student_asks(self, *args, **kwargs):
        return self.sessions.list_student_asks(*args, **kwargs)

    def set_prompt_config(self, *args, **kwargs):
        return self.sessions.set_prompt_config(*args, **kwargs)

    def get_prompt_config(self, *args, **kwargs):
        return self.sessions.get_prompt_config(*args, **kwargs)

    def get_prompt_reply(self, *args, **kwargs):
        return self.sessions.get_prompt_reply(*args, **kwargs)

    def get_prompt_reply_by_id(self, *args, **kwargs):
        return self.sessions.get_prompt_reply_by_id(*args, **kwargs)

    def get_message_reply_by_id(self, *args, **kwargs):
        return self.sessions.get_message_reply_by_id(*args, **kwargs)

    def get_message_rounds_by_session(self, *args, **kwargs):
        return self.sessions.get_message_rounds_by_session(*args, **kwargs)

    def set_message_summary(self, *args, **kwargs):
        return self.sessions.set_message_summary(*args, **kwargs)

    def get_timeline_by_session(self, *args, **kwargs):
        return self.sessions.get_timeline_by_session(*args, **kwargs)

    def _analysis_timeline_rows(self, *args, **kwargs):
        return self.sessions._analysis_timeline_rows(*args, **kwargs)

    def _prompt_timeline_rows(self, *args, **kwargs):
        return self.sessions._prompt_timeline_rows(*args, **kwargs)

    def _bulk_message_timeline_rows(self, *args, **kwargs):
        return self.sessions._bulk_message_timeline_rows(*args, **kwargs)

    def _get_prompt_reply_with_conn(self, *args, **kwargs):
        return self.sessions._get_prompt_reply_with_conn(*args, **kwargs)

    def _message_reply_rows_with_conn(self, *args, **kwargs):
        return self.sessions._message_reply_rows_with_conn(*args, **kwargs)

    # ------------------------------------------------------------------
    # message domain (delegated to MessageStore)
    # ------------------------------------------------------------------

    def add_mentor_message(self, *args, **kwargs):
        return self.messages.add_mentor_message(*args, **kwargs)

    def _message_cursor_id(self, *args, **kwargs):
        return self.messages._message_cursor_id(*args, **kwargs)

    def list_undelivered_messages(self, *args, **kwargs):
        return self.messages.list_undelivered_messages(*args, **kwargs)

    def list_pending_message_receipts(self, *args, **kwargs):
        return self.messages.list_pending_message_receipts(*args, **kwargs)

    def list_messages_since(self, *args, **kwargs):
        return self.messages.list_messages_since(*args, **kwargs)

    def mark_message_delivered(self, *args, **kwargs):
        return self.messages.mark_message_delivered(*args, **kwargs)

    def mark_message_read(self, *args, **kwargs):
        return self.messages.mark_message_read(*args, **kwargs)

    # ------------------------------------------------------------------
    # upload domain (delegated to UploadStore)
    # ------------------------------------------------------------------

    def add_upload_request(self, *args, **kwargs):
        return self.uploads.add_upload_request(*args, **kwargs)

    def get_upload_request(self, *args, **kwargs):
        return self.uploads.get_upload_request(*args, **kwargs)

    def list_upload_requests(self, *args, **kwargs):
        return self.uploads.list_upload_requests(*args, **kwargs)

    def list_pending_upload_requests(self, *args, **kwargs):
        return self.uploads.list_pending_upload_requests(*args, **kwargs)

    def update_upload_request_status(self, *args, **kwargs):
        return self.uploads.update_upload_request_status(*args, **kwargs)

    def compare_and_set_upload_request_axis(self, *args, **kwargs):
        return self.uploads.compare_and_set_upload_request_axis(*args, **kwargs)

    def list_active_upload_request_analyses(self, *args, **kwargs):
        return self.uploads.list_active_upload_request_analyses(*args, **kwargs)

    def claim_upload_analysis_retry(self, *args, **kwargs):
        return self.uploads.claim_upload_analysis_retry(*args, **kwargs)

    def upsert_upload_request_session(self, *args, **kwargs):
        return self.uploads.upsert_upload_request_session(*args, **kwargs)

    def list_upload_request_sessions(self, *args, **kwargs):
        return self.uploads.list_upload_request_sessions(*args, **kwargs)

    def compare_and_set_upload_request_session(self, *args, **kwargs):
        return self.uploads.compare_and_set_upload_request_session(*args, **kwargs)

    def list_active_upload_request_sessions(self, *args, **kwargs):
        return self.uploads.list_active_upload_request_sessions(*args, **kwargs)

    def get_known_session_shas(self, *args, **kwargs):
        return self.uploads.get_known_session_shas(*args, **kwargs)