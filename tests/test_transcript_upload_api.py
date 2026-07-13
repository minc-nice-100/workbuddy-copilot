from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi.testclient import TestClient

from copilot.app_context import AppContext
from copilot.connections import WSRegistry
from copilot.eventbus import EventBus
from copilot.llm import AnalysisOutcome
from copilot.service import (
    _handle_stop_background,
    create_app,
)
from copilot.services import AnalysisService, MessageService
from copilot.store import Store
from copilot.upload_analysis import UploadAnalysisService
from copilot.upload_service import UploadRequestService


TOKEN = "upload-token"


async def _analyze_uploaded_session_background(
    context: AppContext,
    student_id: str,
    session_id: str,
    turns: list[dict[str, Any]],
    sha: str,
) -> tuple[bool, str]:
    """Compatibility wrapper: delegates to UploadAnalysisService.analyze_session."""
    upload_svc = context.upload_svc or UploadRequestService(context.store.uploads)
    svc = UploadAnalysisService(
        store=context.store,
        analysis_svc=context.analysis_svc,
        upload_svc=upload_svc,
        bus=context.bus,
        config=context.config,
    )
    return await svc.analyze_session(student_id, session_id, turns, sha)


class FakeWebSocket:
    def __init__(self):
        self.sent: list[dict[str, Any]] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


def _headers() -> dict[str, str]:
    return {"X-Copilot-Token": TOKEN}


def _line(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


def _transcript() -> str:
    return (
        _line({
            "type": "message",
            "role": "user",
            "content": "<user_query>怎么定位循环边界？</user_query>",
            "timestamp": 10.0,
        })
        + _line({
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "先打印 i 和 len(xs)。"}],
            "timestamp": 11.0,
        })
    )


def _build_upload_app(tmp_path, *, llm_enabled: bool = True):
    store = Store(tmp_path / "copilot.db")
    bus = EventBus()
    registry = WSRegistry(send_timeout=0.05)
    bus.subscribe(registry.handle_event)
    llm_calls: list[dict[str, Any]] = []

    async def fake_llm(config, snap, event, latest_prompt):
        llm_calls.append({
            "event": event,
            "latest_prompt": latest_prompt,
            "messages": [(msg.role, msg.text) for msg in snap.messages],
        })
        return {
            "topic": "loop debugging",
            "understanding": "medium",
            "off_topic": False,
            "stuck_at": "",
            "is_technical": True,
            "severity": "info",
            "diagnosis": "学生在定位循环边界。",
            "suggestion": "先用最小列表打印边界变量。",
            "progress": "已进入调试",
            "guidance": "保持最小复现。",
            "alert": "",
            "ai_reply_summary": "建议打印 i 和 len(xs)。",
        }

    llm_config = {
        "enable_llm": llm_enabled,
        "api_key": "sk-test" if llm_enabled else "",
        "model": "test-model",
        "api_base": "https://llm.example/v1",
        "timeout": 30,
    }
    config = {
        "student_id": "mentor-host",
        "student_name": "Mentor Host",
        "auth": {"token": TOKEN},
        "llm": llm_config,
    }
    context = AppContext(
        config=config,
        store=store,
        session_store=store.sessions,
        message_store=store.messages,
        upload_store=store.uploads,
        analysis_svc=AnalysisService(store, fake_llm, config, bus),
                message_svc=MessageService(store, bus),
        bus=bus,
        ws_registry=registry,
    )
    return create_app(context), store, registry, llm_calls


def test_post_transcript_persists_messages_raw_and_runs_background_analysis(tmp_path):
    app, store, _registry, llm_calls = _build_upload_app(tmp_path)

    with TestClient(app) as client:
        resp = client.post(
            "/api/student/sessions/sess-upload/transcript",
            headers=_headers(),
            json={
                "student_id": "student-a",
                "filtered_content": _transcript(),
                "sha": "sha-upload-1",
            },
        )

    assert resp.status_code == 200
    assert resp.json()["stored"] == 2
    assert resp.json()["analysis_scheduled"] is True
    assert llm_calls == [{
        "event": "Stop",
        "latest_prompt": "怎么定位循环边界？",
        "messages": [
            ("user", "怎么定位循环边界？"),
            ("assistant", "先打印 i 和 len(xs)。"),
        ],
    }]
    with store._conn() as conn:
        messages = [
            dict(row)
            for row in conn.execute(
                "SELECT seq, role, text, content_sha256 FROM messages ORDER BY seq, role",
            ).fetchall()
        ]
    assert messages == [
        {"seq": 0, "role": "assistant", "text": "先打印 i 和 len(xs)。", "content_sha256": "sha-upload-1"},
        {"seq": 0, "role": "user", "text": "怎么定位循环边界？", "content_sha256": "sha-upload-1"},
    ]
    raw = store.get_raw_transcript_for_student_session("student-a", "sess-upload")
    assert raw is not None
    assert raw["content"] == _transcript()
    assert raw["content_sha256"] == "sha-upload-1"
    analyses = store.recent_analyses("student-a", limit=10, session_id="sess-upload")
    assert len(analyses) == 1
    assert analyses[0]["topic"] == "loop debugging"
    assert analyses[0]["diagnosis"] == "学生在定位循环边界。"


def test_stop_and_full_upload_share_configured_analysis_gate(tmp_path):
    async def scenario():
        store = Store(tmp_path / "copilot.db")
        bus = EventBus()
        active_calls = 0
        max_active_calls = 0
        call_started = asyncio.Event()
        release_calls = asyncio.Event()
        llm_prompts = []

        async def blocking_llm(config, snap, event, latest_prompt):
            nonlocal active_calls, max_active_calls
            llm_prompts.append(latest_prompt)
            active_calls += 1
            max_active_calls = max(max_active_calls, active_calls)
            call_started.set()
            try:
                await release_calls.wait()
            finally:
                active_calls -= 1
            return {"topic": latest_prompt, "diagnosis": "bounded"}

        config = {
            "service": {"analysis_max_concurrency": 1},
            "llm": {},
        }
        analysis_svc = AnalysisService(store, blocking_llm, config, bus)
        context = AppContext(
            config=config,
            store=store,
            session_store=store.sessions,
            message_store=store.messages,
            upload_store=store.uploads,
            analysis_svc=analysis_svc,
                        message_svc=MessageService(store, bus),
            bus=bus,
            ws_registry=WSRegistry(send_timeout=0.05),
        )

        stop_tail = _line({
            "type": "message",
            "role": "user",
            "content": "normal Stop",
            "sessionId": "stop-session",
        })
        report_id, session_id, _ = analysis_svc.accept_report(
            student_id="student-a",
            session_id="stop-session",
            event="Stop",
            prompt_text="normal Stop",
            transcript_content=stop_tail,
        )
        upload_turns = [
            {"seq": 0, "role": "user", "text": "full upload", "ts": 1.0},
        ]
        store.replace_session_messages(
            "upload-session",
            "student-a",
            upload_turns,
            "complete uploaded transcript",
            "sha-upload",
        )
        store.set_raw_transcript_analysis_status(
            "upload-session",
            "student-a",
            status="pending",
            content_sha256="sha-upload",
        )

        stop_task = asyncio.create_task(_handle_stop_background(
            analysis_svc,
            "student-a",
            session_id,
            "normal Stop",
            stop_tail,
            report_id,
        ))
        await asyncio.wait_for(call_started.wait(), timeout=1)
        upload_task = asyncio.create_task(_analyze_uploaded_session_background(
            context,
            "student-a",
            "upload-session",
            upload_turns,
            "sha-upload",
        ))
        await asyncio.sleep(0.05)
        release_calls.set()
        await asyncio.gather(stop_task, upload_task)

        assert max_active_calls == 1
        assert sorted(llm_prompts) == ["full upload", "normal Stop"]
        assert [row["topic"] for row in store.recent_analyses(
            "student-a", limit=10, session_id="stop-session"
        )] == ["normal Stop"]
        assert [row["topic"] for row in store.recent_analyses(
            "student-a", limit=10, session_id="upload-session"
        )] == ["full upload"]

    asyncio.run(scenario())


def test_bulk_analysis_gate_covers_only_llm_invocation(tmp_path):
    async def scenario():
        store = Store(tmp_path / "copilot.db")
        bus = EventBus()
        states = {}

        async def fixed_llm(config, snap, event, latest_prompt):
            return {"topic": "gate scope", "diagnosis": "llm completed"}

        config = {"service": {"analysis_max_concurrency": 1}, "llm": {}}
        analysis_svc = AnalysisService(store, fixed_llm, config, bus)
        real_commit = store.commit_bulk_analysis_if_current

        def observed_commit(**kwargs):
            states["db"] = analysis_svc.analysis_semaphore.locked()
            return real_commit(**kwargs)

        store.commit_bulk_analysis_if_current = observed_commit

        async def observed_publish(payload):
            if payload.get("event") == "BulkUpload":
                states["publish"] = analysis_svc.analysis_semaphore.locked()

        bus.subscribe(observed_publish)
        context = AppContext(
            config=config,
            store=store,
            session_store=store.sessions,
            message_store=store.messages,
            upload_store=store.uploads,
            analysis_svc=analysis_svc,
                        message_svc=MessageService(store, bus),
            bus=bus,
            ws_registry=WSRegistry(send_timeout=0.05),
        )
        turns = [{"seq": 0, "role": "user", "text": "bulk prompt", "ts": 1.0}]
        store.replace_session_messages(
            "bulk-scope", "student-a", turns, "full transcript", "sha-scope"
        )
        store.set_raw_transcript_analysis_status(
            "bulk-scope", "student-a", status="pending", content_sha256="sha-scope"
        )

        result = await _analyze_uploaded_session_background(
            context, "student-a", "bulk-scope", turns, "sha-scope"
        )

        assert result == (True, "")
        assert states == {"db": False, "publish": False}
        assert [row["topic"] for row in store.recent_analyses(
            "student-a", limit=10, session_id="bulk-scope"
        )] == ["gate scope"]

    asyncio.run(scenario())


def test_post_transcript_same_sha_skips_parse_store_and_llm(tmp_path):
    app, store, _registry, llm_calls = _build_upload_app(tmp_path)

    with TestClient(app) as client:
        first = client.post(
            "/api/student/sessions/sess-upload/transcript",
            headers=_headers(),
            json={
                "student_id": "student-a",
                "filtered_content": _transcript(),
                "sha": "sha-upload-1",
            },
        )
        second = client.post(
            "/api/student/sessions/sess-upload/transcript",
            headers=_headers(),
            json={
                "student_id": "student-a",
                "filtered_content": "not json and must not be parsed",
                "sha": "sha-upload-1",
            },
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["skipped"] is True
    assert len(llm_calls) == 1
    with store._conn() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?",
            ("sess-upload",),
        ).fetchone()[0] == 2


def test_get_known_defaults_to_legacy_manifest_and_v2_is_explicit(tmp_path):
    app, _store, _registry, _llm_calls = _build_upload_app(tmp_path, llm_enabled=False)

    with TestClient(app) as client:
        posted = client.post(
            "/api/student/sessions/sess-known/transcript",
            headers=_headers(),
            json={
                "student_id": "student-a",
                "filtered_content": _transcript(),
                "sha": "sha-known",
            },
        )
        legacy = client.get(
            "/api/transcripts/known?student_id=student-a",
            headers=_headers(),
        )
        manifest_v2 = client.get(
            "/api/transcripts/known?student_id=student-a&manifest_version=2",
            headers=_headers(),
        )

    assert posted.status_code == 200
    assert posted.json()["analysis_scheduled"] is False
    assert legacy.status_code == 200
    assert legacy.json() == {"sess-known": "sha-known"}
    assert manifest_v2.status_code == 200
    assert manifest_v2.json() == {
        "sess-known": {"sha": "sha-known", "analysis_status": "skipped"},
    }


def test_request_upload_persists_request_and_sends_directed_mentor_command(tmp_path):
    app, store, registry, _llm_calls = _build_upload_app(tmp_path, llm_enabled=False)
    mentor_ws = FakeWebSocket()
    target_float = FakeWebSocket()
    other_float = FakeWebSocket()
    registry.register_mentor(mentor_ws)
    registry.register_float("student-a", target_float)
    registry.register_float("student-b", other_float)

    with TestClient(app) as client:
        resp = client.post(
            "/api/mentor/students/student-a/request-upload",
            headers=_headers(),
            json={"mentor_id": "mentor-1"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    request_id = body["request_id"]
    rows = store.list_pending_upload_requests("student-a")
    assert [row["request_id"] for row in rows] == [request_id]
    assert target_float.sent == [{
        "type": "mentor_command",
        "student_id": "student-a",
        "command": "upload_conversations",
        "request_id": request_id,
        "session_id": "",
        "mentor_id": "mentor-1",
        "timestamp": target_float.sent[0]["timestamp"],
    }]
    assert other_float.sent == []
    assert mentor_ws.sent == []


def test_real_requested_multi_session_analysis_waits_for_transfer_completion(tmp_path):
    app, store, _registry, llm_calls = _build_upload_app(tmp_path)

    with TestClient(app) as client:
        request_id = client.post(
            "/api/mentor/students/student-a/request-upload",
            headers=_headers(),
            json={"mentor_id": "mentor-1"},
        ).json()["request_id"]
        assert client.post(
            f"/api/student/upload-requests/{request_id}/status",
            headers=_headers(),
            json={"student_id": "student-a", "status": "running"},
        ).status_code == 200
        for session_id, sha in (("sess-one", "sha-one"), ("sess-two", "sha-two")):
            uploaded = client.post(
                f"/api/student/sessions/{session_id}/transcript",
                headers=_headers(),
                json={
                    "student_id": "student-a",
                    "filtered_content": _transcript(),
                    "sha": sha,
                    "request_id": request_id,
                },
            )
            assert uploaded.status_code == 200
            # Child analysis may finish, but the parent cannot report done before transfer stored.
            assert store.get_upload_request(request_id)["analysis_status"] == "running"
        completed = client.post(
            f"/api/student/upload-requests/{request_id}/status",
            headers=_headers(),
            json={
                "student_id": "student-a",
                "status": "done",
                "result": {"total": 2, "synced": 2, "skipped": 0, "failed": 0},
            },
        )

    assert completed.status_code == 200
    assert completed.json()["analysis_status"] == "done"
    children = store.list_upload_request_sessions(request_id)
    assert [(row["session_id"], row["analysis_status"]) for row in children] == [
        ("sess-one", "done"), ("sess-two", "done")
    ]
    assert len(llm_calls) == 2


def test_requested_same_sha_empty_probe_registers_done_child_without_reparse(tmp_path):
    app, store, _registry, llm_calls = _build_upload_app(tmp_path)
    with TestClient(app) as client:
        seeded = client.post(
            "/api/student/sessions/sess-same/transcript",
            headers=_headers(),
            json={"student_id": "student-a", "filtered_content": _transcript(), "sha": "sha-same"},
        )
        request_id = client.post(
            "/api/mentor/students/student-a/request-upload",
            headers=_headers(),
        ).json()["request_id"]
        client.post(
            f"/api/student/upload-requests/{request_id}/status",
            headers=_headers(),
            json={"student_id": "student-a", "status": "running"},
        )
        probe = client.post(
            "/api/student/sessions/sess-same/transcript",
            headers=_headers(),
            json={
                "student_id": "student-a", "filtered_content": "", "sha": "sha-same",
                "request_id": request_id,
            },
        )
        completed = client.post(
            f"/api/student/upload-requests/{request_id}/status",
            headers=_headers(),
            json={"student_id": "student-a", "status": "done",
                  "result": {"total": 1, "synced": 0, "skipped": 1, "failed": 0}},
        )

    assert seeded.status_code == 200
    assert probe.status_code == 200
    assert probe.json()["skipped"] is True
    assert completed.json()["analysis_status"] == "done"
    assert store.list_upload_request_sessions(request_id)[0]["sha"] == "sha-same"
    assert len(llm_calls) == 1
    assert store.get_raw_transcript_for_student_session("student-a", "sess-same")["content"] == _transcript()


def test_specific_upload_request_rejects_different_session_before_parsing(tmp_path):
    app, store, _registry, _llm_calls = _build_upload_app(tmp_path)
    with TestClient(app) as client:
        request_id = client.post(
            "/api/mentor/students/student-a/request-upload",
            headers=_headers(),
            json={"session_id": "sess-only"},
        ).json()["request_id"]
        rejected = client.post(
            "/api/student/sessions/sess-other/transcript",
            headers=_headers(),
            json={
                "student_id": "student-a", "filtered_content": "must not parse",
                "sha": "sha-other", "request_id": request_id,
            },
        )

    assert rejected.status_code == 409
    assert store.list_upload_request_sessions(request_id) == []


def test_requested_child_failure_only_fails_parent_after_transfer_stored(tmp_path):
    app, store, _registry, _llm_calls = _build_upload_app(tmp_path)

    async def failed_llm(config, snap, event, latest_prompt):
        return AnalysisOutcome(
            ok=False,
            value={"topic": "", "understanding": "unknown", "severity": "info"},
            error="LLM provider TimeoutError",
        )

    app.state.context.analysis_svc.llm = failed_llm
    with TestClient(app) as client:
        request_id = client.post(
            "/api/mentor/students/student-a/request-upload", headers=_headers()
        ).json()["request_id"]
        client.post(
            f"/api/student/upload-requests/{request_id}/status",
            headers=_headers(), json={"student_id": "student-a", "status": "running"},
        )
        uploaded = client.post(
            "/api/student/sessions/sess-fail/transcript",
            headers=_headers(),
            json={"student_id": "student-a", "filtered_content": _transcript(),
                  "sha": "sha-fail", "request_id": request_id},
        )
        before_stored = store.get_upload_request(request_id)
        completed = client.post(
            f"/api/student/upload-requests/{request_id}/status",
            headers=_headers(),
            json={"student_id": "student-a", "status": "done",
                  "result": {"total": 1, "synced": 1, "skipped": 0, "failed": 0}},
        )

    assert uploaded.status_code == 200
    assert before_stored["analysis_status"] == "running"
    assert store.list_upload_request_sessions(request_id)[0]["analysis_status"] == "failed"
    assert completed.json()["analysis_status"] == "failed"
    assert completed.json()["analysis_error"] == "LLM provider TimeoutError"


def test_requested_transcript_with_llm_disabled_keeps_analysis_not_requested(tmp_path):
    app, store, _registry, llm_calls = _build_upload_app(tmp_path, llm_enabled=False)
    with TestClient(app) as client:
        request_id = client.post(
            "/api/mentor/students/student-a/request-upload", headers=_headers()
        ).json()["request_id"]
        client.post(
            f"/api/student/upload-requests/{request_id}/status",
            headers=_headers(), json={"student_id": "student-a", "status": "running"},
        )
        uploaded = client.post(
            "/api/student/sessions/sess-no-llm/transcript",
            headers=_headers(),
            json={"student_id": "student-a", "filtered_content": _transcript(),
                  "sha": "sha-no-llm", "request_id": request_id},
        )
        completed = client.post(
            f"/api/student/upload-requests/{request_id}/status",
            headers=_headers(),
            json={"student_id": "student-a", "status": "done",
                  "result": {"total": 1, "synced": 1, "skipped": 0, "failed": 0}},
        )

    assert uploaded.status_code == 200
    assert uploaded.json()["analysis_scheduled"] is False
    assert llm_calls == []
    child = store.list_upload_request_sessions(request_id)[0]
    assert child["analysis_status"] == "not_requested"
    assert completed.json()["analysis_status"] == "not_requested"


def test_same_sha_retries_background_analysis_after_previous_failure(tmp_path):
    store = Store(tmp_path / "copilot.db")
    bus = EventBus()
    registry = WSRegistry(send_timeout=0.05)
    bus.subscribe(registry.handle_event)
    calls: list[str] = []

    async def flaky_llm(config, snap, event, latest_prompt):
        calls.append(latest_prompt)
        if len(calls) == 1:
            return AnalysisOutcome(
                ok=False,
                value={
                    "topic": "must not be stored",
                    "understanding": "unknown",
                    "severity": "info",
                    "diagnosis": "display fallback only",
                    "ai_reply_summary": "must not be stored",
                },
                error="LLM provider TimeoutError",
            )
        return {
            "topic": "retry worked",
            "understanding": "medium",
            "off_topic": False,
            "stuck_at": "",
            "is_technical": True,
            "severity": "info",
            "diagnosis": "retry succeeded",
            "suggestion": "continue",
            "progress": "ok",
            "guidance": "ok",
            "alert": "",
            "ai_reply_summary": "retry summary",
        }

    config = {
        "student_id": "mentor-host",
        "student_name": "Mentor Host",
        "auth": {"token": TOKEN},
        "llm": {
            "enable_llm": True,
            "api_key": "sk-test",
            "model": "test-model",
            "api_base": "https://llm.example/v1",
            "timeout": 30,
        },
    }
    context = AppContext(
        config=config,
        store=store,
        session_store=store.sessions,
        message_store=store.messages,
        upload_store=store.uploads,
        analysis_svc=AnalysisService(store, flaky_llm, config, bus),
                message_svc=MessageService(store, bus),
        bus=bus,
        ws_registry=registry,
    )
    app = create_app(context)

    with TestClient(app) as client:
        first = client.post(
            "/api/student/sessions/sess-retry/transcript",
            headers=_headers(),
            json={
                "student_id": "student-a",
                "filtered_content": _transcript(),
                "sha": "sha-retry",
            },
        )
        store.add_raw_transcript(
            "sess-retry",
            "student-a",
            _line({"type": "message", "role": "user", "content": "newer live report"}),
        )
        failed_manifest = client.get(
            "/api/transcripts/known?student_id=student-a&manifest_version=2",
            headers=_headers(),
        )
        raw_after_failure = store.get_raw_transcript_for_student_session_sha(
            "student-a", "sess-retry", "sha-retry"
        )
        analyses_after_failure = store.recent_analyses(
            "student-a", limit=10, session_id="sess-retry"
        )
        with store._conn() as conn:
            messages_after_failure = [
                dict(row)
                for row in conn.execute(
                    "SELECT seq, role, text, content_sha256 FROM messages WHERE session_id = ? ORDER BY role",
                    ("sess-retry",),
                ).fetchall()
            ]
        second = client.post(
            "/api/student/sessions/sess-retry/transcript",
            headers=_headers(),
            json={
                "student_id": "student-a",
                "filtered_content": "invalid content should not be reparsed",
                "sha": "sha-retry",
            },
        )

    assert first.status_code == 200
    assert first.json()["analysis_scheduled"] is True
    assert failed_manifest.json() == {
        "sess-retry": {"sha": "sha-retry", "analysis_status": "failed"},
    }
    assert raw_after_failure is not None
    assert raw_after_failure["content"] == _transcript()
    assert raw_after_failure["analysis_status"] == "failed"
    assert raw_after_failure["analysis_error"] == "LLM provider TimeoutError"
    assert analyses_after_failure == []
    assert second.status_code == 200
    assert second.json()["skipped"] is True
    assert second.json()["stored"] == 0
    assert second.json()["analysis_scheduled"] is True
    assert second.json()["retry_analysis"] is True
    assert calls == ["怎么定位循环边界？", "怎么定位循环边界？"]
    raw = store.get_raw_transcript_for_student_session_sha(
        "student-a", "sess-retry", "sha-retry"
    )
    assert raw is not None
    assert raw["content"] == _transcript()
    assert raw["analysis_status"] == "done"
    assert raw["analysis_error"] == ""
    analyses = store.recent_analyses("student-a", limit=10, session_id="sess-retry")
    assert len(analyses) == 1
    assert analyses[0]["topic"] == "retry worked"
    with store._conn() as conn:
        messages_after_retry = [
            dict(row)
            for row in conn.execute(
                "SELECT seq, role, text, content_sha256 FROM messages WHERE session_id = ? ORDER BY role",
                ("sess-retry",),
            ).fetchall()
        ]
        assert conn.execute(
            "SELECT COUNT(*) FROM raw_transcripts WHERE session_id = ?",
            ("sess-retry",),
        ).fetchone()[0] == 2
        live_raw = dict(conn.execute(
            """SELECT * FROM raw_transcripts
               WHERE session_id = ? AND content_sha256 IS NULL""",
            ("sess-retry",),
        ).fetchone())
    assert messages_after_retry == messages_after_failure
    assert live_raw["content"].endswith("\n")
    assert "newer live report" in live_raw["content"]
    assert live_raw["analysis_status"] == ""


def test_stale_bulk_analysis_result_is_discarded_after_new_sha_replaces_it(tmp_path):
    async def scenario():
        store = Store(tmp_path / "copilot.db")
        bus = EventBus()
        registry = WSRegistry(send_timeout=0.05)
        bus.subscribe(registry.handle_event)
        events = []
        started = asyncio.Event()
        release = asyncio.Event()

        async def collect(payload):
            events.append(payload)

        async def blocked_llm(config, snap, event, latest_prompt):
            started.set()
            await release.wait()
            return {
                "topic": "stale A",
                "understanding": "medium",
                "severity": "info",
                "diagnosis": "must be discarded",
                "ai_reply_summary": "",
            }

        bus.subscribe(collect)
        config = {
            "auth": {"token": TOKEN},
            "llm": {
                "enable_llm": True,
                "api_key": "sk-test",
                "model": "test-model",
                "api_base": "https://llm.example/v1",
            },
        }
        context = AppContext(
            config=config,
            store=store,
            session_store=store.sessions,
            message_store=store.messages,
            upload_store=store.uploads,
            analysis_svc=AnalysisService(store, blocked_llm, config, bus),
                        message_svc=MessageService(store, bus),
            bus=bus,
            ws_registry=registry,
        )
        turns_a = [{"seq": 0, "role": "user", "text": "A prompt", "ts": 1.0}]
        turns_b = [{"seq": 0, "role": "user", "text": "B prompt", "ts": 2.0}]
        store.replace_session_messages("sess-race", "student-a", turns_a, "raw A", "sha-a")
        store.set_raw_transcript_analysis_status(
            "sess-race", "student-a", status="pending", content_sha256="sha-a"
        )

        task = asyncio.create_task(
            _analyze_uploaded_session_background(
                context, "student-a", "sess-race", turns_a, "sha-a"
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        store.replace_session_messages("sess-race", "student-a", turns_b, "raw B", "sha-b")
        store.set_raw_transcript_analysis_status(
            "sess-race", "student-a", status="pending", content_sha256="sha-b"
        )
        release.set()
        await asyncio.wait_for(task, timeout=1)

        assert store.recent_analyses("student-a", limit=10, session_id="sess-race") == []
        current = store.get_raw_transcript_for_student_session_sha(
            "student-a", "sess-race", "sha-b"
        )
        assert current is not None
        assert current["analysis_status"] == "pending"
        assert current["analysis_error"] == ""
        assert [event for event in events if event.get("type") == "analysis"] == []

    asyncio.run(scenario())


def test_stop_and_bulk_analysis_share_configured_concurrency_gate(tmp_path):
    async def scenario():
        store = Store(tmp_path / "copilot.db")
        bus = EventBus()
        registry = WSRegistry(send_timeout=0.05)
        bus.subscribe(registry.handle_event)
        release = asyncio.Event()
        overlap_observed = asyncio.Event()
        active_calls = 0
        max_active_calls = 0
        llm_prompts = []

        async def blocked_llm(config, snap, event, latest_prompt):
            nonlocal active_calls, max_active_calls
            llm_prompts.append(latest_prompt)
            active_calls += 1
            max_active_calls = max(max_active_calls, active_calls)
            if active_calls == 2:
                overlap_observed.set()
            try:
                await release.wait()
            finally:
                active_calls -= 1
            return {
                "topic": f"analysis for {latest_prompt}",
                "understanding": "medium",
                "off_topic": False,
                "stuck_at": "",
                "is_technical": True,
                "severity": "info",
                "diagnosis": "Deterministic concurrency test analysis.",
                "suggestion": "Continue.",
                "progress": "active",
                "guidance": "Keep the reproduction minimal.",
                "alert": "",
                "ai_reply_summary": "Deterministic summary.",
            }

        config = {
            "student_id": "mentor-host",
            "service": {"analysis_max_concurrency": 1},
            "llm": {},
        }
        analysis_svc = AnalysisService(store, blocked_llm, config, bus)
        context = AppContext(
            config=config,
            store=store,
            session_store=store.sessions,
            message_store=store.messages,
            upload_store=store.uploads,
            analysis_svc=analysis_svc,
                        message_svc=MessageService(store, bus),
            bus=bus,
            ws_registry=registry,
        )

        stop_transcript = _line({
            "type": "message",
            "role": "user",
            "content": "ordinary stop prompt",
            "sessionId": "sess-stop-gate",
        })
        report_id, _, _ = analysis_svc.accept_report(
            student_id="student-stop",
            session_id="sess-stop-gate",
            event="Stop",
            prompt_text="ordinary stop prompt",
            transcript_content=stop_transcript,
            raw_transcript_content=stop_transcript,
        )

        bulk_turns = [
            {"seq": 0, "role": "user", "text": "bulk upload prompt", "ts": 1.0},
        ]
        store.replace_session_messages(
            "sess-bulk-gate",
            "student-bulk",
            bulk_turns,
            "bulk raw transcript",
            "sha-bulk-gate",
        )
        store.set_raw_transcript_analysis_status(
            "sess-bulk-gate",
            "student-bulk",
            status="pending",
            content_sha256="sha-bulk-gate",
        )

        stop_task = asyncio.create_task(
            analysis_svc.handle_stop(
                "student-stop",
                "sess-stop-gate",
                "ordinary stop prompt",
                stop_transcript,
                report_id,
            )
        )
        bulk_task = asyncio.create_task(
            _analyze_uploaded_session_background(
                context,
                "student-bulk",
                "sess-bulk-gate",
                bulk_turns,
                "sha-bulk-gate",
            )
        )

        for _ in range(20):
            await asyncio.sleep(0)
            if overlap_observed.is_set():
                break
        release.set()
        stop_result, bulk_result = await asyncio.gather(stop_task, bulk_task)

        assert sorted(llm_prompts) == ["bulk upload prompt", "ordinary stop prompt"]
        assert stop_result.to_dict() == {
            "topic": "analysis for ordinary stop prompt",
            "understanding": "medium",
            "off_topic": False,
            "stuck_at": "",
            "is_technical": True,
            "severity": "info",
            "diagnosis": "Deterministic concurrency test analysis.",
            "suggestion": "Continue.",
            "progress": "active",
            "guidance": "Keep the reproduction minimal.",
            "alert": "",
            "ai_reply_summary": "Deterministic summary.",
        }
        assert bulk_result == (True, "")
        assert max_active_calls == 1

    asyncio.run(scenario())
