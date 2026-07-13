"""Hook contract: bounded local spool, no network, and graceful degradation."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from unittest.mock import MagicMock

import pytest

from copilot.hook import (
    DEFAULT_TAIL_BYTES,
    _load_config,
    _read_transcript_tail,
    _spool_dir,
    main,
)


def _set_stdin(monkeypatch: pytest.MonkeyPatch, value: object) -> None:
    monkeypatch.setattr("sys.stdin", MagicMock(read=lambda: json.dumps(value)))


def _read_spool(spool_dir: Path) -> dict:
    files = sorted(spool_dir.glob("*.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text(encoding="utf-8"))


def _write_config(path: Path, **values: object) -> None:
    path.write_text(json.dumps(values), encoding="utf-8")


def test_config_spool_env_overrides_student_spool_dir(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_spool = tmp_path / "config-spool"
    env_spool = tmp_path / "env-spool"
    _write_config(
        config_path,
        student_id="config-student",
        student={"spool_dir": str(config_spool)},
    )
    monkeypatch.setenv("COPILOT_CONFIG", str(config_path))
    monkeypatch.setenv("COPILOT_SPOOL_DIR", str(env_spool))

    cfg = _load_config()

    assert Path(_spool_dir(cfg)) == env_spool


def test_config_spool_falls_back_to_student_config(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_spool = tmp_path / "config-spool"
    _write_config(
        config_path,
        student_id="config-student",
        student={"spool_dir": str(config_spool)},
    )
    monkeypatch.setenv("COPILOT_CONFIG", str(config_path))
    monkeypatch.delenv("COPILOT_SPOOL_DIR", raising=False)

    assert Path(_spool_dir(_load_config())) == config_spool


def test_student_id_environment_overrides_config(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    _write_config(config_path, student_id="config-student")
    monkeypatch.setenv("COPILOT_CONFIG", str(config_path))
    monkeypatch.setenv("COPILOT_STUDENT_ID", "env-student")

    assert _load_config()["student_id"] == "env-student"


def test_reads_only_bounded_tail_bytes(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_bytes((b"a" * (DEFAULT_TAIL_BYTES + 1024)) + b"TAIL")

    result = _read_transcript_tail(str(transcript), max_bytes=DEFAULT_TAIL_BYTES)

    assert len(result.encode("utf-8")) <= DEFAULT_TAIL_BYTES
    assert result.endswith("TAIL")


def test_stop_hook_writes_bounded_spool_without_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spool_dir = tmp_path / "spool"
    transcript = tmp_path / "huge.jsonl"
    transcript.write_bytes((b"x" * (DEFAULT_TAIL_BYTES + 8192)) + b"last-bytes")
    config_path = tmp_path / "config.json"
    _write_config(
        config_path,
        student_id="student-1",
        student={"spool_dir": str(tmp_path / "wrong-spool")},
        hook={"transcript_tail_bytes": DEFAULT_TAIL_BYTES},
    )
    monkeypatch.setenv("COPILOT_CONFIG", str(config_path))
    monkeypatch.setenv("COPILOT_SPOOL_DIR", str(spool_dir))

    # A network call would violate the hook boundary.  The implementation is
    # deliberately not allowed to import urllib or call an opener at all.
    monkeypatch.setattr("socket.socket", lambda *a, **k: pytest.fail("network forbidden"))
    _set_stdin(
        monkeypatch,
        {
            "session_id": "session-1",
            "hook_event_name": "Stop",
            "prompt": "continue",
            "transcript_path": str(transcript),
            "cwd": "/workspace",
        },
    )

    assert main() == 0
    entry = _read_spool(spool_dir)
    payload = entry["payload"]
    assert entry["event_id"]
    assert payload["student_id"] == "student-1"
    assert payload["event"] == "Stop"
    assert len(payload["transcript_tail"].encode("utf-8")) <= DEFAULT_TAIL_BYTES
    assert payload["transcript_tail"].endswith("last-bytes")
    assert "transcript_full" not in payload


def test_hook_event_matches_student_core_spool_protocol(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.json"
    spool_dir = tmp_path / "spool"
    _write_config(config_path, student_id="student-1")
    monkeypatch.setenv("COPILOT_CONFIG", str(config_path))
    monkeypatch.setenv("COPILOT_SPOOL_DIR", str(spool_dir))
    _set_stdin(monkeypatch, {"hook_event_name": "UserPromptSubmit", "session_id": "s1"})

    assert main() == 0

    entry = _read_spool(spool_dir)
    from copilot.student_core.models import SpoolEntry

    restored = SpoolEntry.from_dict(entry)
    assert restored.event_id == entry["event_id"]
    assert restored.payload.session_id == "s1"


def test_spool_never_contains_local_transcript_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.json"
    spool_dir = tmp_path / "spool"
    transcript = tmp_path / "private" / "transcript.jsonl"
    transcript.parent.mkdir()
    transcript.write_text("tail", encoding="utf-8")
    _write_config(config_path, student_id="student-1")
    monkeypatch.setenv("COPILOT_CONFIG", str(config_path))
    monkeypatch.setenv("COPILOT_SPOOL_DIR", str(spool_dir))
    _set_stdin(
        monkeypatch,
        {"hook_event_name": "Stop", "session_id": "s1", "transcript_path": str(transcript)},
    )

    assert main() == 0

    raw = next(spool_dir.glob("*.json")).read_text(encoding="utf-8")
    payload = json.loads(raw)["payload"]
    assert payload["transcript_path"] == ""
    assert str(transcript) not in raw


def test_invalid_utf8_tail_stays_bounded_after_json_serialization(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.json"
    spool_dir = tmp_path / "spool"
    transcript = tmp_path / "invalid.bin"
    transcript.write_bytes((b"\xff" * (DEFAULT_TAIL_BYTES + 64)) + b"END")
    _write_config(config_path, student_id="student-1")
    monkeypatch.setenv("COPILOT_CONFIG", str(config_path))
    monkeypatch.setenv("COPILOT_SPOOL_DIR", str(spool_dir))
    _set_stdin(
        monkeypatch,
        {"hook_event_name": "Stop", "session_id": "s1", "transcript_path": str(transcript)},
    )

    assert main() == 0

    payload = json.loads(next(spool_dir.glob("*.json")).read_text(encoding="utf-8"))["payload"]
    assert len(payload["transcript_tail"].encode("utf-8")) <= DEFAULT_TAIL_BYTES
    assert payload["transcript_tail"].endswith("END")


def test_escaped_tail_stays_bounded_in_serialized_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.json"
    spool_dir = tmp_path / "spool"
    transcript = tmp_path / "escaped.jsonl"
    transcript.write_bytes((b'"\\' * (DEFAULT_TAIL_BYTES // 2 + 64)) + b"END")
    _write_config(config_path, student_id="student-1")
    monkeypatch.setenv("COPILOT_CONFIG", str(config_path))
    monkeypatch.setenv("COPILOT_SPOOL_DIR", str(spool_dir))
    _set_stdin(
        monkeypatch,
        {"hook_event_name": "Stop", "session_id": "s1", "transcript_path": str(transcript)},
    )

    assert main() == 0

    raw = next(spool_dir.glob("*.json")).read_text(encoding="utf-8")
    payload = json.loads(raw)["payload"]
    serialized_tail = json.dumps(
        payload["transcript_tail"], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    assert len(serialized_tail) <= DEFAULT_TAIL_BYTES
    assert payload["transcript_tail"].endswith("END")


def test_invalid_stdin_always_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", MagicMock(read=lambda: "not-json"))

    assert main() == 0


def test_missing_event_and_missing_student_id_degrade_without_spool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spool_dir = tmp_path / "spool"
    config_path = tmp_path / "config.json"
    _write_config(config_path, student_id="")
    monkeypatch.setenv("COPILOT_CONFIG", str(config_path))
    monkeypatch.setenv("COPILOT_SPOOL_DIR", str(spool_dir))
    _set_stdin(monkeypatch, {"session_id": "s1"})

    assert main() == 0
    assert not spool_dir.exists()


def test_spool_write_failure_always_returns_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.json"
    _write_config(config_path, student_id="student-1")
    monkeypatch.setenv("COPILOT_CONFIG", str(config_path))
    monkeypatch.setenv("COPILOT_SPOOL_DIR", str(tmp_path / "spool"))
    _set_stdin(monkeypatch, {"hook_event_name": "Stop", "session_id": "s1"})
    monkeypatch.setattr("copilot.hook._write_spool_event", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))

    assert main() == 0
