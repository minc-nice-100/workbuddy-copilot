from __future__ import annotations

import sqlite3

import pytest

from copilot.wb_sync import _server_url, annotate_sessions_with_groups
from copilot.student_platform.workbuddy import WorkBuddyDataError

pytestmark = pytest.mark.student


def test_annotate_sessions_marks_space_when_cwd_is_workspace_path():
    sessions = [
        {
            "session_id": "sess-space",
            "title": "Space session",
            "work_dir": "/Users/student/projects/plc",
            "last_activity_at": 200.0,
        },
        {
            "session_id": "sess-task",
            "title": "Task session",
            "work_dir": "/Users/student/WorkBuddy/2026-07-02-abc",
            "last_activity_at": 100.0,
        },
    ]
    workspaces = [{"path": "/Users/student/projects/plc"}]

    annotated = annotate_sessions_with_groups(sessions, workspaces)

    by_id = {row["session_id"]: row for row in annotated}
    assert by_id["sess-space"]["group_type"] == "space"
    assert by_id["sess-space"]["space_name"] == "plc"
    assert by_id["sess-task"]["group_type"] == "task"
    assert by_id["sess-task"]["space_name"] == "2026-07-02-abc"


def test_annotate_sessions_uses_workspace_name_when_available():
    sessions = [{
        "session_id": "sess-named",
        "title": "Named space",
        "work_dir": "/Users/student/projects/camp",
        "last_activity_at": 1.0,
    }]
    workspaces = [{
        "path": "/Users/student/projects/camp",
        "name": "Camp Space",
    }]

    annotated = annotate_sessions_with_groups(sessions, workspaces)

    assert annotated[0]["group_type"] == "space"
    assert annotated[0]["space_name"] == "Camp Space"


def test_server_url_prefers_public_base_url(monkeypatch):
    monkeypatch.delenv("COPILOT_SERVER_URL", raising=False)
    cfg = {
        "service": {
            "host": "127.0.0.1",
            "port": 8765,
            "public_base_url": "https://copilot.example.com/copilot/",
        }
    }

    assert _server_url(cfg, None) == "https://copilot.example.com/copilot"


def test_server_url_env_override_wins_over_public_base_url(monkeypatch):
    monkeypatch.setenv("COPILOT_SERVER_URL", "https://override.example.com/api/")
    cfg = {"service": {"public_base_url": "https://copilot.example.com"}}

    assert _server_url(cfg, None) == "https://override.example.com/api"


def test_read_sessions_preserves_typed_schema_failure_for_cli_callers(tmp_path):
    from copilot import wb_sync

    database = tmp_path / "workbuddy.db"
    sqlite3.connect(database).close()

    with pytest.raises(WorkBuddyDataError) as caught:
        wb_sync.read_sessions(database)

    assert caught.value.failure.code == "schema_mismatch"
