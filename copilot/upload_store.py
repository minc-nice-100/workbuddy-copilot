"""Upload domain store: upload_requests, upload_request_sessions tables.

Also includes `get_known_session_shas` which reads from raw_transcripts but is
primarily used by the upload flow to determine which sessions have already been
transferred.

All methods share the same SQLite connection factory provided by the parent
Store instance. Schema and migrations live in Store; this class only reads and
writes data.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from typing import Any

log = logging.getLogger("copilot.upload_store")


class UploadRetryClaimConflict(RuntimeError):
    """Raised when an analysis retry cannot be claimed atomically."""


class UploadStore:
    def __init__(self, store: "Store") -> None:  # noqa: F821
        self._store = store

    # ------------------------------------------------------------------
    # upload_requests
    # ------------------------------------------------------------------

    def add_upload_request(
        self,
        mentor_id: str,
        student_id: str,
        session_id: str | None = None,
        status: str = "pending",
        request_id: str | None = None,
    ) -> str:
        rid = request_id or uuid.uuid4().hex
        now = time.time()
        with self._store._conn() as c:
            c.execute(
                """INSERT INTO upload_requests
                   (request_id, mentor_id, student_id, session_id, status,
                    transfer_status, analysis_status, error_message,
                    transfer_error, analysis_error, result_json, updated_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'not_requested', '', '', '', NULL, ?, ?)""",
                (
                    rid,
                    mentor_id,
                    student_id,
                    session_id,
                    status,
                    {"done": "stored"}.get(status, status),
                    now,
                    now,
                ),
            )
        return rid

    def get_upload_request(self, request_id: str) -> dict | None:
        with self._store._conn() as c:
            row = c.execute(
                "SELECT * FROM upload_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_upload_requests(
        self,
        student_id: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        clauses = []
        params: list[Any] = []
        if student_id:
            clauses.append("student_id = ?")
            params.append(student_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._store._conn() as c:
            rows = c.execute(
                f"""SELECT * FROM upload_requests
                    {where}
                    ORDER BY created_at ASC""",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def list_pending_upload_requests(self, student_id: str | None = None) -> list[dict]:
        return self.list_upload_requests(student_id=student_id, status="pending")

    def update_upload_request_status(
        self,
        request_id: str,
        *,
        student_id: str,
        status: str,
        error_message: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> int:
        result_json = json.dumps(result, ensure_ascii=False) if result is not None else None
        transfer_status = {"done": "stored"}.get(status, status)
        with self._store._conn() as c:
            cur = c.execute(
                """UPDATE upload_requests
                   SET status = ?,
                       transfer_status = ?,
                       error_message = ?,
                       transfer_error = ?,
                       result_json = ?,
                       updated_at = ?
                   WHERE request_id = ? AND student_id = ?""",
                (
                    status,
                    transfer_status,
                    error_message or "",
                    error_message or "",
                    result_json,
                    time.time(),
                    request_id,
                    student_id,
                ),
            )
            return cur.rowcount

    def compare_and_set_upload_request_axis(
        self,
        request_id: str,
        *,
        student_id: str,
        axis: str,
        expected: str,
        new_status: str,
        error: str,
        result: dict[str, Any] | None = None,
    ) -> int:
        if axis not in {"transfer", "analysis"}:
            raise ValueError(f"unsupported upload request axis: {axis}")
        now = time.time()
        if axis == "transfer":
            legacy_status = {"stored": "done"}.get(new_status, new_status)
            sql = """UPDATE upload_requests
                     SET transfer_status = ?, transfer_error = ?,
                         status = ?, error_message = ?, result_json = ?, updated_at = ?
                     WHERE request_id = ? AND student_id = ?
                       AND transfer_status = ?"""
            params: tuple[Any, ...] = (
                new_status,
                error,
                legacy_status,
                error,
                json.dumps(result, ensure_ascii=False) if result is not None else None,
                now,
                request_id,
                student_id,
                expected,
            )
        else:
            sql = """UPDATE upload_requests
                     SET analysis_status = ?, analysis_error = ?, updated_at = ?
                     WHERE request_id = ? AND student_id = ?
                       AND analysis_status = ?"""
            params = (
                new_status,
                error,
                now,
                request_id,
                student_id,
                expected,
            )
        with self._store._conn() as c:
            cur = c.execute(sql, params)
            return cur.rowcount

    def list_active_upload_request_analyses(self) -> list[dict]:
        with self._store._conn() as c:
            rows = c.execute(
                """SELECT * FROM upload_requests
                   WHERE analysis_status IN ('pending', 'running')"""
            ).fetchall()
            return [dict(row) for row in rows]

    def claim_upload_analysis_retry(
        self,
        request_id: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        now = time.time()
        with self._store._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            parent_row = c.execute(
                "SELECT * FROM upload_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if parent_row is None:
                raise UploadRetryClaimConflict("upload request not found")
            parent = dict(parent_row)
            if (
                parent.get("transfer_status") != "stored"
                or parent.get("analysis_status") != "failed"
            ):
                raise UploadRetryClaimConflict(
                    "analysis retry requires transfer=stored and analysis=failed"
                )
            student_id = str(parent.get("student_id") or "")
            requested_session = str(parent.get("session_id") or "").strip()
            children = [
                dict(row) for row in c.execute(
                    """SELECT * FROM upload_request_sessions
                       WHERE request_id = ? ORDER BY created_at, session_id""",
                    (request_id,),
                ).fetchall()
            ]
            targets = [
                child for child in children
                if child.get("analysis_status") == "failed"
                and (
                    not requested_session
                    or child.get("session_id") == requested_session
                )
            ]

            legacy_target: dict[str, Any] | None = None
            if requested_session and not targets:
                existing = next(
                    (
                        child for child in children
                        if child.get("session_id") == requested_session
                    ),
                    None,
                )
                if existing is not None:
                    raise UploadRetryClaimConflict(
                        "analysis retry requires a failed child"
                    )
                raw_row = c.execute(
                    """SELECT * FROM raw_transcripts
                       WHERE student_id = ? AND session_id = ?
                       ORDER BY created_at DESC, id DESC LIMIT 1""",
                    (student_id, requested_session),
                ).fetchone()
                if raw_row is None or not str(raw_row["content_sha256"] or ""):
                    raise UploadRetryClaimConflict(
                        f"analysis retry raw missing for session: {requested_session}"
                    )
                legacy_target = {
                    "request_id": request_id,
                    "student_id": student_id,
                    "session_id": requested_session,
                    "sha": str(raw_row["content_sha256"]),
                    "analysis_status": "failed",
                    "analysis_error": str(parent.get("analysis_error") or "analysis failed"),
                    "created_at": now,
                    "updated_at": now,
                }
                targets = [legacy_target]
            if not targets:
                raise UploadRetryClaimConflict(
                    "analysis retry requires failed children"
                )

            work_items: list[dict[str, Any]] = []
            for child in targets:
                child_student = str(child.get("student_id") or "")
                child_session = str(child.get("session_id") or "")
                child_sha = str(child.get("sha") or "")
                if child_student != student_id or not child_session or not child_sha:
                    raise UploadRetryClaimConflict(
                        f"analysis retry child ownership invalid: {child_session}"
                    )
                raw_row = c.execute(
                    """SELECT * FROM raw_transcripts
                       WHERE student_id = ? AND session_id = ? AND content_sha256 = ?
                       ORDER BY created_at DESC, id DESC LIMIT 1""",
                    (student_id, child_session, child_sha),
                ).fetchone()
                if raw_row is None:
                    raise UploadRetryClaimConflict(
                        f"analysis retry raw missing for session: {child_session}"
                    )
                work_items.append({
                    "session_id": child_session,
                    "sha": child_sha,
                    "raw": dict(raw_row),
                })

            if legacy_target is not None:
                c.execute(
                    """INSERT INTO upload_request_sessions
                       (request_id, student_id, session_id, sha, analysis_status,
                        analysis_error, updated_at, created_at)
                       VALUES (?, ?, ?, ?, 'failed', ?, ?, ?)""",
                    (
                        request_id,
                        student_id,
                        requested_session,
                        legacy_target["sha"],
                        legacy_target["analysis_error"],
                        now,
                        now,
                    ),
                )

            claimed = c.execute(
                """UPDATE upload_requests
                   SET analysis_status = 'pending', analysis_error = '', updated_at = ?
                   WHERE request_id = ? AND transfer_status = 'stored'
                     AND analysis_status = 'failed'""",
                (now, request_id),
            ).rowcount
            if claimed != 1:
                raise UploadRetryClaimConflict("analysis retry already claimed")

            for child in targets:
                updated = c.execute(
                    """UPDATE upload_request_sessions
                       SET analysis_status = 'pending', analysis_error = '', updated_at = ?
                       WHERE request_id = ? AND student_id = ? AND session_id = ?
                         AND sha = ? AND analysis_status = 'failed'""",
                    (
                        now,
                        request_id,
                        student_id,
                        str(child["session_id"]),
                        str(child["sha"]),
                    ),
                ).rowcount
                if updated != 1:
                    raise UploadRetryClaimConflict(
                        f"analysis retry child already claimed: {child['session_id']}"
                    )
            claimed_parent = c.execute(
                "SELECT * FROM upload_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            return dict(claimed_parent), work_items

    # ------------------------------------------------------------------
    # upload_request_sessions
    # ------------------------------------------------------------------

    def upsert_upload_request_session(
        self,
        request_id: str,
        student_id: str,
        session_id: str,
        sha: str,
        *,
        analysis_status: str = "pending",
        analysis_error: str = "",
    ) -> dict:
        now = time.time()
        with self._store._conn() as c:
            c.execute(
                """INSERT INTO upload_request_sessions
                   (request_id, student_id, session_id, sha, analysis_status,
                    analysis_error, updated_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(request_id, session_id) DO UPDATE SET
                     sha = excluded.sha,
                     analysis_status = excluded.analysis_status,
                     analysis_error = '',
                     updated_at = excluded.updated_at
                   WHERE upload_request_sessions.sha != excluded.sha""",
                (request_id, student_id, session_id, sha, analysis_status,
                 analysis_error, now, now),
            )
            row = c.execute(
                """SELECT * FROM upload_request_sessions
                   WHERE request_id = ? AND session_id = ?""",
                (request_id, session_id),
            ).fetchone()
            return dict(row)

    def list_upload_request_sessions(self, request_id: str) -> list[dict]:
        with self._store._conn() as c:
            rows = c.execute(
                """SELECT * FROM upload_request_sessions
                   WHERE request_id = ? ORDER BY created_at, session_id""",
                (request_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def compare_and_set_upload_request_session(
        self,
        request_id: str,
        student_id: str,
        session_id: str,
        *,
        expected: str,
        new_status: str,
        error: str = "",
        sha: str | None = None,
    ) -> int:
        sha_clause = " AND sha = ?" if sha is not None else ""
        params: list[Any] = [
            new_status, error, time.time(), request_id, student_id,
            session_id, expected,
        ]
        if sha is not None:
            params.append(sha)
        with self._store._conn() as c:
            cur = c.execute(
                f"""UPDATE upload_request_sessions
                   SET analysis_status = ?, analysis_error = ?, updated_at = ?
                   WHERE request_id = ? AND student_id = ? AND session_id = ?
                     AND analysis_status = ?{sha_clause}""",
                params,
            )
            return cur.rowcount

    def list_active_upload_request_sessions(self) -> list[dict]:
        with self._store._conn() as c:
            rows = c.execute(
                """SELECT * FROM upload_request_sessions
                   WHERE analysis_status IN ('pending', 'running')"""
            ).fetchall()
            return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # raw_transcripts (upload-related)
    # ------------------------------------------------------------------

    def get_known_session_shas(self, student_id: str) -> dict[str, dict[str, str]]:
        with self._store._conn() as c:
            rows = c.execute(
                """SELECT session_id, content_sha256, analysis_status
                   FROM raw_transcripts
                   WHERE student_id = ?
                     AND session_id IS NOT NULL
                     AND session_id != ''
                     AND content_sha256 IS NOT NULL
                     AND content_sha256 != ''
                   ORDER BY created_at DESC, id DESC""",
                (student_id,),
            ).fetchall()
        known: dict[str, dict[str, str]] = {}
        for row in rows:
            sid = str(row["session_id"])
            if sid not in known:
                known[sid] = {
                    "sha": str(row["content_sha256"]),
                    "analysis_status": str(row["analysis_status"] or ""),
                }
        return known