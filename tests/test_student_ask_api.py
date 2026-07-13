from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from copilot import service as service_module
from copilot.app_context import AppContext
from copilot.connections import WSRegistry
from copilot.eventbus import EventBus
from copilot.service import create_app
from copilot.services import AnalysisService, MessageService
from copilot.store import Store


async def _unused_llm(config, snap, event, latest_prompt):
    raise AssertionError("analysis LLM is not part of student ask API")


def _line(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


def _build_app(tmp_path, *, llm_config: dict | None = None):
    store = Store(tmp_path / "copilot.db")
    bus = EventBus()
    registry = WSRegistry(send_timeout=0.05)
    bus.subscribe(registry.handle_event)
    events: list[dict] = []

    async def capture_event(payload: dict):
        events.append(payload)

    bus.subscribe(capture_event)
    config = {
        "student_id": "server",
        "service": {"host": "127.0.0.1", "port": 8765},
        "auth": {"token": "secret"},
        "store": {"db_path": str(tmp_path / "copilot.db")},
        "llm": llm_config or {"enable_llm": True, "timeout": 5},
    }
    context = AppContext(
        config=config,
        store=store,
        session_store=store.sessions,
        message_store=store.messages,
        upload_store=store.uploads,
        analysis_svc=AnalysisService(store, _unused_llm, config, bus),
        message_svc=MessageService(store, bus),
        bus=bus,
        ws_registry=registry,
    )
    return create_app(context), store, events


def test_student_ask_uses_llm_context_persists_and_publishes_event(tmp_path, monkeypatch):
    captured: dict = {}

    async def fake_answer_question(config, question, context_messages):
        captured["question"] = question
        captured["context_messages"] = context_messages
        return "固定技术助教答案"

    monkeypatch.setattr(service_module, "llm_answer_question", fake_answer_question, raising=False)
    app, store, events = _build_app(tmp_path)
    store.upsert_student("stu-1", "Alice")
    store.upsert_session("sess-1", "stu-1", "/work/alice", "循环调试")
    store.add_raw_transcript(
        "sess-1",
        "stu-1",
        _line({
            "type": "message",
            "role": "user",
            "content": "我的 for 循环最后一个元素没处理到",
            "sessionId": "sess-1",
        })
        + _line({
            "type": "message",
            "role": "assistant",
            "content": "检查 range 的结束边界是否少了 1。",
            "sessionId": "sess-1",
        }),
    )

    with TestClient(app) as client:
        resp = client.post(
            "/api/student/ask",
            json={
                "student_id": "stu-1",
                "session_id": "sess-1",
                "question": "我应该怎么验证边界？",
            },
            headers={"Authorization": "Bearer secret"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ask_id"] > 0
    assert body["answer"] == "固定技术助教答案"
    assert captured["question"] == "我应该怎么验证边界？"
    assert any("for 循环" in msg["content"] for msg in captured["context_messages"])

    asks = store.list_student_asks("stu-1", "sess-1")
    assert len(asks) == 1
    assert asks[0]["question"] == "我应该怎么验证边界？"
    assert asks[0]["answer"] == "固定技术助教答案"
    assert any(event.get("type") == "student_ask" for event in events)


def test_student_ask_llm_disabled_falls_back_and_still_persists(tmp_path):
    app, store, _events = _build_app(tmp_path, llm_config={"enable_llm": False})

    with TestClient(app) as client:
        resp = client.post(
            "/api/student/ask",
            json={"student_id": "stu-1", "question": "没有 LLM 会怎样？"},
            headers={"X-Copilot-Token": "secret"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ask_id"] > 0
    assert "LLM" in body["answer"]
    assert store.list_student_asks("stu-1")[0]["answer"] == body["answer"]


def test_student_ask_rejects_blank_question(tmp_path):
    app, _store, _events = _build_app(tmp_path)

    with TestClient(app) as client:
        resp = client.post(
            "/api/student/ask",
            json={"student_id": "stu-1", "question": "   "},
            headers={"Authorization": "Bearer secret"},
        )

    assert resp.status_code == 400
