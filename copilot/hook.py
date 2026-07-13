#!/usr/bin/env python3
"""WorkBuddy hook: bounded, local, fire-and-forget event capture.

This file intentionally has no dependency on the ``copilot`` package.  It is
copied/linked into WorkBuddy's hook environment and must remain usable with a
plain Python standard library.  Network delivery is the Student Core agent's
responsibility; this process only writes one small JSON envelope atomically.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import uuid


DEFAULT_TAIL_BYTES = 256 * 1024
_EVENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
DEFAULT_CONFIG = {
    "service": {"host": "COPILOT_SERVER_HOST", "port": 8765},
    "hook": {"transcript_tail_bytes": DEFAULT_TAIL_BYTES},
}


def _script_config_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def _load_config() -> dict:
    """Load config and apply environment overrides without exposing secrets."""
    cfg_path = os.environ.get("COPILOT_CONFIG") or _script_config_path()
    cfg: dict = {}
    try:
        with open(cfg_path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
            if isinstance(loaded, dict):
                cfg = loaded
    except FileNotFoundError:
        print("[copilot hook] config not found; using defaults", file=sys.stderr)
    except Exception as exc:
        print(
            f"[copilot hook] config load failed ({type(exc).__name__}); using defaults",
            file=sys.stderr,
        )

    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    for key, value in cfg.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value

    # Keep these overrides in the config contract for the installer/agent.  A
    # hook never sends them over the network (or writes them to the spool).
    if os.environ.get("COPILOT_STUDENT_ID"):
        merged["student_id"] = os.environ["COPILOT_STUDENT_ID"]
    if os.environ.get("COPILOT_STUDENT_TOKEN"):
        merged.setdefault("auth", {})["student_token"] = os.environ["COPILOT_STUDENT_TOKEN"]
    if os.environ.get("COPILOT_TOKEN"):
        merged["token"] = os.environ["COPILOT_TOKEN"]
    if os.environ.get("COPILOT_SERVER_URL"):
        merged.setdefault("service", {})["public_base_url"] = os.environ["COPILOT_SERVER_URL"]
    return merged


def _spool_dir(cfg: dict) -> str:
    """Resolve spool path: env, config.student.spool_dir, then local spool/."""
    configured = os.environ.get("COPILOT_SPOOL_DIR")
    if not configured:
        student = cfg.get("student", {})
        if isinstance(student, dict):
            configured = student.get("spool_dir")
    if not configured:
        configured = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spool")
    return os.path.abspath(os.path.expanduser(str(configured)))


def _tail_size(cfg: dict) -> int:
    hook_cfg = cfg.get("hook", {})
    raw = hook_cfg.get("transcript_tail_bytes") if isinstance(hook_cfg, dict) else None
    if raw is None:
        raw = cfg.get("transcript_tail_bytes", DEFAULT_TAIL_BYTES)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_TAIL_BYTES
    # Keep a hard upper bound even if a stale/hostile config asks for more.
    return max(0, min(value, DEFAULT_TAIL_BYTES))


def _read_transcript_tail(path: str, max_bytes: int = DEFAULT_TAIL_BYTES) -> str:
    """Read at most ``max_bytes`` from the end, preserving raw-byte bounds."""
    if not isinstance(path, str) or not path or max_bytes <= 0:
        return ""
    max_bytes = min(int(max_bytes), DEFAULT_TAIL_BYTES)
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes), os.SEEK_SET)
        # Ignore an incomplete/invalid leading UTF-8 sequence rather than
        # expanding each bad byte to a three-byte replacement character.  This
        # keeps the serialized text within the raw-byte read bound and retains
        # the newest valid suffix.
        text = handle.read(max_bytes).decode("utf-8", errors="ignore")
    return _fit_serialized_tail(text, max_bytes)


def _serialized_text_size(value: str) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _fit_serialized_tail(text: str, budget: int) -> str:
    """Keep the newest suffix whose JSON string representation fits budget."""
    if budget <= 0 or not text:
        return ""
    if _serialized_text_size(text) <= budget:
        return text
    # Binary search avoids repeatedly trimming a large invalid/escaped tail.
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = text[-middle:]
        if _serialized_text_size(candidate) <= budget:
            low = middle
        else:
            high = middle - 1
    return text[-low:] if low else ""


def _valid_event_id(event_id: str) -> bool:
    return isinstance(event_id, str) and _EVENT_ID.fullmatch(event_id) is not None


def _write_spool_event(spool_dir: str, payload: dict[str, str]) -> str:
    """Atomically write the HookEvent/SpoolEntry-compatible JSON envelope."""
    event_id = uuid.uuid4().hex
    if not _valid_event_id(event_id):  # defensive: uuid4().hex is always safe
        raise ValueError("invalid generated event id")
    os.makedirs(spool_dir, mode=0o700, exist_ok=True)
    destination = os.path.join(spool_dir, f"{event_id}.json")
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=spool_dir,
            prefix=f".{event_id}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = handle.name
            json.dump(
                {"event_id": event_id, "payload": payload},
                handle,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return event_id


def _event_from_input(hook_input: dict, cfg: dict) -> dict[str, str]:
    event = hook_input.get("hook_event_name")
    if not isinstance(event, str) or not event.strip():
        raise ValueError("hook_event_name is required")
    student_id = cfg.get("student_id")
    if not isinstance(student_id, str) or not student_id.strip():
        raise ValueError("student_id is required")

    def text(name: str) -> str:
        value = hook_input.get(name, "")
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        return value

    transcript_path = text("transcript_path")
    transcript_tail = ""
    if transcript_path:
        transcript_tail = _read_transcript_tail(transcript_path, _tail_size(cfg))
    return {
        "event": event,
        "student_id": student_id,
        "session_id": text("session_id"),
        "cwd": text("cwd"),
        "transcript_tail": transcript_tail,
        # A local path is never sent to the server.  Full transcript reads are
        # an agent-side WorkBuddyData concern, not a Hook payload concern.
        "transcript_path": "",
        "prompt": text("prompt"),
    }


def main() -> int:
    """Capture one event; all malformed input/filesystem failures return 0."""
    try:
        raw = sys.stdin.read()
        if not isinstance(raw, str) or not raw.strip():
            return 0
        hook_input = json.loads(raw)
        if not isinstance(hook_input, dict):
            return 0
        cfg = _load_config()
        payload = _event_from_input(hook_input, cfg)
        _write_spool_event(_spool_dir(cfg), payload)
    except Exception as exc:
        # Hook failures must never block or fail WorkBuddy.  The message is
        # deliberately generic and never includes transcript/token contents.
        print(f"[copilot hook] degraded after error: {type(exc).__name__}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
