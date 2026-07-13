from __future__ import annotations

import asyncio
import json

import pytest

from copilot.eventbus import EventBus
from copilot.llm import AnalysisOutcome
from copilot.services import AnalysisService
from copilot.store import Store


def _line(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


def test_stop_tail_is_analysis_input_not_raw_transcript(tmp_path):
    async def scenario():
        store = Store(tmp_path / "service.db")
        bus = EventBus()
        llm_inputs: list[str] = []
        transcript_tail = _line({
            "type": "message",
            "role": "user",
            "content": "tail used only for analysis",
            "sessionId": "tail-only",
        })

        async def fake_llm(cfg, snap, event, latest_prompt):
            llm_inputs.extend(message.text for message in snap.messages)
            return {
                "topic": "tail boundary",
                "diagnosis": "The tail was analyzed without being stored as a full transcript.",
            }

        service = AnalysisService(store, fake_llm, {"llm": {}}, bus)
        report_id, session_id, _ = service.accept_report(
            student_id="student-a",
            session_id="tail-only",
            event="Stop",
            prompt_text="",
            transcript_content=transcript_tail,
        )

        await service.handle_stop(
            "student-a",
            session_id,
            "",
            transcript_tail,
            report_id,
        )

        assert store.get_raw_transcript_for_student_session(
            "student-a", "tail-only"
        ) is None
        assert llm_inputs == ["tail used only for analysis"]

    asyncio.run(scenario())


def test_stop_analysis_gate_covers_only_llm_invocation(tmp_path):
    async def scenario():
        store = Store(tmp_path / "service.db")
        bus = EventBus()
        gate_states = {}
        service = None

        class ObservedConfig(dict):
            def __deepcopy__(self, memo):
                gate_states["config"] = service.analysis_semaphore.locked()
                return dict(self)

        async def fake_llm(cfg, snap, event, latest_prompt):
            gate_states["llm"] = service.analysis_semaphore.locked()
            return {
                "topic": "gate scope",
                "diagnosis": "Only the provider call holds the gate.",
            }

        config = ObservedConfig({
            "service": {"analysis_max_concurrency": 1},
            "llm": {},
        })
        service = AnalysisService(store, fake_llm, config, bus)
        report_id, session_id, _ = service.accept_report(
            student_id="student-a",
            session_id="gate-scope",
            event="Stop",
            prompt_text="",
            transcript_content="",
        )

        await service.handle_stop(
            "student-a", session_id, "", "", report_id,
        )

        assert gate_states == {"config": False, "llm": True}

    asyncio.run(scenario())


def test_handle_stop_uses_uploaded_content_and_persists_full_chain(tmp_path):
    asyncio.run(_run_handle_stop_case(tmp_path))


async def _run_handle_stop_case(tmp_path):
    store = Store(tmp_path / "service.db")
    bus = EventBus()
    events = []

    async def collect(payload):
        events.append(payload)

    transcript_text = (
        _line({"type": "ai-title", "aiTitle": "Debug Session"})
        + _line({
            "type": "message",
            "role": "user",
            "content": "How do I fix this loop?",
            "sessionId": "sess-1",
            "cwd": "/work/project",
        })
        + _line({
            "type": "message",
            "role": "assistant",
            "content": "Print the boundary variables before changing the loop.",
        })
    )
    transcript_bytes = transcript_text.encode("utf-8")

    async def fake_llm(cfg, snap, event, latest_prompt):
        assert event == "Stop"
        assert latest_prompt == "How do I fix this loop after the assistant reply?"
        assert snap.ai_title == "Debug Session"
        assert [m.role for m in snap.messages] == ["user", "assistant"]
        assert snap.messages[0].text == "How do I fix this loop?"
        assert snap.messages[1].text == "Print the boundary variables before changing the loop."
        return {
            "topic": "loop debugging",
            "understanding": "low",
            "off_topic": False,
            "stuck_at": "loop boundary",
            "is_technical": True,
            "severity": "warn",
            "diagnosis": "The loop boundary is probably inverted.",
            "suggestion": "Print i and the boundary value before changing the condition.",
            "progress": "debugging",
            "guidance": "Verify the boundary with a minimal example.",
            "alert": "needs attention",
            "ai_reply_summary": "Assistant suggested printing boundary variables to locate the loop bug.",
        }

    bus.subscribe(collect)
    service = AnalysisService(
        copilot_repo=store,
        llm_analyzer=fake_llm,
        config={"llm": {}},
        event_bus=bus,
    )

    seed_prompt_id = store.add_prompt("sess-1", 0, "stu-1", "Earlier prompt in this session")
    report_id, session_id, _ = service.accept_report(
        student_id="stu-1",
        session_id="sess-1",
        event="Stop",
        prompt_text="How do I fix this loop after the assistant reply?",
        transcript_content=transcript_bytes,
        raw_transcript_content=transcript_bytes,
        cwd="/work/project",
    )
    assert store.get_raw_transcript("sess-1")["content"] == transcript_text

    result = await service.handle_stop(
        "stu-1",
        session_id,
        "How do I fix this loop after the assistant reply?",
        transcript_bytes,
        report_id,
    )

    with store._conn() as conn:
        prompts = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM prompts WHERE session_id = ? ORDER BY seq_in_session",
                ("sess-1",),
            ).fetchall()
        ]
        summaries = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM ai_summaries WHERE session_id = ?",
                ("sess-1",),
            ).fetchall()
        ]
        analyses = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM analyses WHERE session_id = ?",
                ("sess-1",),
            ).fetchall()
        ]
        transcripts = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM raw_transcripts WHERE session_id = ?",
                ("sess-1",),
            ).fetchall()
        ]
        report = dict(conn.execute(
            "SELECT * FROM reports WHERE id = ?",
            (report_id,),
        ).fetchone())

    assert result.topic == "loop debugging"
    assert [row["seq_in_session"] for row in prompts] == [0, 1]
    assert prompts[0]["id"] == seed_prompt_id
    assert prompts[0]["content"] == "Earlier prompt in this session"
    assert prompts[1]["content"] == "How do I fix this loop after the assistant reply?"
    prompt_id = prompts[1]["id"]

    assert len(summaries) == 1
    assert summaries[0]["prompt_id"] == prompt_id
    assert summaries[0]["content"] == (
        "Assistant suggested printing boundary variables to locate the loop bug."
    )

    assert len(analyses) == 1
    assert analyses[0]["report_id"] == report_id
    assert analyses[0]["student_id"] == "stu-1"
    assert analyses[0]["topic"] == "loop debugging"
    assert analyses[0]["severity"] == "warn"
    assert analyses[0]["is_technical"] == 1
    assert analyses[0]["diagnosis"] == "The loop boundary is probably inverted."
    assert analyses[0]["suggestion"] == (
        "Print i and the boundary value before changing the condition."
    )
    assert json.loads(analyses[0]["raw"])["ai_reply_summary"] == (
        "Assistant suggested printing boundary variables to locate the loop bug."
    )

    assert len(transcripts) == 1
    assert transcripts[0]["student_id"] == "stu-1"
    assert transcripts[0]["content"] == transcript_text
    assert report["analysis_pending"] == 0
    assert report["msg_count"] == 2
    assert store.get_session_title("sess-1") == "Debug Session"
    assert store.list_pending_reports() == []

    assert [event["type"] for event in events] == ["prompt", "ai_summary", "analysis"]
    assert events[0]["prompt_id"] == prompt_id
    assert events[0]["seq"] == 1
    assert events[0]["prompt"] == "How do I fix this loop after the assistant reply?"
    assert events[1]["summary"] == (
        "Assistant suggested printing boundary variables to locate the loop bug."
    )
    assert events[2]["report_id"] == report_id
    assert events[2]["result"]["diagnosis"] == "The loop boundary is probably inverted."


def test_handle_stop_keeps_report_pending_and_persists_no_false_analysis_on_provider_failure(tmp_path):
    async def scenario():
        store = Store(tmp_path / "service.db")
        bus = EventBus()
        events = []

        async def collect(payload):
            events.append(payload)

        async def failed_llm(cfg, snap, event, latest_prompt):
            return AnalysisOutcome(
                ok=False,
                value={
                    "topic": "provider fallback",
                    "understanding": "unknown",
                    "severity": "info",
                    "diagnosis": "display-only fallback",
                    "ai_reply_summary": "must not be persisted",
                },
                error="provider timeout",
            )

        bus.subscribe(collect)
        service = AnalysisService(store, failed_llm, {"llm": {}}, bus)
        report_id, session_id, _ = service.accept_report(
            student_id="stu-fail",
            session_id="sess-fail",
            event="Stop",
            prompt_text="why did it fail?",
            transcript_content=_line({"type": "message", "role": "user", "content": "why?"}),
            raw_transcript_content=_line({
                "type": "message", "role": "user", "content": "why?"
            }),
        )

        with pytest.raises(RuntimeError, match="provider timeout"):
            await service.handle_stop(
                "stu-fail",
                session_id,
                "why did it fail?",
                _line({"type": "message", "role": "user", "content": "why?"}),
                report_id,
            )

        assert store.list_pending_reports()[0]["id"] == report_id
        assert store.recent_analyses("stu-fail", limit=10, session_id="sess-fail") == []
        with store._conn() as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM ai_summaries WHERE session_id = ?",
                ("sess-fail",),
            ).fetchone()[0] == 0
        assert [event["type"] for event in events] == ["prompt"]

    asyncio.run(scenario())


def test_handle_stop_retry_reuses_one_persisted_prompt_and_publishes_it_once(tmp_path):
    async def scenario():
        store = Store(tmp_path / "service.db")
        bus = EventBus()
        events = []
        attempts = 0

        async def collect(payload):
            events.append(payload)

        async def flaky_llm(cfg, snap, event, latest_prompt):
            nonlocal attempts
            attempts += 1
            assert latest_prompt == "persist me across retries"
            if attempts <= 2:
                return AnalysisOutcome(
                    ok=False,
                    value={"topic": "fallback"},
                    error="LLM provider TimeoutError",
                )
            return {
                "topic": "recovered",
                "understanding": "medium",
                "severity": "info",
                "diagnosis": "one durable prompt",
                "ai_reply_summary": "one summary",
            }

        bus.subscribe(collect)
        service = AnalysisService(store, flaky_llm, {"llm": {}}, bus)
        report_id, session_id, _ = service.accept_report(
            student_id="stu-retry",
            session_id="sess-retry",
            event="Stop",
            prompt_text="persist me across retries",
            transcript_content="",
        )

        for _ in range(2):
            with pytest.raises(RuntimeError, match="TimeoutError"):
                await service.handle_stop(
                    "stu-retry",
                    session_id,
                    "persist me across retries",
                    "",
                    report_id,
                )

        result = await service.handle_stop(
            "stu-retry",
            session_id,
            "persist me across retries",
            "",
            report_id,
        )

        assert result.topic == "recovered"
        with store._conn() as conn:
            prompts = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM prompts WHERE report_id = ?",
                    (report_id,),
                ).fetchall()
            ]
            summaries = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM ai_summaries WHERE session_id = ?",
                    ("sess-retry",),
                ).fetchall()
            ]
        assert len(prompts) == 1
        assert prompts[0]["seq_in_session"] == 0
        assert prompts[0]["content"] == "persist me across retries"
        assert len(summaries) == 1
        assert summaries[0]["prompt_id"] == prompts[0]["id"]
        assert [event["type"] for event in events] == ["prompt", "ai_summary", "analysis"]
        assert store.list_pending_reports() == []

    asyncio.run(scenario())
