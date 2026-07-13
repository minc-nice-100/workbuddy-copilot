"""Textual contracts for the PowerShell W0 probe and cautious installer."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = [pytest.mark.contract, pytest.mark.windows, pytest.mark.critical]


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_windows_probe_is_read_only_and_outputs_only_redacted_metadata() -> None:
    script = (PROJECT_ROOT / "probe_windows_workbuddy.ps1").read_text(encoding="utf-8")

    assert "function Redact" in script
    assert "WORKBUDDY_CONFIG_DIR" in script
    assert "$env:ProgramData" in script
    assert "WorkBuddy\\users" in script
    assert "ConvertTo-Json" in script
    assert "Get-Process WorkBuddy" in script
    assert "Get-ScheduledTask" in script
    assert "top_keys" in script
    assert "hook_events" in script
    assert "function Cwd-Shape" in script
    assert "cwd_shape" in script
    assert "cwd_redacted" not in script
    assert "Redact ([string]$line.cwd)" not in script
    assert r"$Value -split '[\\/]'" in script
    assert r"$Value.Contains('\')" in script
    assert r"$Value -match '\s'" in script
    assert "if ($env:SystemDrive) {" in script
    assert "transcript_content" not in script
    assert "Set-Content" not in script
    assert "Add-Content" not in script
    assert "Remove-Item" not in script
    assert "Invoke-WebRequest" not in script
    assert "Invoke-RestMethod" not in script


def test_windows_installer_uses_explicit_variables_and_atomic_settings_backup() -> None:
    script = (PROJECT_ROOT / "install_windows.ps1").read_text(encoding="utf-8")

    for required in (
        "ProjectRoot",
        "ConfigDir",
        "StudentId",
        "GitBashHookCommand",
            "Start-Process",
        "requirements-windows.txt",
        "-m venv",
            "Move-Item -LiteralPath $temporaryBackup -Destination $backupPath",
            "if ($LASTEXITCODE -ne 0)",
            "COPILOT_SPOOL_DIR",
        "COPILOT_STUDENT_ID",
    ):
        assert required in script
    assert "LOCALAPPDATA\\Programs\\WorkBuddy" not in script
    assert "python3" not in script
    assert "C:\\Users\\" not in script


def test_windows_installer_refuses_to_construct_an_unverified_git_bash_command() -> None:
    script = (PROJECT_ROOT / "install_windows.ps1").read_text(encoding="utf-8")

    assert "GitBashHookCommand" in script
    assert "requires W0-verified Git Bash hook command" in script
    assert "register_hook.py" in script
    assert "--hook-command" not in script


def test_register_hook_accepts_an_explicit_windows_config_and_verified_command(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "confirmed-config-dir"
    config_dir.mkdir()
    command = "COPILOT_STUDENT_ID=student-9 /verified/python /verified/hook.py || true"
    env = os.environ.copy()
    env.update(
        {
            "WORKBUDDY_CONFIG_DIR": str(config_dir),
            "COPILOT_HOOK_COMMAND": command,
            "COPILOT_STUDENT_ID": "student-9",
            "COPILOT_SPOOL_DIR": str(tmp_path / "spool"),
        }
    )

    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "register_hook.py")],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    settings = json.loads((config_dir / "settings.json").read_text(encoding="utf-8"))
    for event in ("UserPromptSubmit", "Stop"):
        command_entry = settings["hooks"][event][0]["hooks"][0]
        assert command_entry["command"] == command
        assert command_entry["timeout"] == 2
