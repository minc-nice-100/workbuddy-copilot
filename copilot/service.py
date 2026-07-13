"""FastAPI controller layer for WorkBuddy Copilot."""
from __future__ import annotations

import inspect
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .app_context import (
    _extract_supplied_token,
    AppContext,
    acquire_worker_lock,
    build_context,
    get_analysis_service,
    get_context,
    get_message_service,
    get_store,
    get_upload_service,
    require_mentor_token,
    require_student_token,
    release_worker_lock,
    token_is_valid,
    validate_auth_config,
)
from .llm import (
    answer_question as llm_answer_question,
    question_fallback_answer,
)
from .services import (
    AnalysisService,
    MessageService,
)
from .store import Store
from .upload_analysis import UploadAnalysisService
from .upload_service import (
    InvalidStateTransition,
    UploadRequestNotFound,
    UploadRequestService,
    UploadTranscriptNotFound,
)
from .transcript import parse_text, parse_turns

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
log = logging.getLogger("copilot.service")


class ReportIn(BaseModel):
    student_id: str
    session_id: str | None = None
    event: str
    prompt: str = ""
    transcript_tail: str | None = None
    transcript_full: str | None = None
    cwd: str | None = None


class MentorMessageIn(BaseModel):
    student_id: str
    text: str
    mentor_id: str | None = None


class StudentMessageAckIn(BaseModel):
    student_id: str
    message_id: str


class StudentAskIn(BaseModel):
    student_id: str
    question: str
    session_id: str | None = None


class SyncSessionIn(BaseModel):
    session_id: str
    title: str = ""
    work_dir: str = ""
    group_type: Literal["space", "task"] | None = None
    space_name: str = ""
    created_at: float | None = None
    last_activity_at: float | None = None


class SessionsSyncIn(BaseModel):
    student_id: str
    sessions: list[SyncSessionIn]


class TranscriptUploadIn(BaseModel):
    student_id: str
    filtered_content: Any
    sha: str
    request_id: str | None = None


class MentorUploadRequestIn(BaseModel):
    mentor_id: str | None = None
    session_id: str | None = None


class UploadRequestStatusIn(BaseModel):
    student_id: str
    status: Literal["pending", "running", "done", "failed"]
    error_message: str | None = None
    result: dict[str, Any] | None = None


def _question_context_from_raw(content: str | bytes | None) -> list[dict[str, str]]:
    if not content:
        return []
    snap = parse_text(content)
    messages = [
        {"role": msg.role, "content": msg.text}
        for msg in snap.messages[-16:]
        if msg.text
    ]
    if messages:
        return messages
    raw = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
    raw = raw.strip()
    return [{"role": "transcript", "content": raw[-6000:]}] if raw else []


def _question_context_from_recent(rows: list[dict]) -> list[dict[str, str]]:
    context: list[dict[str, str]] = []
    for row in reversed(rows):
        parts: list[str] = []
        topic = row.get("topic") or ""
        diagnosis = row.get("diagnosis") or ""
        suggestion = row.get("suggestion") or ""
        progress = row.get("progress") or ""
        if topic:
            parts.append(f"主题：{topic}")
        if diagnosis:
            parts.append(f"诊断：{diagnosis}")
        if suggestion:
            parts.append(f"建议：{suggestion}")
        if progress:
            parts.append(f"进展：{progress}")
        if parts:
            context.append({"role": "analysis", "content": "；".join(parts)})
    return context


def _build_student_question_context(
    store: Store,
    student_id: str,
    session_id: str | None,
) -> list[dict[str, str]]:
    if session_id:
        raw = store.get_raw_transcript_for_student_session(student_id, session_id)
        raw_context = _question_context_from_raw((raw or {}).get("content"))
        if raw_context:
            return raw_context
    recent = store.recent_analyses(student_id, limit=5, session_id=session_id)
    return _question_context_from_recent(recent)


def _student_ask_timeout(config: dict[str, Any]) -> float:
    try:
        base = float(config.get("llm", {}).get("timeout", 30))
    except (TypeError, ValueError):
        base = 30.0
    return min(max(base + 5.0, 5.0), 45.0)


async def _handle_stop_background(
    analysis_svc: AnalysisService,
    student_id: str,
    session_id: str,
    prompt: str,
    transcript_content: str,
    report_id: int,
) -> None:
    try:
        await analysis_svc.handle_stop(
            student_id=student_id,
            session_id=session_id,
            prompt_text=prompt,
            transcript_content=transcript_content,
            report_id=report_id,
        )
    except Exception as exc:
        log.exception("background Stop analysis failed report_id=%s: %s", report_id, exc)


def create_app(context: AppContext | None = None) -> FastAPI:
    ctx = context or build_context()
    startup_upload_svc = ctx.upload_svc or UploadRequestService(ctx.store.uploads)
    if ctx.upload_svc is None:
        ctx.upload_svc = startup_upload_svc
    if ctx.upload_analysis_svc is None:
        ctx.upload_analysis_svc = UploadAnalysisService(
            store=ctx.store,
            analysis_svc=ctx.analysis_svc,
            upload_svc=ctx.upload_svc,
            bus=ctx.bus,
            config=ctx.config,
        )
    validate_auth_config(ctx.config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.info("Copilot service starting, student=%s", ctx.config.get("student_id", ""))
        try:
            acquire_worker_lock(ctx)
            recovered_uploads = startup_upload_svc.recover_interrupted_analysis()
            if recovered_uploads:
                log.warning(
                    "recovered %d interrupted upload analyses",
                    len(recovered_uploads),
                )
            await ctx.upload_analysis_svc.recover_pending()
            yield
        finally:
            release_worker_lock(ctx)
            log.info("Copilot service stopped")

    app = FastAPI(title="WorkBuddy Copilot", version="0.2.0", lifespan=lifespan)
    app.state.context = ctx

    from .mentor.routes import router as mentor_router

    app.include_router(mentor_router, dependencies=[Depends(require_mentor_token)])

    static_dir = Path(__file__).parent / "static" / "mentor"
    if static_dir.exists():
        from fastapi.staticfiles import StaticFiles

        app.mount(
            "/mentor",
            StaticFiles(directory=str(static_dir), html=True),
            name="mentor-static",
        )

    @app.get("/health")
    async def health(context: AppContext = Depends(get_context)):
        return {"status": "UP", "student": context.config.get("student_id", "")}

    @app.post("/report", status_code=202)
    async def report(
        data: ReportIn,
        background_tasks: BackgroundTasks,
        _: None = Depends(require_student_token),
        analysis_svc: AnalysisService = Depends(get_analysis_service),
    ):
        transcript_content = data.transcript_tail or ""
        try:
            report_id, session_id, snap = analysis_svc.accept_report(
                student_id=data.student_id,
                session_id=data.session_id,
                event=data.event,
                prompt_text=data.prompt,
                transcript_content=transcript_content,
                raw_transcript_content=data.transcript_full,
                cwd=data.cwd,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        log.info(
            "report accepted student=%s session=%s event=%s msgs=%d tools=%d",
            data.student_id,
            (session_id or "?")[:8],
            data.event,
            len(snap.messages),
            snap.tool_calls,
        )

        body: dict[str, Any] = {"status": "accepted", "report_id": report_id}
        if data.event == "UserPromptSubmit":
            body["prompt_id"] = await analysis_svc.handle_user_prompt_submit(
                data.student_id,
                session_id,
                data.prompt,
            )
        elif data.event == "Stop":
            background_tasks.add_task(
                _handle_stop_background,
                analysis_svc,
                data.student_id,
                session_id,
                data.prompt,
                transcript_content,
                report_id,
            )
        return body

    @app.get("/recent")
    async def recent(
        limit: int = 20,
        student_id: str | None = None,
        session_id: str | None = None,
        _: None = Depends(require_student_token),
        store: Store = Depends(get_store),
    ):
        return {"items": store.recent_analyses(student_id, limit=limit, session_id=session_id)}

    @app.post("/api/sessions/sync")
    async def sync_sessions(
        data: SessionsSyncIn,
        _: None = Depends(require_student_token),
        store: Store = Depends(get_store),
    ):
        """Accept student-machine session inventory and upsert it into copilot.db."""
        store.upsert_student(data.student_id)
        synced = 0
        for session in data.sessions:
            if not session.session_id:
                log.warning("skip sync session with empty session_id student=%s", data.student_id)
                continue
            try:
                store.upsert_session(
                    session_id=session.session_id,
                    student_id=data.student_id,
                    work_dir=session.work_dir,
                    title=session.title,
                    created_at=session.created_at,
                    last_activity_at=session.last_activity_at,
                    group_type=session.group_type,
                    space_name=session.space_name,
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            synced += 1
        log.info("sessions sync accepted student=%s synced=%d", data.student_id, synced)
        return {"ok": True, "synced": synced}

    @app.post("/api/student/sessions/{session_id}/transcript")
    async def upload_session_transcript(
        session_id: str,
        data: TranscriptUploadIn,
        background_tasks: BackgroundTasks,
        _: None = Depends(require_student_token),
        context: AppContext = Depends(get_context),
        store: Store = Depends(get_store),
        upload_svc: UploadRequestService = Depends(get_upload_service),
    ):
        """Accept one already-filtered session transcript from a student client."""
        student_id = data.student_id.strip()
        sha = data.sha.strip()
        if not student_id:
            raise HTTPException(status_code=400, detail="student_id is required")
        if not sha:
            raise HTTPException(status_code=400, detail="sha is required")

        request_id = (data.request_id or "").strip() or None
        analysis_scheduled = UploadAnalysisService._bulk_upload_llm_enabled(context.config)

        known = store.get_known_session_shas(student_id)
        known_entry = known.get(session_id) or {}
        raw_row = store.get_raw_transcript_for_student_session_sha(
            student_id, session_id, sha
        )
        if request_id:
            raw_status = str((raw_row or {}).get("analysis_status") or "")
            if not analysis_scheduled:
                child_status = "not_requested"
            else:
                child_status = "done" if raw_status == "done" else "pending"
            try:
                _child, parent_rows = upload_svc.register_session(
                    request_id,
                    student_id,
                    session_id,
                    sha,
                    analysis_status=child_status,
                )
            except UploadRequestNotFound as exc:
                raise HTTPException(status_code=404, detail="upload request not found") from exc
            except InvalidStateTransition as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            await context.upload_analysis_svc._publish_upload_parent_rows(parent_rows)
        if known_entry.get("sha") == sha:
            retry_analysis = (
                bool(raw_row)
                and raw_row.get("analysis_status") != "done"
                and analysis_scheduled
            )
            if retry_analysis:
                raw = str((raw_row or {}).get("content") or "")
                turns = parse_turns(parse_text(raw).messages)
                store.set_raw_transcript_analysis_status(
                    session_id,
                    student_id,
                    status="pending",
                    error_message="",
                    content_sha256=sha,
                )
                background_tasks.add_task(
                    context.upload_analysis_svc.analyze_session,
                    student_id,
                    session_id,
                    turns,
                    sha,
                    request_id,
                )
                log.info(
                    "bulk transcript unchanged; retrying failed analysis student=%s session=%s sha=%s",
                    student_id,
                    session_id[:8],
                    sha[:12],
                )
                return {
                    "ok": True,
                    "skipped": True,
                    "session_id": session_id,
                    "sha": sha,
                    "stored": 0,
                    "analysis_scheduled": True,
                    "retry_analysis": True,
                }
            log.info(
                "bulk transcript skipped unchanged student=%s session=%s sha=%s",
                student_id,
                session_id[:8],
                sha[:12],
            )
            return {
                "ok": True,
                "skipped": True,
                "session_id": session_id,
                "sha": sha,
                "stored": 0,
                "analysis_scheduled": False,
                "retry_analysis": False,
            }

        raw = UploadAnalysisService._filtered_content_to_raw(data.filtered_content)
        snap = parse_text(raw)
        turns = parse_turns(snap.messages)
        try:
            stored = store.replace_session_messages(
                session_id=session_id,
                student_id=student_id,
                turns=turns,
                raw=raw,
                sha=sha,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        store.set_raw_transcript_analysis_status(
            session_id,
            student_id,
            status="pending" if analysis_scheduled else "skipped",
            error_message="",
            content_sha256=sha,
        )
        if analysis_scheduled:
            background_tasks.add_task(
                context.upload_analysis_svc.analyze_session,
                student_id,
                session_id,
                turns,
                sha,
                request_id,
            )
        log.info(
            "bulk transcript accepted student=%s session=%s messages=%d sha=%s llm=%s",
            student_id,
            session_id[:8],
            stored,
            sha[:12],
            analysis_scheduled,
        )
        return {
            "ok": True,
            "skipped": False,
            "session_id": session_id,
            "sha": sha,
            "stored": stored,
            "analysis_scheduled": analysis_scheduled,
            "retry_analysis": False,
        }

    @app.get("/api/transcripts/known")
    async def known_transcript_shas(
        student_id: str,
        manifest_version: int = 1,
        _: None = Depends(require_student_token),
        store: Store = Depends(get_store),
    ):
        manifest = store.get_known_session_shas(student_id)
        if manifest_version >= 2:
            return manifest
        return {session_id: entry["sha"] for session_id, entry in manifest.items()}

    @app.post("/api/mentor/students/{student_id}/request-upload")
    async def request_student_upload(
        student_id: str,
        data: MentorUploadRequestIn | None = None,
        _: None = Depends(require_mentor_token),
        context: AppContext = Depends(get_context),
        upload_svc: UploadRequestService = Depends(get_upload_service),
    ):
        body = data or MentorUploadRequestIn()
        mentor_id = (body.mentor_id or "mentor").strip() or "mentor"
        session_id = (body.session_id or "").strip() or None
        request_id = upload_svc.create(
            mentor_id=mentor_id,
            student_id=student_id,
            session_id=session_id,
        )
        payload = {
            "type": "mentor_command",
            "student_id": student_id,
            "command": "upload_conversations",
            "request_id": request_id,
            "session_id": session_id or "",
            "mentor_id": mentor_id,
            "timestamp": time.time(),
        }
        await context.bus.publish(payload)
        log.info(
            "upload requested mentor=%s student=%s session=%s request_id=%s",
            mentor_id,
            student_id,
            session_id or "*",
            request_id,
        )
        return {
            "request_id": request_id,
            "status": "pending",
            "student_id": student_id,
            "session_id": session_id or "",
            "transfer_status": "pending",
            "analysis_status": "not_requested",
        }

    @app.get("/api/mentor/upload-requests/{request_id}")
    async def get_mentor_upload_request(
        request_id: str,
        _: None = Depends(require_mentor_token),
        upload_svc: UploadRequestService = Depends(get_upload_service),
    ):
        try:
            row = upload_svc.get(request_id)
        except UploadRequestNotFound as exc:
            raise HTTPException(status_code=404, detail="upload request not found") from exc
        return upload_svc.to_response(row)

    @app.post(
        "/api/mentor/upload-requests/{request_id}/retry-analysis",
        status_code=202,
    )
    async def retry_mentor_upload_analysis(
        request_id: str,
        background_tasks: BackgroundTasks,
        _: None = Depends(require_mentor_token),
        context: AppContext = Depends(get_context),
        upload_svc: UploadRequestService = Depends(get_upload_service),
    ):
        try:
            pending, work_items = upload_svc.prepare_analysis_retry(request_id)
        except UploadRequestNotFound as exc:
            raise HTTPException(status_code=404, detail="upload request not found") from exc
        except UploadTranscriptNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except InvalidStateTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        response = upload_svc.to_response(pending)
        await context.upload_analysis_svc._publish_upload_request_status(pending)
        for item in work_items:
            raw_row = item["raw"]
            background_tasks.add_task(
                context.upload_analysis_svc.retry_request,
                request_id,
                str(pending.get("student_id") or ""),
                str(item.get("session_id") or ""),
                str(raw_row.get("content") or ""),
                str(item.get("sha") or ""),
            )
        return response

    @app.get("/api/student/upload-requests")
    async def list_student_upload_requests(
        student_id: str,
        status: Literal["pending", "running", "done", "failed", "all"] = "pending",
        _: None = Depends(require_student_token),
        upload_svc: UploadRequestService = Depends(get_upload_service),
    ):
        status_value = None if status == "all" else status
        return {"items": [
            upload_svc.to_response(row)
            for row in upload_svc.list(student_id=student_id, status=status_value)
        ]}

    @app.post("/api/student/upload-requests/{request_id}/status")
    async def update_student_upload_request_status(
        request_id: str,
        data: UploadRequestStatusIn,
        _: None = Depends(require_student_token),
        context: AppContext = Depends(get_context),
        upload_svc: UploadRequestService = Depends(get_upload_service),
    ):
        transfer_status = "stored" if data.status == "done" else data.status
        try:
            row = upload_svc.mark_transfer(
                request_id,
                data.student_id,
                transfer_status,
                error=data.error_message,
                result=data.result,
            )
        except UploadRequestNotFound as exc:
            raise HTTPException(status_code=404, detail="upload request not found")
        except InvalidStateTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await context.upload_analysis_svc._publish_upload_request_status(row)
        parent_rows = upload_svc.refresh_parent_analysis(request_id, data.student_id)
        await context.upload_analysis_svc._publish_upload_parent_rows(parent_rows)
        latest = parent_rows[-1] if parent_rows else row
        return upload_svc.to_response(latest)

    @app.get("/sessions")
    async def list_sessions(
        student_id: str | None = None,
        limit: int = 10,
        _: None = Depends(require_student_token),
        context: AppContext = Depends(get_context),
        store: Store = Depends(get_store),
    ):
        sid = student_id or context.config.get("student_id", "student-1")
        conversations = store.get_sessions_by_student(sid, limit=limit)
        return {"items": [c.__dict__ for c in conversations]}

    @app.get("/current_session")
    async def current_session(
        work_dir: str | None = None,
        student_id: str | None = None,
        _: None = Depends(require_student_token),
        context: AppContext = Depends(get_context),
        store: Store = Depends(get_store),
    ):
        sid = student_id or context.config.get("student_id", "student-1")
        active = store.get_active_session_from_table(work_dir, student_id=sid)
        if not active:
            return {"session_id": None, "items": []}
        all_sessions = store.list_sessions_from_table(work_dir, student_id=sid, limit=8)
        items = [{
            "session_id": s["session_id"],
            "work_dir": s["work_dir"],
            "resumed_at": s["resumed_at"],
            "session_title": s.get("title", ""),
            "is_active": s["session_id"] == active["session_id"],
        } for s in all_sessions]
        return {
            "session_id": active["session_id"],
            "work_dir": active["work_dir"],
            "resumed_at": active["resumed_at"],
            "items": items,
        }

    @app.get("/alerts/unread")
    async def unread_alerts(
        since: float = 0.0,
        student_id: str | None = None,
        _: None = Depends(require_student_token),
        store: Store = Depends(get_store),
    ):
        return {"items": store.unread_alerts(since, student_id)}

    @app.post("/api/mentor/message")
    async def send_mentor_message(
        data: MentorMessageIn,
        _: None = Depends(require_mentor_token),
        message_svc: MessageService = Depends(get_message_service),
    ):
        return await message_svc.send(
            student_id=data.student_id,
            mentor_id=data.mentor_id,
            text=data.text,
        )

    @app.get("/api/student/messages")
    async def get_student_messages(
        student_id: str,
        since: int = 0,
        limit: int | None = None,
        _: None = Depends(require_student_token),
        message_svc: MessageService = Depends(get_message_service),
    ):
        return {"items": message_svc.get_catchup(student_id, since, limit=limit)}

    @app.get("/api/student/messages/pending-receipts")
    async def get_pending_student_message_receipts(
        student_id: str,
        limit: int = 64,
        after_id: int = 0,
        _: None = Depends(require_student_token),
        message_svc: MessageService = Depends(get_message_service),
    ):
        return {
            "items": message_svc.get_pending_receipts(
                student_id,
                limit=limit,
                after_id=after_id,
            )
        }

    @app.post("/api/student/messages/ack")
    async def ack_student_message(
        data: StudentMessageAckIn,
        _: None = Depends(require_student_token),
        message_svc: MessageService = Depends(get_message_service),
    ):
        result = message_svc.ack(data.message_id, data.student_id)
        ok = await result if inspect.isawaitable(result) else result
        if not ok:
            raise HTTPException(status_code=404, detail="message not found")
        return {"ok": True}

    @app.post("/api/student/ask")
    async def ask_copilot(
        data: StudentAskIn,
        _: None = Depends(require_student_token),
        context: AppContext = Depends(get_context),
        store: Store = Depends(get_store),
    ):
        student_id = data.student_id.strip()
        question = data.question.strip()
        session_id = (data.session_id or "").strip() or None
        if not student_id:
            raise HTTPException(status_code=400, detail="student_id is required")
        if not question:
            raise HTTPException(status_code=400, detail="question is required")

        context_messages = _build_student_question_context(store, student_id, session_id)
        try:
            answer = await asyncio.wait_for(
                llm_answer_question(context.config, question, context_messages),
                timeout=_student_ask_timeout(context.config),
            )
        except Exception as exc:
            log.warning("student ask LLM failed, using fallback: %s", exc)
            answer = question_fallback_answer()

        ask_id = store.add_student_ask(
            student_id=student_id,
            session_id=session_id,
            question=question,
            answer=answer,
        )
        await context.bus.publish({
            "type": "student_ask",
            "student_id": student_id,
            "session_id": session_id or "",
            "ask_id": ask_id,
            "question": question[:300],
            "timestamp": time.time(),
        })
        return {"ask_id": ask_id, "answer": answer}

    @app.delete("/api/admin/students/{student_id}")
    async def delete_student(
        student_id: str,
        _: None = Depends(require_mentor_token),
        store: Store = Depends(get_store),
    ):
        return {"deleted": store.delete_student(student_id)}

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        context: AppContext = ws.app.state.context
        registry = context.ws_registry
        student_id = ws.query_params.get("student_id") or ""
        token = _extract_supplied_token(
            ws.headers.get("authorization"),
            ws.headers.get("x-copilot-token"),
        ) or ws.query_params.get("token")
        if not student_id or not token_is_valid(context.config, token, role="student"):
            await ws.close(code=1008)
            return
        await ws.accept()
        registry.register_float(student_id, ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            log.warning("float WS error: %s", exc)
        finally:
            registry.unregister_float(student_id, ws)

    @app.websocket("/ws/mentor")
    async def mentor_ws(ws: WebSocket):
        context: AppContext = ws.app.state.context
        registry = context.ws_registry
        token = ws.query_params.get("token")
        if not token_is_valid(context.config, token, role="mentor"):
            await ws.close(code=1008)
            return
        await ws.accept()
        registry.register_mentor(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            log.warning("mentor WS error: %s", exc)
        finally:
            registry.unregister_mentor(ws)

    return app


app = create_app()
ws_clients = app.state.context.ws_registry.floats
mentor_ws_clients = app.state.context.ws_registry.mentors
