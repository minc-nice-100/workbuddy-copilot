from __future__ import annotations

import asyncio
import json

from copilot.connections import WSRegistry
from copilot.eventbus import EventBus
from copilot.services import MessageService
from copilot.store import Store


class FakeWebSocket:
    def __init__(self):
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


def test_send_persists_before_live_push_and_waits_for_student_rest_receipt(tmp_path):
    async def scenario():
        store = Store(tmp_path / "messages.db")
        bus = EventBus()
        seen_during_publish = []
        target = FakeWebSocket()
        registry = WSRegistry(send_timeout=0.05)
        registry.register_float("student-a", target)

        async def assert_persisted_before_push(payload):
            rows = store.list_messages_since("student-a", 0)
            seen_during_publish.append((payload["message_id"], rows[0]["delivered_at"]))

        bus.subscribe(assert_persisted_before_push)
        bus.subscribe(registry.handle_event)
        service = MessageService(store, bus)

        result = await service.send("student-a", "mentor-1", "Try a smaller example")

        rows = store.list_messages_since("student-a", 0)
        assert len(rows) == 1
        assert rows[0]["message_id"] == result["message_id"]
        assert seen_during_publish == [(result["message_id"], None)]
        assert rows[0]["delivered_at"] is None
        assert result == {
            "message_id": rows[0]["message_id"],
            "id": rows[0]["id"],
            "delivered": False,
        }

        assert await service.ack(result["message_id"], "student-a") is True
        assert store.list_messages_since("student-a", 0)[0]["delivered_at"] is not None

    asyncio.run(scenario())


def test_send_offline_remains_undelivered_and_catchup_filters_since(tmp_path):
    async def scenario():
        store = Store(tmp_path / "messages.db")
        bus = EventBus()
        registry = WSRegistry(send_timeout=0.05)
        bus.subscribe(registry.handle_event)
        service = MessageService(store, bus)

        first = await service.send("student-a", "mentor-1", "First")
        second = await service.send("student-a", "mentor-1", "Second")

        rows = store.list_messages_since("student-a", 0)
        assert [row["message_id"] for row in rows] == [first["message_id"], second["message_id"]]
        assert all(row["delivered_at"] is None for row in rows)
        assert first["delivered"] is False
        assert second["delivered"] is False

        catchup = service.get_catchup("student-a", first["id"])

        assert [row["message_id"] for row in catchup] == [second["message_id"]]
        assert catchup[0]["type"] == "mentor_message"

    asyncio.run(scenario())


def test_catchup_limit_bounds_backlog_in_store_order(tmp_path):
    async def scenario():
        store = Store(tmp_path / "messages.db")
        service = MessageService(store, EventBus())

        first = await service.send("student-a", "mentor-1", "First")
        second = await service.send("student-a", "mentor-1", "Second")
        third = await service.send("student-a", "mentor-1", "Third")

        pending = service.get_catchup("student-a", 0, limit=2)

        assert [row["message_id"] for row in pending] == [
            first["message_id"],
            second["message_id"],
        ]
        assert third["message_id"] not in {row["message_id"] for row in pending}

    asyncio.run(scenario())


def test_ack_is_idempotent_and_message_ids_are_unique(tmp_path):
    async def scenario():
        store = Store(tmp_path / "messages.db")
        service = MessageService(store, EventBus())

        first = await service.send("student-a", "mentor-1", "Same text")
        second = await service.send("student-a", "mentor-1", "Same text")

        assert first["message_id"] != second["message_id"]
        assert await service.ack(first["message_id"], "student-a") is True
        delivered_once = store.list_messages_since("student-a", 0)[0]["delivered_at"]
        assert await service.ack(first["message_id"], "student-a") is True
        delivered_twice = store.list_messages_since("student-a", 0)[0]["delivered_at"]
        assert delivered_twice == delivered_once
        assert await service.ack(first["message_id"], "student-b") is False

    asyncio.run(scenario())


def test_ack_publishes_delivery_receipt_to_mentors_after_offline_catchup(tmp_path):
    async def scenario():
        store = Store(tmp_path / "messages.db")
        bus = EventBus()
        registry = WSRegistry(send_timeout=0.05)
        mentor = FakeWebSocket()
        registry.register_mentor(mentor)
        bus.subscribe(registry.handle_event)
        service = MessageService(store, bus)

        sent = await service.send("student-a", "mentor-1", "Catch up when you reconnect")
        assert sent["delivered"] is False
        assert mentor.sent == []
        assert store.list_messages_since("student-a", 0)[0]["delivered_at"] is None

        assert await service.ack(sent["message_id"], "student-a") is True

        delivered_row = store.list_messages_since("student-a", 0)[0]
        assert delivered_row["delivered_at"] is not None
        assert [json.loads(text) for text in mentor.sent] == [{
            "type": "message_delivered",
            "student_id": "student-a",
            "message_id": sent["message_id"],
            "id": sent["id"],
            "timestamp": delivered_row["delivered_at"],
        }]

    asyncio.run(scenario())


def test_ack_does_not_republish_receipt_when_message_is_already_delivered(tmp_path):
    async def scenario():
        store = Store(tmp_path / "messages.db")
        bus = EventBus()
        registry = WSRegistry(send_timeout=0.05)
        mentor = FakeWebSocket()
        registry.register_mentor(mentor)
        bus.subscribe(registry.handle_event)
        service = MessageService(store, bus)

        sent = await service.send("student-a", "mentor-1", "Already pushed live")
        store.mark_message_delivered(sent["message_id"], student_id="student-a")

        assert await service.ack(sent["message_id"], "student-a") is True
        assert mentor.sent == []

    asyncio.run(scenario())
