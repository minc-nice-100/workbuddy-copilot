"""Breaker fixture: every construct in this file must trip a server redline."""

from pathlib import Path

from copilot.wb_upload import upload_transcripts


def read_student_workbuddy_files(db_path: Path):
    workbuddy_home = Path.home() / (".work" + "buddy")
    local_db = workbuddy_home / ("workbuddy" + ".db")
    local_jsonl = workbuddy_home / ("pro" + "jects") / "session.jsonl"
    old_project_root = db_path.parent.parent
    rows = iter_recent_transcripts(old_project_root)
    return local_db, local_jsonl, rows, upload_transcripts
