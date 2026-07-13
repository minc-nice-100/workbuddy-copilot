"""Service 层：业务编排。

封装核心业务流程，与 HTTP/WS 无关，可被路由、hook、定时任务复用。
- AnalysisService：transcript → LLM → 入库 → 发事件
- SessionQueryService：对话列表 / 当前会话 / 时间线
"""
from __future__ import annotations

import asyncio
import logging
import copy
import time
import uuid
from typing import Any

from .config import _validate_analysis_max_concurrency
from .models import (
    Student, Conversation, TimelineEntry, AnalysisResult,
)
from .eventbus import EventBus
from .llm import coerce_analysis_outcome
from .transcript import TranscriptSnapshot, parse_text

log = logging.getLogger("copilot.services")

EXPLICIT_RAW_TRANSCRIPT_MARKER = "copilot:explicit-raw-transcript"


class AnalysisService:
    """学习分析编排服务。

    封装 Stop 事件的完整业务流程：
    transcript 解析 → LLM 分析 → 存储 ai_summary + analysis → 发布事件。

    依赖 Store + EventBus，不再读取学员机本地文件或 WorkBuddy DB。
    """

    def __init__(
        self,
        copilot_repo,
        llm_analyzer,
        config: dict,
        event_bus: EventBus,
        notifier: Any | None = None,
    ):
        """
        Args:
            copilot_repo: CopilotRepo 实例（读写 copilot.db）
            llm_analyzer: LLM 分析器（copilot.llm.analyze 函数）
            config: 全局配置 dict
            event_bus: 进程内事件总线
            notifier: Notifier 端口实现（系统通知，可选）
        """
        self.copilot = copilot_repo
        self.llm = llm_analyzer
        self.config = config
        self.bus = event_bus
        self.notifier = notifier
        configured_concurrency = (
            config.get("service", {}).get("analysis_max_concurrency", 2)
        )
        max_concurrency = _validate_analysis_max_concurrency(configured_concurrency)
        self.analysis_semaphore = asyncio.Semaphore(max_concurrency)

    def _config_with_prompt_overrides(self) -> dict:
        """Return analysis config with server-stored prompt overrides applied."""
        cfg = copy.deepcopy(self.config)
        try:
            row = self.copilot.get_prompt_config("process_reminder")
        except AttributeError:
            row = None
        if row and str(row.get("prompt") or "").strip():
            cfg.setdefault("analysis", {})["process_reminder_prompt"] = str(row["prompt"])
        return cfg

    def parse_transcript_content(self, transcript_content: str | bytes | None) -> TranscriptSnapshot:
        """Parse uploaded transcript content, degrading to an empty snapshot."""
        try:
            return parse_text(transcript_content or "")
        except Exception as exc:
            log.warning("transcript parse failed, using empty snapshot: %s", exc)
            return TranscriptSnapshot()

    def accept_report(
        self,
        *,
        student_id: str,
        session_id: str | None,
        event: str,
        prompt_text: str,
        transcript_content: str | bytes | None,
        raw_transcript_content: str | bytes | None = None,
        cwd: str | None = None,
    ) -> tuple[int, str, TranscriptSnapshot]:
        """Upsert ownership rows and persist the incoming report metadata."""
        snap = self.parse_transcript_content(transcript_content)
        resolved_session_id = session_id or snap.session_id or ""
        raw_content = raw_transcript_content if event == "Stop" else None
        has_explicit_raw = bool(raw_content and resolved_session_id)
        title = snap.ai_title or ""
        self.copilot.upsert_student(student_id)
        if resolved_session_id:
            self.copilot.upsert_session(
                session_id=resolved_session_id,
                student_id=student_id,
                work_dir=cwd or snap.cwd or "",
                title=title,
            )
        report_id = self.copilot.add_report(
            student_id=student_id,
            session_id=resolved_session_id or None,
            event=event,
            prompt=prompt_text,
            transcript_path=(
                EXPLICIT_RAW_TRANSCRIPT_MARKER if has_explicit_raw else ""
            ),
            msg_count=len(snap.messages),
            tool_calls=snap.tool_calls,
        )
        if event == "Stop":
            if has_explicit_raw:
                content = (
                    raw_content.decode("utf-8", errors="replace")
                    if isinstance(raw_content, bytes)
                    else raw_content
                )
                self.copilot.add_raw_transcript(resolved_session_id, student_id, content)
                self.copilot.set_analysis_pending(report_id, True)
        return report_id, resolved_session_id, snap

    async def handle_user_prompt_submit(
        self, student_id: str, session_id: str, prompt_text: str,
    ) -> int:
        """处理 UserPromptSubmit 事件：存 prompt → 发事件。"""
        seq = len(self.copilot.get_prompts_by_session(session_id))
        prompt_id = self.copilot.add_prompt(session_id, seq, student_id, prompt_text)

        await self.bus.publish({
            "type": "prompt",
            "student_id": student_id,
            "session_id": session_id,
            "prompt_id": prompt_id,
            "seq": seq,
            "prompt": prompt_text[:120],
            "timestamp": time.time(),
        })
        return prompt_id

    async def handle_stop(
        self, student_id: str, session_id: str, prompt_text: str,
        transcript_content: str | bytes | None, report_id: int,
    ) -> AnalysisResult:
        """处理 Stop 事件：存 prompt → LLM 分析 → 存结果 → 发事件。"""
        # Persist one prompt per report so process recovery is idempotent.
        prompt_id: int | None = None
        effective_prompt = prompt_text
        prompt_row = self.copilot.get_prompt_for_report(report_id)
        prompt_created = False
        if prompt_row:
            prompt_id = int(prompt_row["id"])
            effective_prompt = str(prompt_row.get("content") or prompt_text)
        elif prompt_text:
            prompt_row, prompt_created = self.copilot.get_or_create_prompt_for_report(
                report_id=report_id,
                session_id=session_id,
                student_id=student_id,
                content=prompt_text,
            )
            prompt_id = int(prompt_row["id"])
            effective_prompt = str(prompt_row.get("content") or prompt_text)

        if prompt_row and prompt_created:
            # The committed prompt row is authoritative. This EventBus signal is
            # transient; clients that miss it catch up through persisted queries.
            await self.bus.publish({
                "type": "prompt",
                "student_id": student_id,
                "session_id": session_id,
                "prompt_id": prompt_id,
                "seq": int(prompt_row["seq_in_session"]),
                "prompt": effective_prompt[:120],
                "timestamp": time.time(),
            })

        snap = self.parse_transcript_content(transcript_content)
        # LLM 分析
        llm_config = self._config_with_prompt_overrides()
        async with self.analysis_semaphore:
            raw_outcome = await self.llm(
                llm_config,
                snap,
                "Stop",
                effective_prompt,
            )
        outcome = coerce_analysis_outcome(raw_outcome)
        if not outcome.ok:
            error = outcome.error or "LLM provider analysis failed"
            log.error(
                "analysis provider failed rid=%d sid=%s session=%s error=%s status=pending",
                report_id,
                student_id,
                (session_id or "?")[:8],
                error,
            )
            raise RuntimeError(error)
        result = AnalysisResult.from_dict(outcome.value)

        # 标题从 copilot.db sessions 表读取；解析出的 ai_title 仅作兜底。
        session_title = self.copilot.get_session_title(session_id) or snap.ai_title or ""

        # 存储
        self.copilot.add_ai_summary(
            prompt_id, session_id, student_id, result.ai_reply_summary,
        )
        self.copilot.add_analysis(
            report_id, student_id, result.to_dict(),
            session_id=session_id, session_title=session_title,
        )
        self.copilot.set_analysis_pending(report_id, False)

        log.info(
            "分析完成 rid=%d sid=%s session=%s topic=%s",
            report_id, student_id, (session_id or "?")[:8], result.topic,
        )

        # 发布 AI 摘要事件
        if result.ai_reply_summary:
            await self.bus.publish({
                "type": "ai_summary",
                "student_id": student_id,
                "session_id": session_id,
                "summary": result.ai_reply_summary,
                "timestamp": time.time(),
            })

        # 发布分析事件
        await self.bus.publish({
            "type": "analysis",
            "student_id": student_id,
            "session_id": session_id,
            "session_title": session_title,
            "report_id": report_id,
            "event": "Stop",
            "prompt": effective_prompt[:120],
            "result": result.to_dict(),
            "timestamp": time.time(),
        })

        return result


class SessionQueryService:
    """会话查询服务。

    统一查询入口，浮标和导师台共用。
    所有会话查询均读取 copilot.db sessions 表。
    """

    def __init__(self, copilot_repo, config: dict):
        """
        Args:
            copilot_repo: CopilotRepo 实例
            config: 全局配置
        """
        self.copilot = copilot_repo
        self.config = config

    def list_students(self) -> list[Student]:
        """学员列表 + 状态概览。"""
        student_id = self.config.get("student_id", "student-1")
        student_name = self.config.get("student_name") or student_id

        rows = self.copilot.students_overview()
        students = []
        for r in rows:
            sid = r.get("student_id", student_id)
            display_name = r.get("display_name") or (student_name if sid == student_id else "") or sid
            # 实时计算有效会话数
            sessions = self.list_sessions(sid)
            students.append(Student(
                student_id=sid,
                display_name=display_name,
                analysis_count=r.get("analysis_count", 0),
                session_count=len(sessions),
                last_ts=r.get("last_ts", 0),
                last_topic=r.get("last_topic", ""),
                last_severity=r.get("last_severity", "info"),
                alert_count=r.get("alert_count", 0),
                last_diagnosis=r.get("last_diagnosis", ""),
            ))
        return students

    def list_sessions(self, student_id: str, limit: int = 1000) -> list[Conversation]:
        """某学员的对话列表（以 copilot.db sessions 表为权威源）。"""
        rows = self.copilot.get_sessions_by_student(student_id, limit=limit)
        return [Conversation(
            session_id=r["session_id"],
            work_dir=r.get("work_dir", ""),
            title=r.get("session_title", ""),
            group_type=r.get("group_type", "") or "",
            space_name=r.get("space_name", "") or "",
            created_at=r.get("created_at", 0) or 0,
            analysis_count=r.get("analysis_count", 0),
            message_count=r.get("message_count", 0),
            alert_count=r.get("alert_count", 0),
            last_diagnosis=r.get("last_diagnosis", ""),
            last_topic=r.get("last_topic", ""),
            last_severity=r.get("last_severity", "info"),
            last_is_technical=r.get("last_is_technical", 0),
            last_activity_at=r.get("last_ts", 0),
        ) for r in rows]

    def get_timeline(self, session_id: str) -> list[TimelineEntry]:
        """某对话的时间线（三表 UNION）。"""
        rows = self.copilot.get_timeline_by_session(session_id)
        return [TimelineEntry(
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
        ) for r in rows]

    def get_active_session(
        self,
        work_dir: str | None = None,
        student_id: str | None = None,
    ) -> dict | None:
        """当前激活的对话（浮标跟随用），读取 sessions 表。"""
        return self.copilot.get_active_session_from_table(work_dir=work_dir, student_id=student_id)

    def list_all_sessions_with_title(
        self,
        work_dir: str | None = None,
        student_id: str | None = None,
        limit: int = 8,
    ) -> list[dict]:
        """列出最近会话（带标题），浮标切换栏用，读取 sessions 表。"""
        return self.copilot.list_sessions_from_table(
            work_dir=work_dir,
            student_id=student_id,
            limit=limit,
        )


class MessageService:
    """Mentor-to-student message workflow."""

    def __init__(self, copilot_repo, event_bus: EventBus):
        self.copilot = copilot_repo
        self.bus = event_bus

    async def send(self, student_id: str, mentor_id: str | None, text: str) -> dict[str, Any]:
        """Persist a mentor message, publish it, and report delivery status."""
        resolved_mentor_id = mentor_id or "mentor"
        message_id = uuid.uuid4().hex
        self.copilot.upsert_student(student_id)
        row_id = self.copilot.add_mentor_message(
            student_id=student_id,
            mentor_id=resolved_mentor_id,
            session_id="",
            text=text,
            message_id=message_id,
        )
        row = self._find_message(student_id, row_id)
        payload = self._to_wire_message(row)

        await self.bus.publish(payload)

        delivered_row = self._find_message(student_id, row_id)
        return {
            "message_id": message_id,
            "id": row_id,
            "delivered": bool(delivered_row.get("delivered_at")),
        }

    def get_catchup(
        self,
        student_id: str,
        since_id: int | str | None,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return only still-unacknowledged messages after the client cursor.

        A client's display cursor can intentionally roll back while recovering
        an earlier state-publish failure.  Confirmed history is audit data,
        not a delivery backlog: returning it here would reintroduce it to a
        bounded client de-duplication cache and cause duplicate rendering.
        """
        return [
            self._to_wire_message(row)
            for row in self.copilot.list_undelivered_messages(
                student_id,
                since_id,
                limit=limit,
            )
        ]

    def get_pending_receipts(
        self,
        student_id: str,
        *,
        limit: int = 64,
        after_id: int = 0,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 64))
        return [
            self._to_wire_message(row)
            for row in self.copilot.list_pending_message_receipts(
                student_id,
                limit=bounded_limit,
                after_id=max(0, int(after_id)),
            )
        ]

    async def ack(self, message_id: str, student_id: str) -> bool:
        try:
            existing = self._find_message_by_message_id(student_id, message_id)
        except LookupError:
            return False
        if existing.get("delivered_at") is not None:
            return True

        updated = self.copilot.mark_message_delivered(message_id, student_id=student_id)
        if updated <= 0:
            return False

        row = self._find_message_by_message_id(student_id, message_id)
        await self.bus.publish({
            "type": "message_delivered",
            "student_id": student_id,
            "message_id": message_id,
            "id": row["id"],
            "timestamp": row.get("delivered_at") or time.time(),
        })
        return True

    def _find_message(self, student_id: str, row_id: int) -> dict[str, Any]:
        for row in self.copilot.list_messages_since(student_id, 0):
            if int(row["id"]) == int(row_id):
                return row
        raise LookupError(f"mentor message not found: student={student_id} id={row_id}")

    def _find_message_by_message_id(self, student_id: str, message_id: str) -> dict[str, Any]:
        for row in self.copilot.list_messages_since(student_id, 0):
            if row["message_id"] == message_id:
                return row
        raise LookupError(f"mentor message not found: student={student_id} message={message_id}")

    @staticmethod
    def _to_wire_message(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "mentor_message",
            "student_id": row["student_id"],
            "message_id": row["message_id"],
            "id": row["id"],
            "text": row["text"],
            "mentor_id": row["mentor_id"],
            "timestamp": row["created_at"],
        }
