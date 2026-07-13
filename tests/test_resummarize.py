from __future__ import annotations

import asyncio

from copilot.resummarize import summarize_latest_sessions
from copilot.store import Store


def test_resummarize_latest_sessions_is_limited_and_idempotent(tmp_path):
    asyncio.run(_run_resummarize_case(tmp_path))


async def _run_resummarize_case(tmp_path):
    store = Store(tmp_path / "copilot.db")
    calls: list[tuple[str, str]] = []

    store.upsert_session("sess-old", "stu-1", "/work", "old", created_at=1.0, last_activity_at=1.0)
    store.upsert_session("sess-new-1", "stu-1", "/work", "new 1", created_at=2.0, last_activity_at=20.0)
    store.upsert_session("sess-new-2", "stu-1", "/work", "new 2", created_at=3.0, last_activity_at=30.0)

    old_prompt = store.add_prompt("sess-old", 0, "stu-1", "旧会话问题")
    first_prompt = store.add_prompt("sess-new-1", 0, "stu-1", "新会话一问题")
    second_prompt = store.add_prompt("sess-new-2", 0, "stu-1", "新会话二问题")
    for session_id, prompt, reply in [
        ("sess-old", "旧会话问题", "旧回复"),
        ("sess-new-1", "新会话一问题", "新回复一"),
        ("sess-new-2", "新会话二问题", "新回复二"),
    ]:
        store.replace_session_messages(
            session_id=session_id,
            student_id="stu-1",
            turns=[
                {"seq": 0, "role": "user", "text": prompt, "ts": 1.0},
                {"seq": 0, "role": "assistant", "text": reply, "ts": 2.0},
            ],
            raw=f"{prompt}\n{reply}",
            sha=f"sha-{session_id}",
        )

    async def fake_summarizer(config, prompt_text, full_reply_text):
        calls.append((prompt_text, full_reply_text))
        return f"摘要：{prompt_text} -> {full_reply_text}"

    stats = await summarize_latest_sessions(
        {"llm": {"enable_llm": True}},
        latest=2,
        concurrency=2,
        store=store,
        summarizer=fake_summarizer,
    )
    stats_second = await summarize_latest_sessions(
        {"llm": {"enable_llm": True}},
        latest=2,
        concurrency=2,
        store=store,
        summarizer=fake_summarizer,
    )

    assert stats == {"sessions": 2, "rounds": 2, "prompts": 2, "summarized": 2, "skipped": 0}
    assert stats_second == {"sessions": 2, "rounds": 2, "prompts": 2, "summarized": 0, "skipped": 2}
    assert calls == [
        ("新会话二问题", "新回复二"),
        ("新会话一问题", "新回复一"),
    ]

    rows = store.get_ai_summaries_by_session("sess-new-1") + store.get_ai_summaries_by_session("sess-new-2")
    assert len(rows) == 2
    assert {row["prompt_id"] for row in rows} == {first_prompt, second_prompt}
    assert store.get_ai_summaries_by_session("sess-old") == []
    assert old_prompt not in {row["prompt_id"] for row in rows}


def test_resummarize_latest_sessions_summarizes_bulk_message_rounds(tmp_path):
    asyncio.run(_run_bulk_resummarize_case(tmp_path))


async def _run_bulk_resummarize_case(tmp_path):
    store = Store(tmp_path / "copilot.db")
    calls: list[tuple[str, str]] = []

    store.upsert_session("sess-bulk", "stu-1", "/work", "bulk", created_at=1.0, last_activity_at=10.0)
    store.replace_session_messages(
        session_id="sess-bulk",
        student_id="stu-1",
        turns=[
            {"seq": 0, "role": "user", "text": "第一问", "ts": 10.0},
            {"seq": 0, "role": "assistant", "text": "第一答 A", "ts": 11.0},
            {"seq": 1, "role": "assistant", "text": "第一答 B", "ts": 12.0},
            {"seq": 2, "role": "user", "text": "第二问", "ts": 20.0},
            {"seq": 2, "role": "assistant", "text": "第二答", "ts": 21.0},
        ],
        raw="raw",
        sha="sha-bulk",
    )

    async def fake_summarizer(config, prompt_text, full_reply_text):
        calls.append((prompt_text, full_reply_text))
        return f"摘要：{prompt_text} -> {full_reply_text}"

    stats = await summarize_latest_sessions(
        {"llm": {"enable_llm": True}},
        latest=1,
        concurrency=2,
        store=store,
        summarizer=fake_summarizer,
    )
    stats_second = await summarize_latest_sessions(
        {"llm": {"enable_llm": True}},
        latest=1,
        concurrency=2,
        store=store,
        summarizer=fake_summarizer,
    )

    assert stats == {"sessions": 1, "rounds": 2, "prompts": 0, "summarized": 2, "skipped": 0}
    assert stats_second == {"sessions": 1, "rounds": 2, "prompts": 0, "summarized": 0, "skipped": 2}
    assert calls == [
        ("第一问", "第一答 A\n\n第一答 B"),
        ("第二问", "第二答"),
    ]

    timeline = store.get_timeline_by_session("sess-bulk")
    summaries = [row for row in timeline if row["type"] == "ai_summary"]
    assert [row["content"] for row in summaries] == [
        "摘要：第一问 -> 第一答 A\n\n第一答 B",
        "摘要：第二问 -> 第二答",
    ]
    assert all(str(row["reply_ref"]).startswith("msg:") for row in summaries)
