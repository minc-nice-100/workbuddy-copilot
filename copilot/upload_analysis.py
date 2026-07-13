"""Upload analysis service for WorkBuddy Copilot.

Handles background LLM analysis for uploaded session transcripts, retry of
failed analyses, and crash recovery of pending Stop reports.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from .llm import coerce_analysis_outcome
from .models import AnalysisResult
from .services import EXPLICIT_RAW_TRANSCRIPT_MARKER
from .store import Store
from .transcript import Message, TranscriptSnapshot, parse_text, parse_turns
from .upload_service import (
    InvalidStateTransition,
    UploadRequestNotFound,
    UploadRequestService,
)

log = logging.getLogger("copilot.upload_analysis")


class UploadAnalysisService:
    """Handles background LLM analysis for uploaded session transcripts."""

    def __init__(
        self,
        store: Store,
        analysis_svc,
        upload_svc: UploadRequestService,
        bus,
        config: dict[str, Any],
    ):
        self._store = store
        self._analysis_svc = analysis_svc
        self._upload_svc = upload_svc
        self._bus = bus
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze_session(
        self,
        student_id: str,
        session_id: str,
        turns: list[dict[str, Any]],
        sha: str,
        request_id: str | None = None,
    ) -> tuple[bool, str]:
        """Run bounded LLM analysis for an uploaded historical session."""
        try:
            if request_id:
                await self._mark_upload_child_and_publish(
                    request_id, student_id, session_id, "running", sha=sha
                )
            self._store.set_raw_transcript_analysis_status(
                session_id,
                student_id,
                status="running",
                content_sha256=sha,
            )
            snap = self._snapshot_from_turns(turns)
            latest_prompt = self._latest_user_prompt(turns)
            llm_config = (
                self._analysis_svc._config_with_prompt_overrides()
                if hasattr(self._analysis_svc, "_config_with_prompt_overrides")
                else self._config
            )
            async with self._analysis_svc.analysis_semaphore:
                raw_outcome = await self._analysis_svc.llm(
                    llm_config,
                    snap,
                    "Stop",
                    latest_prompt,
                )
            outcome = coerce_analysis_outcome(raw_outcome)
            if not outcome.ok:
                raise RuntimeError(outcome.error or "LLM provider analysis failed")
            result = AnalysisResult.from_dict(outcome.value)
            session_title = self._store.get_session_title(session_id)
            report_id = self._store.commit_bulk_analysis_if_current(
                student_id=student_id,
                session_id=session_id,
                content_sha256=sha,
                result=result.to_dict(),
                session_title=session_title,
                msg_count=len(snap.messages),
            )
            if report_id is None:
                log.info(
                    "bulk analysis discarded stale_sha student=%s session=%s sha=%s",
                    student_id,
                    session_id[:8],
                    sha[:12],
                )
                if request_id:
                    await self._mark_upload_child_and_publish(
                        request_id, student_id, session_id, "failed",
                        error="analysis stale transcript", sha=sha,
                    )
                return False, "analysis stale transcript"
            await self._bus.publish({
                "type": "analysis",
                "student_id": student_id,
                "session_id": session_id,
                "session_title": session_title,
                "report_id": report_id,
                "event": "BulkUpload",
                "prompt": latest_prompt[:120],
                "result": result.to_dict(),
                "timestamp": time.time(),
            })
            log.info(
                "bulk upload analysis complete student=%s session=%s report_id=%s",
                student_id,
                session_id[:8],
                report_id,
            )
            if request_id:
                await self._mark_upload_child_and_publish(
                    request_id, student_id, session_id, "done", sha=sha
                )
            return True, ""
        except Exception as exc:
            error_code = self._stable_background_analysis_error(exc)
            self._store.set_raw_transcript_analysis_status(
                session_id,
                student_id,
                status="failed",
                error_message=error_code,
                content_sha256=sha,
            )
            log.error(
                "bulk upload analysis failed student=%s session=%s error=%s type=%s",
                student_id,
                session_id[:8],
                error_code,
                type(exc).__name__,
            )
            if request_id:
                await self._mark_upload_child_and_publish(
                    request_id, student_id, session_id, "failed",
                    error=error_code, sha=sha,
                )
            return False, error_code

    async def retry_request(
        self,
        request_id: str,
        student_id: str,
        session_id: str,
        raw: str,
        sha: str,
    ) -> None:
        """Analyze the already-persisted raw transcript and mirror request status."""
        try:
            children = self._store.list_upload_request_sessions(request_id)
            has_child = any(child.get("session_id") == session_id for child in children)
            if has_child:
                await self._mark_upload_child_and_publish(
                    request_id, student_id, session_id, "running", sha=sha
                )
            else:
                running = self._upload_svc.mark_analysis(
                    request_id, student_id, "running", error=""
                )
                await self._publish_upload_request_status(running)
            turns = parse_turns(parse_text(raw).messages)
            ok, error = await self.analyze_session(
                student_id,
                session_id,
                turns,
                sha,
            )
            if has_child:
                await self._mark_upload_child_and_publish(
                    request_id,
                    student_id,
                    session_id,
                    "done" if ok else "failed",
                    error="" if ok else error,
                    sha=sha,
                )
            elif ok:
                final = self._upload_svc.mark_analysis(
                    request_id, student_id, "done", error=""
                )
            else:
                final = self._upload_svc.mark_analysis(
                    request_id, student_id, "failed", error=error
                )
            if not has_child:
                await self._publish_upload_request_status(final)
        except (InvalidStateTransition, UploadRequestNotFound) as exc:
            log.warning(
                "upload request retry state changed request_id=%s error=%s",
                request_id,
                exc,
            )

    async def recover_pending(self) -> None:
        """Recover pending Stop reports from a previous process crash."""
        pending_reports = self._store.list_pending_reports()
        if not pending_reports:
            return

        stop_reports = [row for row in pending_reports if row.get("event") == "Stop"]
        log.warning(
            "recovering %d pending Stop reports from previous process",
            len(stop_reports),
        )
        for row in stop_reports:
            report_id = int(row["id"])
            student_id = str(row.get("student_id") or "")
            session_id = str(row.get("session_id") or "")
            if self._store.analysis_exists_for_report(report_id):
                log.warning(
                    "pending Stop report_id=%s already has analysis; clearing pending flag",
                    report_id,
                )
                self._store.set_analysis_pending(report_id, False)
                continue

            transcript_content = ""
            if (
                session_id
                and row.get("transcript_path") == EXPLICIT_RAW_TRANSCRIPT_MARKER
            ):
                raw = self._store.get_raw_transcript_for_report(session_id, row.get("created_at"))
                transcript_content = (raw or {}).get("content") or ""
            log.info(
                "requeue pending Stop report_id=%s student=%s session=%s transcript_bytes=%d",
                report_id,
                student_id,
                (session_id or "?")[:8],
                len(transcript_content.encode("utf-8")),
            )
            try:
                await self._analysis_svc.handle_stop(
                    student_id=student_id,
                    session_id=session_id,
                    prompt_text=str(row.get("prompt") or ""),
                    transcript_content=transcript_content,
                    report_id=report_id,
                )
            except Exception as exc:
                log.exception("background Stop analysis failed report_id=%s: %s", report_id, exc)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _filtered_content_to_raw(filtered_content: Any) -> str:
        """Normalize already-filtered uploaded message content into JSONL text."""
        if filtered_content is None:
            return ""
        if isinstance(filtered_content, str):
            return filtered_content
        if isinstance(filtered_content, (bytes, bytearray)):
            return bytes(filtered_content).decode("utf-8", errors="replace")
        if isinstance(filtered_content, dict):
            for key in ("messages", "items", "lines"):
                value = filtered_content.get(key)
                if isinstance(value, list):
                    return UploadAnalysisService._filtered_content_to_raw(value)
            return json.dumps(filtered_content, ensure_ascii=False)
        if isinstance(filtered_content, list):
            lines: list[str] = []
            for item in filtered_content:
                if isinstance(item, (dict, list)):
                    lines.append(json.dumps(item, ensure_ascii=False))
                else:
                    lines.append(str(item))
            return "\n".join(lines)
        return str(filtered_content)

    @staticmethod
    def _bulk_upload_llm_enabled(config: dict[str, Any]) -> bool:
        analysis_cfg = config.get("analysis", {}) or {}
        if not analysis_cfg.get("enable_llm", True):
            return False
        llm_cfg = config.get("llm", {}) or {}
        if not llm_cfg.get("enable_llm", True):
            return False
        return bool(
            llm_cfg.get("api_key")
            and llm_cfg.get("model")
            and llm_cfg.get("api_base")
        )

    @staticmethod
    def _snapshot_from_turns(turns: list[dict[str, Any]]) -> TranscriptSnapshot:
        snap = TranscriptSnapshot()
        for turn in turns:
            role = str(turn.get("role") or "")
            text = str(turn.get("text") or "")
            if role not in {"user", "assistant"} or not text:
                continue
            snap.messages.append(
                Message(role=role, text=text, timestamp=turn.get("ts"))
            )
        return snap

    @staticmethod
    def _latest_user_prompt(turns: list[dict[str, Any]]) -> str:
        for turn in reversed(turns):
            if turn.get("role") == "user" and turn.get("text"):
                return str(turn["text"])
        return ""

    @staticmethod
    def _stable_background_analysis_error(exc: Exception) -> str:
        """Return a bounded error code without provider response or exception details."""
        message = str(exc)
        if message.startswith("LLM provider HTTP "):
            return " ".join(message.split()[:4])[:80]
        if message.startswith("LLM provider "):
            return " ".join(message.split()[:3])[:80]
        if message.startswith("LLM response JSON invalid"):
            return "LLM response JSON invalid"
        return f"analysis {type(exc).__name__}"

    async def _publish_upload_request_status(
        self,
        row: dict[str, Any],
    ) -> None:
        """Publish a persisted request snapshot to mentor sockets only."""
        snapshot = self._upload_svc.to_response(row)
        snapshot["result"] = self._sanitize_upload_event_result(snapshot.get("result"))
        await self._bus.publish({
            "type": "upload_request_status",
            **snapshot,
            "timestamp": time.time(),
        })

    async def _publish_upload_parent_rows(
        self,
        rows: list[dict[str, Any]],
    ) -> None:
        for row in rows:
            await self._publish_upload_request_status(row)

    async def _mark_upload_child_and_publish(
        self,
        request_id: str,
        student_id: str,
        session_id: str,
        status: str,
        *,
        error: str = "",
        sha: str | None = None,
    ) -> None:
        _child, rows = self._upload_svc.mark_session_analysis(
            request_id,
            student_id,
            session_id,
            status,
            error=error,
            sha=sha,
        )
        await self._publish_upload_parent_rows(rows)

    @staticmethod
    def _sanitize_upload_event_result(value: Any) -> Any:
        """Return only bounded aggregate counters from a client-controlled result."""
        if not isinstance(value, dict):
            return None
        sanitized: dict[str, int] = {}
        for key in ("total", "synced", "skipped", "failed"):
            item = value.get(key)
            if isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 1_000_000:
                sanitized[key] = item
        return sanitized