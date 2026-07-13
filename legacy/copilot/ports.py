"""端口定义（Port — 适配器模式）。

定义 4 个抽象接口，Service 层依赖端口而非具体实现。
切换 Agent 框架 / 操作系统 / 通知方式时，只需提供新的适配器实现。

端口：
  AgentSessionRepository — Agent 框架的会话数据读取
  TranscriptParser       — 对话记录解析
  FloatingWindow         — 浮动窗口 UI（平台相关）
  Notifier               — 系统通知（平台相关）

当前实现：
  WorkBuddySessionRepository — 读取 ~/.workbuddy/workbuddy.db
  WorkBuddyTranscriptParser  — 解析 WorkBuddy JSONL 格式
  MacFloatingWindow          — PyObjC NSPanel（macOS）
  MacNotifier                — osascript（macOS）

未来实现（示例）：
  CursorSessionRepository    — 读取 Cursor 的存储
  ClaudeCodeTranscriptParser — 解析 Claude Code 的格式
  WinFloatingWindow          — win32gui / PyQt6（Windows）
  PushNotifier               — APNs / FCM（移动端）
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol

from .models import Conversation, TimelineEntry


# ---- 端口 1: AgentSessionRepository ----

class AgentSessionRepository(ABC):
    """Agent 框架的会话数据读取端口。

    不同 Agent 框架（WorkBuddy / Cursor / Claude Code）实现此接口。
    Service 层通过此接口获取会话列表和标题，不关心底层是 SQLite 还是 API。
    """

    @abstractmethod
    def list_sessions(
        self, cwd: str | None = None,
        include_deleted: bool = False, limit: int = 200,
    ) -> list[dict]:
        """查询会话列表。

        Returns:
            每条 dict 含: session_id, work_dir, title, status, mode,
            created_at, last_activity_at, deleted
        """

    @abstractmethod
    def get_session_title(self, session_id: str) -> str:
        """获取会话标题（custom_title 优先）。"""

    @abstractmethod
    def get_active_session(self, work_dir: str | None = None) -> dict | None:
        """获取当前激活的对话（最新活动的）。"""


# ---- 端口 2: TranscriptParser ----

class TranscriptParser(ABC):
    """对话记录解析端口。

    不同 Agent 框架的 transcript 格式不同（JSONL / JSON / DB），
    通过此接口统一输出 TranscriptSnapshot。
    """

    @abstractmethod
    def parse(self, path: str) -> "TranscriptSnapshot":
        """解析 transcript 文件，返回统一格式。"""

    @abstractmethod
    def recent_messages(self, path: str, last_n: int = 16) -> list:
        """只取最近 N 条消息，用于喂给 LLM。"""


# ---- 端口 3: FloatingWindow ----

class FloatingWindow(ABC):
    """浮动窗口 UI 端口（平台相关）。

    macOS 用 PyObjC NSPanel，Windows 用 win32gui 或 PyQt6，
    移动端可能用系统通知替代浮动窗口。
    """

    @abstractmethod
    def show(self) -> None:
        """显示浮标。"""

    @abstractmethod
    def hide(self) -> None:
        """隐藏浮标。"""

    @abstractmethod
    def update_state(self, state: str, analysis: dict | None = None) -> None:
        """更新浮标状态。

        Args:
            state: "idle" / "active" / "alert"
            analysis: 最新分析结果（可选）
        """

    @abstractmethod
    def set_position(self, x: int, y: int) -> None:
        """设置浮标位置。"""

    @abstractmethod
    def run(self) -> None:
        """启动 UI 事件循环（阻塞）。"""


# ---- 端口 4: Notifier ----

class Notifier(ABC):
    """系统通知端口（平台相关）。

    macOS 用 osascript，Windows 用 toast 通知，
    移动端用 APNs / FCM。
    """

    @abstractmethod
    def notify(self, title: str, body: str, severity: str = "info") -> bool:
        """发送系统通知。

        Args:
            title: 通知标题
            body: 通知正文
            severity: "info" / "warn" / "error"

        Returns:
            True 表示发送成功
        """


# ---- 工厂函数 ----

def create_session_repository(config: dict) -> AgentSessionRepository:
    """根据配置创建会话仓库适配器。

    config.agent.framework 决定使用哪个实现：
    - "workbuddy" → WorkBuddySessionRepository（默认）
    - "cursor" → CursorSessionRepository（未来）
    - "claude-code" → ClaudeCodeSessionRepository（未来）
    """
    framework = config.get("agent", {}).get("framework", "workbuddy")

    if framework == "workbuddy":
        from .wb_sessions import WorkBuddySessionRepository
        return WorkBuddySessionRepository()

    raise ValueError(f"不支持的 agent 框架: {framework}（目前只支持 workbuddy）")


def create_transcript_parser(config: dict) -> TranscriptParser:
    """根据配置创建 transcript 解析器。"""
    framework = config.get("agent", {}).get("framework", "workbuddy")

    if framework == "workbuddy":
        from .transcript import WorkBuddyTranscriptParser
        return WorkBuddyTranscriptParser()

    raise ValueError(f"不支持的 agent 框架: {framework}")


def create_notifier(config: dict) -> Notifier:
    """根据操作系统创建通知器。"""
    import platform
    system = platform.system()

    if system == "Darwin":
        from .notifier_macos import MacNotifier
        return MacNotifier()
    elif system == "Windows":
        # 未来: from .notifier_windows import WinNotifier
        raise NotImplementedError("Windows 通知尚未实现")
    else:
        raise NotImplementedError(f"不支持的平台: {system}")
