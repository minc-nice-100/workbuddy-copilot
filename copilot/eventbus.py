"""事件总线：发布/订阅解耦。

Service 层发布事件，Controller 层（WS 推送）订阅。
未来增加飞书推送、Webhook 等订阅者时，只需注册新订阅者。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Coroutine

log = logging.getLogger("copilot.eventbus")

# 订阅者类型：async 回调，接收 payload dict
Subscriber = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class EventBus:
    """简单的 async 事件总线。

    Service 层调用 await bus.publish(payload)，
    所有注册的订阅者会异步收到 payload。
    """

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, callback: Subscriber) -> None:
        """注册订阅者。"""
        self._subscribers.append(callback)
        log.info("EventBus 新增订阅者，当前 %d 个", len(self._subscribers))

    def unsubscribe(self, callback: Subscriber) -> None:
        """取消订阅。"""
        if callback in self._subscribers:
            self._subscribers.remove(callback)
            log.info("EventBus 移除订阅者，当前 %d 个", len(self._subscribers))

    async def publish(self, payload: dict[str, Any]) -> None:
        """发布事件，所有订阅者异步收到。

        某个订阅者失败不影响其他订阅者。
        """
        for sub in list(self._subscribers):
            try:
                await sub(payload)
            except Exception as e:
                log.warning("EventBus 订阅者失败: %s", e)


# 全局单例
bus = EventBus()
