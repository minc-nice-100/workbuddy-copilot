from __future__ import annotations

import asyncio

from copilot.eventbus import EventBus
from copilot.services import AnalysisService
from copilot.store import Store
from copilot.transcript import TranscriptSnapshot


def test_prompt_config_can_be_updated_and_read_from_store(tmp_path):
    store = Store(tmp_path / "copilot.db")

    first = store.set_prompt_config(
        key="process_reminder",
        prompt="低效模式只在明显重复试错时轻提醒。",
        updated_by="admin",
    )
    second = store.set_prompt_config(
        key="process_reminder",
        prompt="提醒要少而准，不要频繁打断。",
        updated_by="admin",
    )

    row = store.get_prompt_config("process_reminder")
    assert first == second
    assert row is not None
    assert row["prompt"] == "提醒要少而准，不要频繁打断。"
    assert row["updated_by"] == "admin"
    assert row["updated_at"] >= row["created_at"]


def test_analysis_service_passes_stored_process_reminder_prompt_to_llm(tmp_path):
    async def scenario():
        store = Store(tmp_path / "copilot.db")
        store.set_prompt_config("process_reminder", "只在反复试错三次以上时提醒。")
        bus = EventBus()
        seen = {}

        async def fake_llm(cfg, snap, event, latest_prompt):
            seen["prompt"] = cfg["analysis"]["process_reminder_prompt"]
            return {
                "topic": "debug",
                "understanding": "medium",
                "severity": "info",
                "diagnosis": "ok",
                "suggestion": "ok",
                "is_technical": True,
                "ai_reply_summary": "",
            }

        service = AnalysisService(
            copilot_repo=store,
            llm_analyzer=fake_llm,
            config={"analysis": {}, "llm": {}},
            event_bus=bus,
        )
        report_id = store.add_report("stu-1", "sess-1", "Stop", "", "", 0, 0)
        await service.handle_stop(
            "stu-1",
            "sess-1",
            "帮我直接写完",
            "",
            report_id,
        )

        assert seen["prompt"] == "只在反复试错三次以上时提醒。"

    asyncio.run(scenario())


def test_analysis_service_uses_config_prompt_when_store_has_no_override(tmp_path):
    async def scenario():
        store = Store(tmp_path / "copilot.db")
        bus = EventBus()
        seen = {}

        async def fake_llm(cfg, snap, event, latest_prompt):
            seen["prompt"] = cfg["analysis"]["process_reminder_prompt"]
            return {
                "topic": "debug",
                "understanding": "medium",
                "severity": "info",
                "diagnosis": "ok",
                "suggestion": "ok",
                "is_technical": True,
                "ai_reply_summary": "",
            }

        service = AnalysisService(
            copilot_repo=store,
            llm_analyzer=fake_llm,
            config={
                "analysis": {
                    "process_reminder_prompt": "配置文件里的提醒提示词。",
                },
                "llm": {},
            },
            event_bus=bus,
        )
        report_id = store.add_report("stu-1", "sess-1", "Stop", "", "", 0, 0)
        await service.handle_stop("stu-1", "sess-1", "问题", "", report_id)

        assert seen["prompt"] == "配置文件里的提醒提示词。"

    asyncio.run(scenario())
