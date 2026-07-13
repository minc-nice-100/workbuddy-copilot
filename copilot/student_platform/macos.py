"""macOS-owned construction helpers for local WorkBuddy data access.

This module deliberately contains no AppKit or PyObjC imports.  NSPanel
rendering remains in ``floating_native.py``; this narrow adapter only binds a
macOS installation's already-known config directory to the shared data reader.
"""
from __future__ import annotations

import asyncio
import inspect
import os
from pathlib import Path
import threading
from typing import Any, Callable

from .workbuddy import WorkBuddyDataAdapter


class MacOSWorkBuddyData(WorkBuddyDataAdapter):
    """Named macOS adapter kept separate from future Windows path logic."""


class StudentCoordinatorCommandCallback:
    """Move a non-UI mentor command onto the shared coordinator.

    ``floating_native`` owns NSPanel rendering and its main thread.  This
    callback is an injectable seam: it starts coordinator work away from that
    UI thread while leaving the mature PyObjC view code untouched.  A custom
    ``run`` function makes the handoff deterministic in tests.
    """

    def __init__(
        self,
        coordinator: Any,
        *,
        run: Callable[[Any], Any] | None = None,
    ) -> None:
        self.coordinator = coordinator
        self._run = run or self._run_in_background_thread

    def __call__(self, command: dict[str, Any]) -> bool:
        """Handle only outcomes known synchronously to be accepted.

        A coroutine's eventual false result must not suppress floating's legacy
        fallback.  Call :meth:`submit` when a caller can supply that fallback
        for asynchronous result bridging.
        """
        handler = getattr(self.coordinator, "handle_command", None)
        if not callable(handler):
            return False
        try:
            outcome = handler(command)
        except Exception:
            return False
        if inspect.isawaitable(outcome):
            close = getattr(outcome, "close", None)
            if callable(close):
                close()
            return False
        return bool(outcome)

    def submit(self, command: dict[str, Any], fallback: Callable[[dict[str, Any]], Any]) -> bool:
        """Schedule an async command and call ``fallback`` if it resolves false."""
        handler = getattr(self.coordinator, "handle_command", None)
        if not callable(handler):
            return False
        try:
            outcome = handler(command)
        except Exception:
            return False
        if not inspect.isawaitable(outcome):
            return bool(outcome)

        async def resolve() -> None:
            try:
                handled = bool(await outcome)
            except Exception:
                handled = False
            if not handled:
                fallback(command)

        self._run(resolve())
        return True

    @staticmethod
    def _run_in_background_thread(awaitable: Any) -> None:
        def runner() -> None:
            asyncio.run(awaitable)

        threading.Thread(target=runner, daemon=True).start()


def macos_workbuddy_data(
    config_dir: str | os.PathLike[str],
    *,
    database_path: str | os.PathLike[str] | None = None,
    projects_dir: str | os.PathLike[str] | None = None,
) -> MacOSWorkBuddyData:
    """Create an adapter from an explicit, caller-supplied config directory."""
    return MacOSWorkBuddyData(
        Path(config_dir), database_path=database_path, projects_dir=projects_dir
    )
