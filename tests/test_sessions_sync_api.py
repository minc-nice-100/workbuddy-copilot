from __future__ import annotations

from fastapi.testclient import TestClient

from copilot.app_context import AppContext
from copilot.connections import WSRegistry
from copilot.eventbus import EventBus
from copilot.service import create_app
from copilot.services import AnalysisService, MessageService, SessionQueryService
from copilot.store import Store


TOKEN = "sync-token"


async def _unused_llm(config, snap, event, latest_prompt):
    raise AssertionError("sync API must not call LLM")


def _build_test_app(tmp_path):
    store = Store(tmp_path / "copilot.db")
    bus = EventBus()
    registry = WSRegistry()
    bus.subscribe(registry.handle_event)
    config = {
        "student_id": "mentor-host",
        "student_name": "Mentor Host",
        "auth": {"token": TOKEN},
    }
    context = AppContext(
        config=config,
        store=store,
        analysis_svc=AnalysisService(store, _unused_llm, config, bus),
        session_svc=SessionQueryService(store, config),
        message_svc=MessageService(store, bus),
        bus=bus,
        ws_registry=registry,
    )
    return create_app(context), store


def _headers() -> dict[str, str]:
    return {"X-Copilot-Token": TOKEN}


def test_sync_sessions_upserts_rows_and_mentor_api_returns_unanalyzed_sessions(tmp_path):
    app, store = _build_test_app(tmp_path)
    with TestClient(app) as client:
        resp = client.post(
            "/api/sessions/sync",
            headers=_headers(),
            json={
                "student_id": "stu-sync",
                "sessions": [
                    {
                        "session_id": "sess-space",
                        "title": "Space title",
                        "work_dir": "/Users/student/projects/plc",
                        "group_type": "space",
                        "space_name": "plc",
                        "created_at": 50.0,
                        "last_activity_at": 200.0,
                    },
                    {
                        "session_id": "sess-task",
                        "title": "Task title",
                        "work_dir": "/Users/student/WorkBuddy/task-1",
                        "group_type": "task",
                        "space_name": "task-1",
                        "created_at": 20.0,
                        "last_activity_at": 100.0,
                    },
                ],
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "synced": 2}

        with store._conn() as conn:
            created_at = conn.execute(
                "SELECT created_at FROM sessions WHERE session_id = ?",
                ("sess-space",),
            ).fetchone()["created_at"]
        assert created_at == 50.0

        store.replace_session_messages(
            "sess-space",
            "stu-sync",
            [
                {"seq": 0, "role": "user", "text": "space question", "ts": 210.0},
                {"seq": 0, "role": "assistant", "text": "space answer", "ts": 211.0},
            ],
            raw="space raw",
            sha="sha-space",
        )
        store.replace_session_messages(
            "sess-task",
            "stu-sync",
            [
                {"seq": 0, "role": "user", "text": "task question", "ts": 110.0},
                {"seq": 0, "role": "assistant", "text": "task answer", "ts": 111.0},
            ],
            raw="task raw",
            sha="sha-task",
        )
        report_id = store.add_report("stu-sync", "sess-task", "Stop", "", "", 1, 0)
        store.add_analysis(
            report_id,
            "stu-sync",
            {
                "topic": "loop debugging",
                "understanding": "high",
                "severity": "warn",
                "diagnosis": "needs boundary checks",
            },
            "sess-task",
            "Task title",
        )

        sessions = client.get(
            "/api/mentor/students/stu-sync/sessions",
            headers=_headers(),
        )
        assert sessions.status_code == 200
        by_id = {row["session_id"]: row for row in sessions.json()["items"]}

        assert set(by_id) == {"sess-space", "sess-task"}
        assert by_id["sess-space"]["session_title"] == "Space title"
        assert by_id["sess-space"]["group_type"] == "space"
        assert by_id["sess-space"]["space_name"] == "plc"
        assert by_id["sess-space"]["message_count"] == 2
        assert by_id["sess-space"]["analysis_count"] == 0
        assert by_id["sess-space"]["alert_count"] == 0
        assert by_id["sess-space"]["last_severity"] == "info"
        assert by_id["sess-space"]["last_topic"] == ""

        assert by_id["sess-task"]["group_type"] == "task"
        assert by_id["sess-task"]["space_name"] == "task-1"
        assert by_id["sess-task"]["message_count"] == 2
        assert by_id["sess-task"]["analysis_count"] == 1
        assert by_id["sess-task"]["last_severity"] == "warn"
        assert by_id["sess-task"]["last_topic"] == "loop debugging"


def test_mentor_sessions_api_returns_more_than_twenty_synced_sessions(tmp_path):
    app, _store = _build_test_app(tmp_path)
    payload_sessions = [
        {
            "session_id": f"sess-{i:02d}",
            "title": f"Session {i:02d}",
            "work_dir": f"/Users/student/WorkBuddy/task-{i:02d}",
            "group_type": "task",
            "space_name": f"task-{i:02d}",
            "created_at": float(i),
            "last_activity_at": float(100 + i),
        }
        for i in range(25)
    ]
    with TestClient(app) as client:
        sync = client.post(
            "/api/sessions/sync",
            headers=_headers(),
            json={"student_id": "stu-many", "sessions": payload_sessions},
        )
        assert sync.status_code == 200

        resp = client.get(
            "/api/mentor/students/stu-many/sessions",
            headers=_headers(),
        )

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 25
    assert items[0]["session_id"] == "sess-24"
    assert items[-1]["session_id"] == "sess-00"
