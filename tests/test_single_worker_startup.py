from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _health_is_up(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health",
            timeout=0.2,
        ) as response:
            body = json.load(response)
            return response.status == 200 and body.get("status") == "UP"
    except (OSError, TimeoutError, urllib.error.URLError):
        return False


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        if process.poll() is None:
            process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        process.wait(timeout=3)


@pytest.mark.component
@pytest.mark.server
@pytest.mark.critical
def test_uvicorn_cli_workers_two_fails_closed_before_serving_health(tmp_path: Path):
    port = _unused_loopback_port()
    log_path = tmp_path / "uvicorn-workers-2.log"
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env.pop("WORKBUDDY_CONFIG_DIR", None)
    for name in ("COPILOT_WORKERS", "UVICORN_WORKERS", "WEB_CONCURRENCY"):
        env.pop(name, None)

    with log_path.open("wb") as log_file:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "copilot.service:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                "2",
                "--log-level",
                "warning",
            ],
            cwd=PROJECT_ROOT,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        health_seen = False
        natural_returncode: int | None = None
        post_exit_deadline: float | None = None
        try:
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                observed_returncode = process.poll()
                if observed_returncode is not None and natural_returncode is None:
                    natural_returncode = observed_returncode
                    post_exit_deadline = time.monotonic() + 0.5
                if _health_is_up(port):
                    health_seen = True
                    break
                if post_exit_deadline is not None and time.monotonic() >= post_exit_deadline:
                    break
                time.sleep(0.05)
        finally:
            _stop_process_group(process)

    output = log_path.read_text(encoding="utf-8", errors="replace")
    assert not health_seen, f"multi-worker supervisor served /health:\n{output}"
    assert natural_returncode is not None, (
        "multi-worker supervisor stayed alive instead of failing closed:\n" + output
    )
    assert natural_returncode != 0, output
