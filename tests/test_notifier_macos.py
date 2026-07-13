from __future__ import annotations

from types import SimpleNamespace

from copilot.notifier_macos import MacNotifier


def test_notify_passes_title_and_body_via_osascript_argv(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("copilot.notifier_macos.subprocess.run", fake_run)

    title = '导师 "提醒"'
    body = '第一行\n第二行 "quoted"; display dialog "pwned"'
    assert MacNotifier().notify(title, body, severity="warn") is True

    args, kwargs = calls[0]
    assert args[0:2] == ["osascript", "-e"]
    script = args[2]
    assert "on run argv" in script
    assert title not in script
    assert body not in script
    assert args[3] == title
    assert args[4] == body
    assert kwargs == {"timeout": 5, "capture_output": True}
