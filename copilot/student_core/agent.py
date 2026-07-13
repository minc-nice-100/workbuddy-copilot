"""Headless resident runtime for the platform-neutral Student Core."""
from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Mapping
from typing import Any, Callable

from .coordinator import StudentCoordinator

log = logging.getLogger("copilot.student_core.agent")


async def _default_sleeper(delay: float) -> None:
    # Keep asyncio lazy so importing Student Core remains platform-neutral.
    import asyncio

    await asyncio.sleep(delay)


class StudentAgent:
    """Run durable spool delivery and one long-lived student WebSocket.

    The agent contains no WorkBuddy/UI imports.  A platform adapter can supply
    an uploader to the coordinator, while this runtime keeps HTTP delivery and
    WebSocket reception alive across macOS and Windows.
    """

    def __init__(
        self,
        coordinator: StudentCoordinator,
        *,
        sleeper: Callable[[float], Any] | None = None,
        interval: float = 1.0,
        stop_timeout: float = 1.0,
    ) -> None:
        if interval < 0:
            raise ValueError("interval must be non-negative")
        if stop_timeout <= 0:
            raise ValueError("stop_timeout must be positive")
        self.coordinator = coordinator
        self._sleeper = sleeper or _default_sleeper
        self.interval = float(interval)
        self.stop_timeout = float(stop_timeout)
        self._stopping = False
        self._task: Any | None = None

    @property
    def stopped(self) -> bool:
        return self._stopping

    async def one_cycle(self) -> int:
        """Flush spool and retry any persisted mentor-message receipts."""
        try:
            accepted = await self.coordinator.flush_spool_once()
        except Exception as exc:
            # Filesystem/spool failures must not tear down the independent WS
            # loop; the next interval can retry once the local disk recovers.
            log.warning("student spool cycle failed type=%s", type(exc).__name__)
            accepted = 0
        await self._pull_pending_messages()
        return accepted

    async def _pull_pending_messages(self) -> int:
        pull_method = getattr(self.coordinator, "pull_pending_messages", None)
        if not callable(pull_method):
            return 0
        try:
            result = pull_method()
            resolved = await result if inspect.isawaitable(result) else result
            return int(resolved) if isinstance(resolved, int) else 0
        except Exception as exc:
            log.warning("student message receipt recovery failed type=%s", type(exc).__name__)
            return 0

    async def run(self) -> None:
        """Run spool and persistent WebSocket loops until stopped or cancelled."""
        import asyncio

        if self._stopping:
            return
        spool_task = asyncio.create_task(self._spool_loop())
        ws_task = asyncio.create_task(self._ws_loop())
        try:
            await asyncio.gather(spool_task, ws_task)
        finally:
            self._stopping = True
            for task in (spool_task, ws_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(spool_task, ws_task, return_exceptions=True)

    async def _spool_loop(self) -> None:
        while not self._stopping:
            await self.one_cycle()
            if self._stopping:
                return
            result = self._sleeper(self.interval)
            if inspect.isawaitable(result):
                await result

    async def _ws_loop(self) -> None:
        """Receive server frames on one connection; reconnect with core backoff."""
        import asyncio

        while not self._stopping:
            try:
                transport = getattr(self.coordinator, "transport", None)
                connector = getattr(transport, "open_ws", None)
                if connector is None:
                    # Unit-only coordinators can exercise the spool loop
                    # without inventing a platform/network implementation.
                    return
                async with connector() as socket:
                    self.coordinator.reset_reconnect_backoff()
                    await self._pull_pending_messages()
                    while not self._stopping:
                        raw = await socket.recv()
                        payload = self._decode_event(raw)
                        if payload is not None:
                            await self.coordinator.handle_event(payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("student WS disconnected type=%s", type(exc).__name__)
                if not self._stopping:
                    await self.coordinator.reconnect_once(lambda: False)

    @staticmethod
    def _decode_event(raw: Any) -> Mapping[str, Any] | None:
        if not isinstance(raw, (str, bytes, bytearray)):
            return None
        try:
            text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
            payload = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, Mapping) else None

    async def _background_run(self) -> None:
        import asyncio

        try:
            await self.run()
        except asyncio.CancelledError:
            # ``stop`` deliberately cancels blocked socket/sleeper work. The
            # public task resolves normally once cleanup is complete.
            return

    def start(self) -> Any:
        """Schedule the loops and return a task; calling twice is idempotent."""
        if self._task is None or self._task.done():
            self._stopping = False
            import asyncio

            self._task = asyncio.create_task(self._background_run())
        return self._task

    async def stop(self) -> None:
        """Cancel blocked I/O and wait only for the bounded cleanup interval."""
        import asyncio

        self._stopping = True
        task = self._task
        if task is None or task is asyncio.current_task() or task.done():
            return
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=self.stop_timeout)
        except asyncio.TimeoutError:
            log.warning("student agent stop timed out")
        except asyncio.CancelledError:
            return

    async def __aenter__(self) -> "StudentAgent":
        self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.stop()
