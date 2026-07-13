"""桌面浮动浮标 app（PyQt6）。

可拖拽的圆形浮动图标，类似豆包/Doubao 风格：
- 圆形图标，可拖拽到桌面任意位置
- 点击展开分析卡片面板
- 新分析到达时显示未读徽章 + 弹出提示动画
- 右键菜单：刷新 / 全部已读 / 退出

启动：
  python3 -m copilot.floating
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.request
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import (
    Qt,
    QPoint,
    QRect,
    QSize,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPixmap,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import websockets
import asyncio

from .config import load_config, ws_url

log = logging.getLogger("copilot.floating")


# ── macOS 跨 Space 支持 ──────────────────────────────────


def _make_window_join_all_spaces(widget) -> None:
    """让 Qt 窗口在所有 macOS Space（含全屏应用）显示。

    核心原理：macOS 全屏应用创建独立 Space，普通窗口只在主桌面显示。
    需要 3 个条件同时满足才能跨 Space:
      1. NSWindow.collectionBehavior 含 fullScreenAuxiliary
      2. window level >= kCGFloatingWindowLevel (3)
      3. 必须在 show() **之后** 设置，且可能需要多次设置（Qt 会覆盖）
    """
    if sys.platform != "darwin":
        return
    try:
        import objc
        from AppKit import (
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorStationary,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorIgnoresCycle,
            NSPanel,  # NSPanel 比 NSWindow 更适合浮动窗口
        )

        view_ptr = int(widget.winId())
        ns_view = objc.objc_object(c_void_p=view_ptr)
        ns_window = ns_view.window()
        if not ns_window:
            log.warning("NSView 无关联 NSWindow")
            return

        # === 关键: 完整的跨 Space 配置 ===

        # 1. collectionBehavior — 允许加入所有空间
        behavior = (
            NSWindowCollectionBehaviorCanJoinAllSpaces          # 加入所有 Space
            | NSWindowCollectionBehaviorFullScreenAuxiliary       # 全屏空间辅助窗口
            | NSWindowCollectionBehaviorStationary               # 不参与 Exposé
            | NSWindowCollectionBehaviorIgnoresCycle             # 不参与 Cmd+Tab 循环
        )
        ns_window.setCollectionBehavior_(behavior)

        # 2. level — 使用 floating level (3)，这是 macOS 浮动窗口的标准值
        # 太高(如 101) 可能被系统限制；3 是 Dock/菜单栏级别
        ns_window.setLevel_(3)

        # 3. style mask — 确保是非激活面板风格
        try:
            from AppKit import NSWindowStyleMaskNonActivatingPanel, NSWindowStyleMaskUtilityWindow
            current_style = ns_window.styleMask()
            # 加上 NonActivatingPanel 面板样式
            new_style = (current_style | NSWindowStyleMaskUtilityWindow)
            ns_window.setStyleMask_(new_style)
        except Exception as e:
            log.debug("styleMask 设置跳过: %s", e)

        # 4. 强制置前
        ns_window.orderFrontRegardless()

        # 5. 验证
        actual_cb = ns_window.collectionBehavior()
        actual_level = ns_window.level()
        can_join = bool(actual_cb & NSWindowCollectionBehaviorCanJoinAllSpaces)
        has_aux = bool(actual_cb & NSWindowCollectionBehaviorFullScreenAuxiliary)

        log.info(
            "跨Space设置: %s → cb=%d (allSpaces=%s, auxiliary=%s), level=%d",
            widget.__class__.__name__,
            actual_cb,
            can_join,
            has_aux,
            actual_level,
        )

    except Exception as e:
        log.warning("跨Space设置失败: %s", e)


# ── 定时重复设置（对抗 Qt 重置）───


def _schedule_cross_space_fixup(icon_widget) -> None:
    """Qt 可能在 show()/resize() 时重置 NSWindow 属性，
    所以需要定时重新设置 collectionBehavior 和 level。
    """
    from PyQt6.QtCore import QTimer

    def _fixup():
        _make_window_join_all_spaces(icon_widget)

    # show 后立即设一次
    QTimer.singleShot(100, _fixup)
    # 500ms 再设一次（Qt 可能延迟处理 resize）
    QTimer.singleShot(500, _fixup)
    # 2 秒后再确认一次
    QTimer.singleShot(2000, _fixup)

UNDERSTANDING_EMOJI = {
    "high": "\U0001f7e2",
    "medium": "\U0001f7e1",
    "low": "\U0001f7e0",
    "stuck": "\U0001f534",
    "unknown": "\u26aa",
}

ICON_SIZE = 72
PANEL_WIDTH = 380
PANEL_MAX_HEIGHT = 520
ANIMATION_MS = 250


# ── Floating circular icon widget ────────────────────────


class FloatingIcon(QWidget):
    """圆形浮动图标，可拖拽。纯 Qt 绘制，不依赖 emoji 渲染。"""

    clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(ICON_SIZE + 8, ICON_SIZE + 8)
        # 最小化 Qt 窗口标志，避免 Qt 干扰 macOS 原生窗口属性
        # 所有 "置顶"、"浮动" 行为由 NSWindow API 控制
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        # 不用透明背景，避免 macOS 上渲染空白
        self.setAutoFillBackground(True)
        from PyQt6.QtGui import QPalette
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor("#f8f9fa"))
        self.setPalette(pal)

        # 拖动状态
        self._dragging = False
        self._drag_pos: QPoint = QPoint()
        self._pos_saved: QPoint = QPoint(100, 200)

        # 状态
        self._unread_count = 0
        self._pulse_phase = 0.0
        self._state = "idle"  # idle / active / alert

        # 动画定时器（脉冲效果）
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._advance_pulse)
        self._anim_timer.start(30)

    def set_state(self, state: str = "idle", unread: int = 0) -> None:
        """idle / active / alert"""
        self._state = state
        self._unread_count = unread
        self.update()

    # ── paint ──

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        cx, cy = w // 2, h // 2
        r = ICON_SIZE // 2

        # 整个 widget 填充浅灰底（确保窗口不透明）
        painter.fillRect(0, 0, w, h, QColor("#f8f9fa"))

        # 外圈光晕（有新内容时脉冲）
        if self._unread_count > 0 or self._state == "alert":
            alpha = int(80 + 60 * (0.5 + 0.5 * (self._pulse_phase)))
            glow_color = QColor("#ff6b6b") if self._state == "alert" else QColor("#4dabf7")
            glow_color.setAlpha(alpha)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(glow_color))
            painter.drawEllipse(cx - r - 8, cy - r - 8, (r + 8) * 2, (r + 8) * 2)

        # 主圆形 — 根据 state 用不同颜色
        if self._state == "alert":
            main_color = QColor("#ff6b6b")
            main_dark = QColor("#e85555")
            letter = "!"
            letter_color = QColor("#ffffff")
        elif self._state == "active" and self._unread_count > 0:
            main_color = QColor("#4dabf7")
            main_dark = QColor("#3a91d8")
            letter = "C"
            letter_color = QColor("#ffffff")
        else:
            main_color = QColor("#4263eb")
            main_dark = QColor("#364fc7")
            letter = "C"
            letter_color = QColor("#ffffff")

        grad = QRadialGradient(cx - 6, cy - 6, r * 1.5)
        grad.setColorAt(0, main_color.lighter(115))
        grad.setColorAt(1, main_dark)
        painter.setPen(QPen(QColor("#ff3333"), 3))  # 醒目红色边框
        painter.setBrush(QBrush(grad))
        painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        # 中间字母（用 Helvetica 保证渲染）
        font = QFont("Helvetica", 30, QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(letter_color)
        text_rect = QRect(cx - r, cy - r - 2, r * 2, r * 2)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, letter)

        # 未读数字角标
        if self._unread_count > 0:
            badge_r = 13
            badge_x = cx + r - 4
            badge_y = cy - r
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor("#ff4757")))
            painter.drawEllipse(
                badge_x - badge_r, badge_y - badge_r, badge_r * 2, badge_r * 2
            )
            font_sm = QFont("Helvetica", 10, QFont.Weight.Bold)
            painter.setFont(font_sm)
            painter.setPen(QColor("#ffffff"))
            text = str(self._unread_count) if self._unread_count <= 99 else "99+"
            painter.drawText(
                QRect(badge_x - badge_r, badge_y - badge_r, badge_r * 2, badge_r * 2),
                Qt.AlignmentFlag.AlignCenter,
                text,
            )

        painter.end()

    def _advance_pulse(self) -> None:
        t = time.time()
        self._pulse_phase = (t % 2.0) / 2.0  # 0..1 循环
        self.update()

    # ── mouse events ──

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            new_pos = event.globalPosition().toPoint() - self._drag_pos
            self.move(new_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._dragging:
                self._dragging = False
                # 判断是否是点击（移动距离小）
                moved = (
                    event.globalPosition().toPoint()
                    - self._drag_pos
                    - self.pos().toPoint()
                ).manhattanLength()
                if moved < 5:
                    self.clicked.emit()
        super().mouseReleaseEvent(event)


# ── Analysis card widget ──────────────────────────────


class AnalysisCard(QWidget):
    """单条分析卡片。"""

    def __init__(self, data: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.data = data
        self.setup_ui()

    def setup_ui(self) -> None:
        raw = self.data.get("raw", "{}")
        try:
            r = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            r = {}

        topic = r.get("topic", "?")[:40]
        understanding = r.get("understanding", "?")
        progress = r.get("progress", "")[:80]
        guidance = r.get("guidance", "")[:80]
        alert = r.get("alert", "")
        event = self.data.get("event", "")
        prompt = self.data.get("prompt", "")[:60]
        ts_str = time.strftime("%H:%M:%S", time.localtime(self.data.get("created_at", 0)))

        emoji = UNDERSTANDING_EMOJI.get(understanding, "\u26aa")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        # 左侧状态圆点
        status_label = QLabel(emoji)
        status_label.setFont(QFont("Apple Color Emoji", 18))
        status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_label.setMinimumWidth(28)

        # 右侧内容
        content = QWidget()
        clayout = QVBoxLayout(content)
        clayout.setContentsMargins(0, 0, 0, 0)
        clayout.setSpacing(3)

        header_row = QWidget()
        hlayout = QHBoxLayout(header_row)
        hlayout.setContentsMargins(0, 0, 0, 0)
        hlayout.setSpacing(6)
        title_lbl = QLabel(topic)
        title_lbl.setFont(QFont("SF Pro Text", 13, QFont.Weight.DemiBold))
        title_lbl.setStyleSheet(f"color: {'#c0392b' if alert else '#185fa5'};")
        event_tag = QLabel(f"[{event}]")
        event_tag.setFont(QFont("SF Pro Text", 10))
        event_tag.setStyleSheet("color: #888; background: #f0f0f0; padding: 2px 6px; border-radius: 4px;")
        hlayout.addWidget(title_lbl)
        hlayout.addStretch()
        hlayout.addWidget(event_tag)
        hlayout.setSizeConstraint(QLayout.SizeConstraint.SetDefaultConstraint) if 'QLayout' in dir() else None
        clayout.addWidget(header_row)

        prog_lbl = QLabel(progress)
        prog_lbl.setFont(QFont("SF Pro Text", 12))
        prog_lbl.setStyleSheet("color: #555;")
        prog_lbl.setWordWrap(True)
        clayout.addWidget(prog_lbl)

        guide_lbl = QLabel(guidance)
        guide_lbl.setFont(QFont("SF Pro Text", 12))
        guide_lbl.setStyleSheet(
            f"color: #fff; background: {('#ffe0e0' if alert else '#e1f5ee')}; "
            f"padding: 6px 10px; border-radius: 6px;"
        )
        guide_lbl.setWordWrap(True)
        guide_lbl.setTextFormat(Qt.TextFormat.PlainText)
        clayout.addWidget(guide_lbl)

        ts_lbl = QLabel(ts_str)
        ts_lbl.setFont(QFont("SF Pro Text", 10))
        ts_lbl.setStyleSheet("color: #aaa;")
        clayout.addWidget(ts_lbl)

        clayout.addStretch()
        content.setLayout(clayout)
        content.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

        layout.addWidget(status_label)
        layout.addWidget(content, stretch=1)
        self.setLayout(layout)

        # 卡片整体样式
        self.setStyleSheet("""
            AnalysisCard {
                background: #fff;
                border: 1px solid #e5e9ef;
                border-radius: 10px;
                margin-bottom: 6px;
            }
        """)


# ── Panel dialog (pops up when clicking the icon) ─────────


class AnalysisPanel(QDialog):
    """点击浮动图标后弹出的分析卡片面板。"""

    def __init__(
        self,
        service_url: str,
        student_name: str = "学员 1",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self._service_url = service_url
        self._student_name = student_name
        self._items: List[dict] = []
        self.setup_ui()
        self.refresh_data()

    def setup_ui(self) -> None:
        self.setWindowTitle("Copilot 学习分析")
        self.setModal(False)
        self.resize(PANEL_WIDTH, PANEL_MAX_HEIGHT)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        # 标题栏
        header = QWidget()
        hdr_layout = QHBoxLayout(header)
        hdr_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel(f"\U0001f393 Copilot — {self._student_name}")
        title.setFont(QFont("SF Pro Display", 15, QFont.Weight.Bold))
        title.setStyleSheet("color: #185fa5;")
        hdr_layout.addWidget(title)
        hdr_layout.addStretch()
        close_btn = QLabel("\u2715")
        close_btn.setFont(QFont("SF Pro Text", 18))
        close_btn.setStyleSheet("color: #999; padding: 4px 8px;")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        hdr_layout.addWidget(close_btn)
        header.setLayout(hdr_layout)
        outer.addWidget(header)

        # 分割线
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background: #e5e9ef;")
        outer.addWidget(line)

        # 卡片列表（可滚动）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background: transparent;")
        self._card_container = QWidget()
        self._card_layout = QVBoxLayout(self._card_container)
        self._card_layout.setContentsMargins(0, 0, 0, 0)
        self._card_layout.setSpacing(8)
        self._card_layout.addStretch()
        self._card_container.setLayout(self._card_layout)
        scroll.setWidget(self._card_container)
        outer.addWidget(scroll, stretch=1)

        # 底部操作栏
        footer = QWidget()
        ftr_layout = QHBoxLayout(footer)
        ftr_layout.setContentsMargins(0, 0, 0, 0)
        refresh_btn = QPushButton("\U0001f504 刷新")
        read_all_btn = QPushButton("\u2705 全部已读")
        for btn in [refresh_btn, read_all_btn]:
            btn.setFont(QFont("SF Pro Text", 12))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(32)
            btn.setStyleSheet("""
                QPushButton {
                    color: #555; background: #f0f4f8;
                    border: 1px solid #dde3ea; border-radius: 8px;
                    padding: 0 16px;
                }
                QPushButton:hover { background: #e8eef5; border-color: #c0c8d0; }
            """)
        refresh_btn.clicked.connect(self.refresh_data)
        read_all_btn.clicked.connect(self.mark_read)
        ftr_layout.addWidget(refresh_btn)
        ftr_layout.addStretch()
        ftr_layout.addWidget(read_all_btn)
        footer.setLayout(ftr_layout)
        outer.addWidget(footer)

        self.setLayout(outer)

        # close 事件
        close_btn.mousePressEvent = lambda e: self.hide()

    def refresh_data(self) -> None:
        try:
            url = f"{self._service_url}/recent?limit=20"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            items = data.get("items", [])
            self._items = items
            self._rebuild_cards()
        except Exception as e:
            log.warning("拉取数据失败: %s", e)

    def _rebuild_cards(self) -> None:
        # 清除旧卡片（保留 stretch）
        while self._card_layout.count() > 1:
            w = self._card_layout.takeAt(0)
            if w and w.widget():
                w.widget().deleteLater()

        for item in reversed(self._items):
            card = AnalysisCard(item)
            self._card_layout.insertWidget(0, card)

        if not self._items:
            empty = QLabel("(暂无分析记录，在 WorkBuddy 中对话试试)")
            empty.setFont(QFont("SF Pro Text", 13))
            empty.setStyleSheet("color: #aaa; padding: 20px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._card_layout.insertWidget(0, empty)

    def mark_read(self) -> None:
        self._items.clear()
        self._rebuild_cards()


# ── Main floating app controller ───────────────────────────


class CopilotFloatApp:
    """浮动浮标主控制器：管理图标 + 面板 + WS 连接。"""

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        mb = cfg.get("menubar", {})
        self._notify_on_alert = mb.get("notification_on_alert", True)

        # Qt App
        qapp = QApplication.instance()
        if not qapp:
            qapp = QApplication([])

        # 浮动图标
        log.info("创建浮动图标...")
        self.icon_widget = FloatingIcon()
        self.icon_widget.clicked.connect(self._toggle_panel)

        # 分析面板
        svc = cfg["service"]
        base_url = f"http://{svc['host']}:{svc['port']}"
        self.panel = AnalysisPanel(base_url, cfg.get("student_name", ""))
        self.panel.hide()

        # 初始位置：屏幕正中间偏上（最显眼，便于首次发现）
        screen = qapp.primaryScreen().availableGeometry()
        init_x = screen.center().x() - (ICON_SIZE + 8) // 2
        init_y = screen.top() + screen.height() // 4
        log.info("屏幕可用区域: %s, 图标初始位置: (%d, %d)", screen, init_x, init_y)
        self.icon_widget.move(QPoint(init_x, init_y))

        # WS 客户端线程
        self._ws_thread: Any = None

        # 定时拉取最新（WS 断线时的降级）
        self._poll_timer = QTimer()
        self._poll_timer.timeout.connect(self._poll_recent)
        self._poll_timer.start(15000)

        # 启动 WS
        self._start_ws_loop()

        # 显示图标（强制置顶 + 激活）
        self.icon_widget.show()
        self.icon_widget.raise_()

        # macOS: 让窗口跨所有 Space 显示（含全屏应用空间）+ 定时重复设置防 Qt 重置
        _schedule_cross_space_fixup(self.icon_widget)

        # 延迟再激活一次（macOS 有时需要延迟才能正确置顶）
        def _delayed_raise():
            self.icon_widget.raise_()
            self.icon_widget.activateWindow()
            # 再次确保跨 Space 属性
            _make_window_join_all_spaces(self.icon_widget)
            log.info("延迟 raise 完成, pos=%s", self.icon_widget.pos())

        QTimer.singleShot(500, _delayed_raise)

    def _toggle_panel(self) -> None:
        if self.panel.isVisible():
            self.panel.hide()
        else:
            pos = self.icon_widget.mapToGlobal(QPoint(0, ICON_SIZE + 8))
            self.panel.move(pos)
            self.panel.show()
            self.panel.raise_()
            self.panel.activateWindow()
            _make_window_join_all_spaces(self.panel)

    def _start_ws_loop(self) -> None:
        import threading

        def _run_loop():
            asyncio.run(self._ws_async_loop())

        self._ws_thread = threading.Thread(target=_run_loop, daemon=True)
        self._ws_thread.start()

    async def _ws_async_loop(self) -> None:
        ws_addr = ws_url(self.cfg)
        log.info("WS 连接中 %s", ws_addr)
        backoff = 1
        while True:
            try:
                async with websockets.connect(ws_addr, ping_interval=20) as ws:
                    log.info("WS 已连接")
                    backoff = 1
                    async for raw in ws:
                        self._handle_ws_message(raw)
            except Exception as e:
                log.warning("WS 断开: %s (%ds 后重连)", e, backoff)
                await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def _handle_ws_message(self, raw) -> None:
        try:
            data = json.loads(raw) if isinstance(raw, (str, bytes)) else {}
        except Exception:
            return
        if data.get("type") != "analysis":
            return

        result = data.get("result", {})
        is_alert = bool(result.get("alert")) or result.get("understanding") in ("low", "stuck")

        # 更新图标状态
        if is_alert:
            self.icon_widget.set_state("alert")
        elif result:
            current_unread = self.icon_widget._unread_count + 1
            self.icon_widget.set_state("active", current_unread)

        # 如果面板开着就刷新
        if self.panel.isVisible():
            self.panel.refresh_data()

        # macOS 通知（仅 alert 时）
        if is_alert and self._notify_on_alert:
            self._show_notification(result)

    def _show_notification(self, result: dict) -> None:
        try:
            from subprocess import run as _run

            title = "Copilot 学习提醒"
            subtitle = result.get("topic", "")
            message = result.get("alert") or result.get("guidance", "")

            # 使用 osascript 发送 macOS 通知
            script = f'''
            display notification "{message}" with title "{title}" subtitle "{subtitle}" sound name "Glass"
            '''
            _run(["osascript", "-e", script], timeout=5, capture_output=True)
        except Exception as e:
            log.debug("通知发送失败: %s", e)

    def _poll_recent(self) -> None:
        if not self.panel.isVisible():
            return
        self.panel.refresh_data()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    log.info("=== Copilot 浮动图标启动 ===")
    cfg = load_config()
    log.info("配置加载完成: student=%s", cfg.get("student_name", "?"))

    # 确保 QApplication 存在
    qapp = QApplication.instance()
    if qapp is None:
        qapp = QApplication([])
        log.info("创建 QApplication")

    app = CopilotFloatApp(cfg)
    log.info("进入 Qt 事件循环...")
    qapp.exec()


if __name__ == "__main__":
    main()
