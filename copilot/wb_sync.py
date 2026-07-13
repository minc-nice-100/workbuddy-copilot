"""Student-side WorkBuddy session inventory sync.

This module runs on the student machine. It reads local WorkBuddy SQLite state
in read-only mode, labels each session as a space or task, and posts the result
to the central Copilot service.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import load_config
from .student_platform.workbuddy import WorkBuddyDataAdapter

log = logging.getLogger("copilot.wb_sync")

DEFAULT_DB_PATH = Path.home() / ".workbuddy" / "workbuddy.db"


def _connect_readonly(db_path: str | os.PathLike[str]) -> sqlite3.Connection:
    """Legacy private helper retained for callers that still need a connection."""
    path = Path(db_path).expanduser()
    return WorkBuddyDataAdapter(path.parent, database_path=path)._connect_readonly()


def _adapter_for_db(db_path: str | os.PathLike[str]) -> WorkBuddyDataAdapter:
    path = Path(db_path).expanduser()
    return WorkBuddyDataAdapter(path.parent, database_path=path)


def _ms_to_seconds(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value) / 1000.0
    except (TypeError, ValueError):
        return 0.0


def _norm_path(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    return os.path.normpath(os.path.expanduser(text))


def _dir_name(path: str) -> str:
    normalized = _norm_path(path)
    if not normalized:
        return ""
    return os.path.basename(normalized) or normalized


def _workspace_display_name(workspace: dict[str, Any]) -> str:
    for key in ("name", "title", "display_name"):
        value = workspace.get(key)
        if value:
            return str(value)
    return _dir_name(str(workspace.get("path") or ""))


def read_sessions(
    db_path: str | os.PathLike[str] = DEFAULT_DB_PATH,
    *,
    include_deleted: bool = False,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Compatibility wrapper over the explicit WorkBuddy platform adapter."""
    return [
        session.to_dict()
        for session in _adapter_for_db(db_path).list_sessions(
            include_deleted=include_deleted, limit=limit
        )
    ]


def read_workspaces(
    db_path: str | os.PathLike[str] = DEFAULT_DB_PATH,
    *,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Compatibility wrapper over the explicit WorkBuddy platform adapter."""
    return _adapter_for_db(db_path).list_workspaces(limit=limit)


def annotate_sessions_with_groups(
    sessions: list[dict[str, Any]],
    workspaces: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach group_type and space_name using WorkBuddy's space/task rule."""
    workspace_names = {
        _norm_path(workspace.get("path")): _workspace_display_name(workspace)
        for workspace in workspaces
        if _norm_path(workspace.get("path"))
    }

    annotated: list[dict[str, Any]] = []
    for session in sessions:
        row = dict(session)
        work_dir = str(row.get("work_dir") or row.get("cwd") or "")
        normalized = _norm_path(work_dir)
        if normalized in workspace_names:
            row["group_type"] = "space"
            row["space_name"] = workspace_names[normalized]
        else:
            row["group_type"] = "task"
            row["space_name"] = _dir_name(work_dir)
        annotated.append(row)
    return annotated


def build_payload(student_id: str, sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the server contract body for /api/sessions/sync."""
    return {
        "student_id": student_id,
        "sessions": [
            {
                "session_id": str(session.get("session_id") or ""),
                "title": str(session.get("title") or ""),
                "work_dir": str(session.get("work_dir") or ""),
                "group_type": session.get("group_type"),
                "space_name": str(session.get("space_name") or ""),
                "created_at": float(session.get("created_at") or 0.0),
                "last_activity_at": float(session.get("last_activity_at") or 0.0),
            }
            for session in sessions
            if session.get("session_id")
        ],
    }


def _config_token(cfg: dict[str, Any]) -> str:
    auth = cfg.get("auth", {}) or {}
    return (
        os.environ.get("COPILOT_STUDENT_TOKEN")
        or os.environ.get("COPILOT_TOKEN")
        or str(auth.get("student_token", "") or "")
        or str(cfg.get("token", "") or "")
        or str(auth.get("token", "") or "")
        or str(cfg.get("service", {}).get("token", "") or "")
    )


def _student_id(cfg: dict[str, Any], override: str | None) -> str:
    return (
        override
        or os.environ.get("COPILOT_STUDENT_ID")
        or str(cfg.get("student_id", "") or "")
    )


def _server_url(cfg: dict[str, Any], override: str | None) -> str:
    explicit = override or os.environ.get("COPILOT_SERVER_URL")
    if explicit:
        return explicit.rstrip("/")
    service = cfg.get("service", {})
    public_base = str(service.get("public_base_url") or "").strip()
    if public_base:
        return public_base.rstrip("/")
    host = service.get("host", "127.0.0.1")
    port = service.get("port", 8765)
    return f"http://{host}:{port}"


def _load_runtime_config(path: str | None) -> dict[str, Any]:
    try:
        return load_config(path)
    except FileNotFoundError as exc:
        log.warning("config not found; using env/defaults: %s", exc)
        return {}


def post_sync(
    url: str,
    payload: dict[str, Any],
    token: str = "",
    timeout: float = 10.0,
) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-Copilot-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}


def sync_once(
    *,
    db_path: str | os.PathLike[str] = DEFAULT_DB_PATH,
    student_id: str,
    server_url: str,
    token: str = "",
    include_deleted: bool = False,
    limit: int = 1000,
    timeout: float = 10.0,
) -> dict[str, Any]:
    sessions = read_sessions(db_path, include_deleted=include_deleted, limit=limit)
    workspaces = read_workspaces(db_path, limit=limit)
    annotated = annotate_sessions_with_groups(sessions, workspaces)
    payload = build_payload(student_id, annotated)
    endpoint = server_url.rstrip("/") + "/api/sessions/sync"
    log.info(
        "syncing sessions student=%s sessions=%d endpoint=%s",
        student_id,
        len(payload["sessions"]),
        endpoint,
    )
    return post_sync(endpoint, payload, token=token, timeout=timeout)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync WorkBuddy sessions to Copilot")
    parser.add_argument("--config", default=os.environ.get("COPILOT_CONFIG"))
    parser.add_argument("--db-path", default=os.environ.get("COPILOT_WB_DB") or str(DEFAULT_DB_PATH))
    parser.add_argument("--student-id", default=None)
    parser.add_argument("--server-url", default=None)
    parser.add_argument("--token", default=None)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--include-deleted", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    args = _parse_args(argv)
    cfg = _load_runtime_config(args.config)
    student_id = _student_id(cfg, args.student_id)
    if not student_id:
        print("COPILOT_STUDENT_ID or config student_id is required", file=sys.stderr)
        return 2
    token = args.token if args.token is not None else _config_token(cfg)
    server_url = _server_url(cfg, args.server_url)

    try:
        sessions = read_sessions(args.db_path, include_deleted=args.include_deleted, limit=args.limit)
        workspaces = read_workspaces(args.db_path, limit=args.limit)
        payload = build_payload(student_id, annotate_sessions_with_groups(sessions, workspaces))
        if args.dry_run:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        endpoint = server_url.rstrip("/") + "/api/sessions/sync"
        result = post_sync(endpoint, payload, token=token, timeout=args.timeout)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, sqlite3.Error, urllib.error.URLError, TimeoutError) as exc:
        log.error("sync failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
