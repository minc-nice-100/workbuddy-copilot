from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]

pytestmark = [pytest.mark.server, pytest.mark.integration, pytest.mark.critical]


def test_server_build_and_api_smoke_never_access_student_workbuddy_home(tmp_path):
    isolated_home = tmp_path / "isolated-home"
    isolated_home.mkdir()
    config_path = tmp_path / "server-config.json"
    config_path.write_text(
        json.dumps(
            {
                "student_id": "sentinel",
                "service": {"host": "127.0.0.1", "port": 8765},
                "auth": {
                    "mode": "local",
                    "student_token": "",
                    "mentor_token": "",
                },
                "analysis": {"enable_llm": False, "recent_n": 4},
                "llm": {"timeout": 1},
                "store": {"db_path": str(tmp_path / "server-copilot.db")},
            }
        ),
        encoding="utf-8",
    )

    script = textwrap.dedent(
        r"""
        import builtins
        import io
        import os
        from pathlib import Path
        import sqlite3
        import sys

        blocked = (Path(os.environ["HOME"]) / ".workbuddy").absolute()

        def guard(value):
            if isinstance(value, int) or value in {None, ":memory:"}:
                return
            try:
                candidate = Path(os.fsdecode(os.fspath(value))).absolute()
            except (TypeError, ValueError):
                return
            if candidate == blocked or blocked in candidate.parents:
                raise AssertionError(f"server touched student WorkBuddy home: {candidate}")

        real_builtin_open = builtins.open
        def guarded_builtin_open(value, *args, **kwargs):
            guard(value)
            return real_builtin_open(value, *args, **kwargs)
        builtins.open = guarded_builtin_open

        real_io_open = io.open
        def guarded_io_open(value, *args, **kwargs):
            guard(value)
            return real_io_open(value, *args, **kwargs)
        io.open = guarded_io_open

        for name in ("open", "stat", "lstat", "listdir", "scandir", "mkdir"):
            original = getattr(os, name)
            def guarded(value, *args, _original=original, **kwargs):
                guard(value)
                return _original(value, *args, **kwargs)
            setattr(os, name, guarded)

        real_connect = sqlite3.connect
        def guarded_connect(database, *args, **kwargs):
            guard(database)
            return real_connect(database, *args, **kwargs)
        sqlite3.connect = guarded_connect

        # Negative control: prove the sentinel itself turns a forbidden access red.
        try:
            os.stat(blocked / "workbuddy.db")
        except AssertionError:
            pass
        else:
            raise AssertionError("student WorkBuddy sentinel did not fire")

        from fastapi.testclient import TestClient
        from copilot.app_context import build_context
        from copilot.service import create_app

        context = build_context(sys.argv[1])
        app = create_app(context)
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/api/mentor/students").status_code == 200
            assert client.get(
                "/api/student/messages", params={"student_id": "sentinel"}
            ).status_code == 200
            assert client.post(
                "/report",
                json={
                    "student_id": "sentinel",
                    "session_id": "sentinel-session",
                    "event": "Prompt",
                    "prompt": "sentinel prompt",
                    "transcript_tail": "student: sentinel prompt",
                },
            ).status_code == 202
        print("SERVER_HOME_SENTINEL_OK")
        """
    )
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(isolated_home),
            "USERPROFILE": str(isolated_home),
            "PYTHONPATH": str(PROJECT_ROOT),
        }
    )

    result = subprocess.run(
        [sys.executable, "-c", script, str(config_path)],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "SERVER_HOME_SENTINEL_OK" in result.stdout
