from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.contract

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_UI_ARTIFACTS = {
    "docs/mentor-ui-fixed.png",
    "docs/mentor-ui-screenshot.png",
}
PERSONAL_HOME = b"/Users/" + b"michael/"


def _tracked_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPOSITORY_ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [
        Path(raw.decode("utf-8", errors="surrogateescape"))
        for raw in result.stdout.split(b"\0")
        if raw
    ]


def test_private_ui_artifacts_are_not_tracked():
    tracked = {path.as_posix() for path in _tracked_paths()}

    assert tracked.isdisjoint(PRIVATE_UI_ARTIFACTS), (
        "private mentor UI captures must not be version controlled: "
        f"{sorted(tracked & PRIVATE_UI_ARTIFACTS)}"
    )


def test_tracked_content_has_no_personal_home_path():
    violations: list[str] = []

    for relative_path in _tracked_paths():
        file_path = REPOSITORY_ROOT / relative_path
        if not file_path.is_file():
            continue
        with file_path.open("rb") as tracked_file:
            for line_number, line in enumerate(tracked_file, start=1):
                if PERSONAL_HOME not in line:
                    continue
                violations.append(f"{relative_path.as_posix()}:{line_number}")

    assert not violations, (
        f"tracked content contains personal home path {PERSONAL_HOME.decode()}: "
        f"{violations}"
    )
