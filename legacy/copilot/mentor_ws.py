# Archived legacy mentor WebSocket pool.
# Historical source: 26ce25c:copilot/mentor/ws.py.
# Retained for reference only; never import this module from server runtime.

"""导师侧 WebSocket 客户端池与连接处理。

与浮标侧 copilot.service.ws_clients 隔离的独立客户端池，
导师观察台前端连接 /ws/mentor 进入此池。
"""
from __future__ import annotations

import logging

from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger("copilot.mentor.ws")

# 独立客户端池，与浮标 ws_clients 隔离
mentor_ws_clients: list[WebSocket] = []


async def mentor_ws_endpoint(ws: WebSocket) -> None:
    """导师 WS 连接：accept 后加入独立池，被动接收维持心跳。"""
    await ws.accept()
    mentor_ws_clients.append(ws)
    log.info("导师 WS 客户端连入，当前 %d 个", len(mentor_ws_clients))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        if ws in mentor_ws_clients:
            mentor_ws_clients.remove(ws)
        log.info("导师 WS 客户端断开，当前 %d 个", len(mentor_ws_clients))
    except Exception as e:
        log.warning("导师 WS 异常: %s", e)
        if ws in mentor_ws_clients:
            mentor_ws_clients.remove(ws)
