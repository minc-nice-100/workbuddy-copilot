"""macOS 通知适配器。"""
from __future__ import annotations

import subprocess


class MacNotifier:
    """macOS 通知实现（osascript）。"""

    def notify(self, title: str, body: str, severity: str = "info") -> bool:
        """通过 osascript 发送 macOS 系统通知。"""
        # 根据严重程度选择通知声音
        sound = "Glass" if severity == "info" else "Basso" if severity == "error" else "Frog"
        script = (
            "on run argv\n"
            "    set notificationTitle to item 1 of argv\n"
            "    set notificationBody to item 2 of argv\n"
            "    set notificationSound to item 3 of argv\n"
            "    display notification notificationBody with title notificationTitle sound name notificationSound\n"
            "end run"
        )
        try:
            result = subprocess.run(
                ["osascript", "-e", script, title, body, sound],
                timeout=5, capture_output=True,
            )
            return result.returncode == 0
        except Exception:
            return False
