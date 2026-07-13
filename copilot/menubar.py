"""macOS menu bar 浮标 app（基于 rumps）。

常驻菜单栏小图标，下拉显示最近分析卡片，新分析通过 WebSocket 实时推送进来。
有 alert 时图标变 ⚠️ 并弹 macOS 通知。

启动：
  python3 -m copilot.menubar
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

import rumps

from .config import load_config

log = logging.getLogger("copilot.menubar")

UNDERSTANDING_EMOJI = {
    "high": "🟢",
    "medium": "🟡",
    "low": "🟠",
    "stuck": "🔴",
    "unknown": "⚪",
}


class CopilotBarApp(rumps.App):
    def __init__(self, cfg: dict):
        self.cfg = cfg
        mb = cfg.get("menubar", {})
        self.icon_idle = mb.get("icon_idle", "🎓")
        self.icon_active = mb.get("icon_active", "✨")
        self.icon_alert = mb.get("icon_alert", "⚠️")
        self.max_items = mb.get("max_menu_items", 12)
        self.notify_on_alert = mb.get("notification_on_alert", True)

        super().__init__(name="Copilot", title=self.icon_idle, quit_button="退出")

        self.items: list[dict[str, Any]] = []
        self.last_seen_ts: float = 0.0
        self.unread_alerts: int = 0

        # 启动 WS 客户端线程
        self._ws_thread = threading.Thread(target=self._run_ws_loop, daemon=True)
        self._ws_thread.start()

        # 拉取历史
        self._fetch_recent()

    # ---------- UI 构建 ----------

    def build_menu(self):
        self.menu.clear()
        if not self.items:
            self.menu.add(rumps.MenuItem("（暂无分析，开始和 WorkBuddy 对话试试）", callback=None))
            self.menu.add(rumps.separator)
            self.menu.add(rumps.MenuItem(f"学生: {self.cfg.get('student_name', self.cfg.get('student_id', '?'))}", callback=None))
            self.menu.add(rumps.MenuItem("刷新", callback=self.refresh))
            return

        for i, it in enumerate(self.items[: self.max_items]):
            title = self._format_item_title(it)
            mi = rumps.MenuItem(title, callback=self._on_item_click)
            mi._copilot_data = it  # type: ignore
            self.menu.add(mi)

        if self.unread_alerts > 0:
            self.menu.insert(0, rumps.MenuItem(f"🔔 {self.unread_alerts} 条新告警", callback=None))
            self.menu.insert(1, rumps.separator)

        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("全部标为已读", callback=self.mark_read))
        self.menu.add(rumps.MenuItem("刷新", callback=self.refresh))

    def _format_item_title(self, it: dict) -> str:
        result = it.get("raw", "")
        try:
            r = json.loads(result) if isinstance(result, str) else result
        except json.JSONDecodeError:
            r = {}
        emoji = UNDERSTANDING_EMOJI.get(r.get("understanding", "unknown"), "⚪")
        topic = r.get("topic", "?")[:20]
        guidance = r.get("guidance", "")[:40]
        ts = time.strftime("%H:%M", time.localtime(it.get("created_at", 0)))
        alert_tag = " ⚠️" if r.get("alert") else ""
        return f"{emoji} {ts} {topic}{alert_tag} — {guidance}"

    # ---------- 回调 ----------

    def refresh(self, _sender=None):
        self._fetch_recent()
        self.build_menu()

    def mark_read(self, _sender=None):
        self.unread_alerts = 0
        self.title = self.icon_idle
        self.build_menu()

    def _on_item_click(self, sender):
        data = getattr(sender, "_copilot_data", None)
        if not data:
            return
        try:
            r = json.loads(data.get("raw", "{}")) if isinstance(data.get("raw"), str) else data.get("raw", {})
        except json.JSONDecodeError:
            r = {}
        lines = [
            f"主题: {r.get('topic', '')}",
            f"理解度: {r.get('understanding', '?')}",
            f"走偏: {'是' if r.get('off_topic') else '否'}",
            f"卡点: {r.get('stuck_at', '—')}",
            f"进展: {r.get('progress', '')}",
            "",
            f"引导: {r.get('guidance', '')}",
        ]
        if r.get("alert"):
            lines.append("")
            lines.append(f"⚠️ 告警: {r['alert']}")
        rumps.alert(title="学习分析详情", message="\n".join(lines))

    # ---------- 数据拉取 ----------

    def _fetch_recent(self):
        svc = self.cfg["service"]
        url = f"http://{svc['host']}:{svc['port']}/recent?limit={self.max_items}"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            self.items = data.get("items", [])
            if self.items:
                latest_ts = self.items[0].get("created_at", 0)
                if latest_ts > self.last_seen_ts:
                    self.last_seen_ts = latest_ts
        except Exception as e:
            log.warning("拉取最近分析失败: %s", e)

    # ---------- WebSocket 实时推送 ----------

    def _run_ws_loop(self):
        """在独立线程里跑 asyncio WS 客户端，断线自动重连。"""
        asyncio.run(self._ws_async_loop())

    async def _ws_async_loop(self):
        import websockets

        svc = self.cfg["service"]
        ws_url = f"ws://{svc['host']}:{svc['port']}/ws"
        while True:
            try:
                async with websockets.connect(ws_url, ping_interval=20) as ws:
                    log.info("WS 已连接 %s", ws_url)
                    async for raw in ws:
                        self._handle_ws_message(raw)
            except Exception as e:
                log.warning("WS 断开: %s，3 秒后重连", e)
                await asyncio.sleep(3)

    def _handle_ws_message(self, raw: str | bytes):
        try:
            data = json.loads(raw) if isinstance(raw, (str, bytes)) else {}
        except json.JSONDecodeError:
            return
        if data.get("type") != "analysis":
            return

        result = data.get("result", {})
        item = {
            "raw": json.dumps(result, ensure_ascii=False),
            "created_at": data.get("timestamp", time.time()),
            "event": data.get("event"),
            "prompt": data.get("prompt"),
        }
        self.items.insert(0, item)
        self.items = self.items[: self.max_items]

        # alert 处理
        if result.get("alert") or result.get("understanding") in ("low", "stuck"):
            self.unread_alerts += 1
            self.title = f"{self.icon_alert}{'·' + str(self.unread_alerts) if self.unread_alerts else ''}"
            if self.notify_on_alert:
                rumps.notification(
                    title="Copilot 学习提醒",
                    subtitle=result.get("topic", ""),
                    message=result.get("alert") or result.get("guidance", ""),
                )
        else:
            self.title = self.icon_active

        self.build_menu()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    cfg = load_config()
    app = CopilotBarApp(cfg)
    app.run()


if __name__ == "__main__":
    main()
