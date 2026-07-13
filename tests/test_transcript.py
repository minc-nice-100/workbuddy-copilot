"""transcript.py 单元测试：JSONL 解析逻辑。

覆盖三种 content type (text/input_text/output_text)、
空文件、tool_use/tool_result、损坏行等情况。
"""
from __future__ import annotations

import json

from copilot.transcript import (
    parse_text,
    parse_turns,
    extract_user_query,
    _extract_text,
)


def _jsonl(lines: list[dict]) -> bytes:
    return "\n".join(json.dumps(obj, ensure_ascii=False) for obj in lines).encode("utf-8")


class TestExtractText:
    def test_string_content(self):
        assert _extract_text("hello") == "hello"

    def test_text_type(self):
        content = [{"type": "text", "text": "你好"}]
        assert _extract_text(content) == "你好"

    def test_input_text_type(self):
        content = [{"type": "input_text", "text": "用户输入"}]
        assert _extract_text(content) == "用户输入"

    def test_output_text_type(self):
        content = [{"type": "output_text", "text": "AI 回复"}]
        assert _extract_text(content) == "AI 回复"

    def test_tool_use(self):
        content = [{"type": "tool_use", "name": "bash"}]
        assert "<tool_use:bash>" in _extract_text(content)

    def test_tool_result(self):
        content = [{"type": "tool_result"}]
        assert "<tool_result>" in _extract_text(content)

    def test_mixed_content(self):
        content = [
            {"type": "text", "text": "前半"},
            {"type": "tool_use", "name": "read"},
            {"type": "text", "text": "后半"},
        ]
        result = _extract_text(content)
        assert "前半" in result and "后半" in result

    def test_empty_list(self):
        assert _extract_text([]) == ""

    def test_none(self):
        assert _extract_text(None) == ""


class TestParseText:
    def test_empty_content(self):
        snap = parse_text(b"")
        assert len(snap.messages) == 0
        assert snap.total_lines == 0

    def test_none_content(self):
        snap = parse_text(None)
        assert len(snap.messages) == 0

    def test_user_assistant_messages_from_bytes(self):
        payload = _jsonl([
            {"type": "message", "role": "user", "content": [{"type": "text", "text": "学 Python"}]},
            {"type": "message", "role": "assistant", "content": [{"type": "text", "text": "好的"}]},
        ])
        snap = parse_text(payload)
        assert len(snap.messages) == 2
        assert snap.messages[0].role == "user"
        assert snap.messages[0].text == "学 Python"
        assert snap.messages[1].role == "assistant"
        assert snap.messages[1].text == "好的"

    def test_tool_calls_counted(self):
        payload = _jsonl([
            {"type": "message", "role": "user", "content": "hi"},
            {"type": "function_call", "name": "bash"},
            {"type": "function_call", "name": "read"},
        ])
        snap = parse_text(payload)
        assert snap.tool_calls == 2

    def test_reasoning_counted(self):
        payload = _jsonl([
            {"type": "reasoning"},
            {"type": "reasoning"},
            {"type": "reasoning"},
        ])
        snap = parse_text(payload)
        assert snap.reasoning_steps == 3

    def test_ai_title(self):
        payload = _jsonl([
            {"type": "ai-title", "aiTitle": "Python 学习"},
        ])
        snap = parse_text(payload)
        assert snap.ai_title == "Python 学习"

    def test_corrupt_line_skipped(self):
        payload = b'{"type": "message", "role": "user", "content": "ok"}\nnot json\n'
        snap = parse_text(payload)
        assert len(snap.messages) == 1
        assert snap.total_lines == 2

    def test_session_id_captured(self):
        payload = _jsonl([
            {"type": "message", "role": "user", "content": "x", "sessionId": "abc-123"},
        ])
        snap = parse_text(payload)
        assert snap.session_id == "abc-123"

    def test_cwd_captured(self):
        payload = _jsonl([
            {"type": "message", "role": "user", "content": "x", "cwd": "/work/project"},
        ])
        snap = parse_text(payload)
        assert snap.cwd == "/work/project"

    def test_to_text(self):
        payload = _jsonl([
            {"type": "message", "role": "user", "content": "问题"},
            {"type": "message", "role": "assistant", "content": "回答"},
        ])
        snap = parse_text(payload)
        text = snap.to_text()
        assert "[user]" in text and "问题" in text
        assert "[assistant]" in text and "回答" in text

    def test_to_text_last_n(self):
        msgs = [
            {"type": "message", "role": "user", "content": f"msg{i}"}
            for i in range(10)
        ]
        snap = parse_text(_jsonl(msgs))
        text = snap.to_text(last_n=3)
        assert "msg9" in text and "msg7" in text
        assert "msg0" not in text


class TestBulkUploadParsing:
    def test_extract_user_query_prefers_tagged_text(self):
        text = "prefix <user_query>怎么修复 pytest 失败？</user_query> suffix"
        assert extract_user_query(text) == "怎么修复 pytest 失败？"

    def test_extract_user_query_falls_back_to_full_text_without_tag(self):
        text = "这是一段没有标签的原生 input_text"
        assert extract_user_query(text) == text

    def test_extract_user_query_joins_multi_input_text_fragments_before_matching(self):
        payload = _jsonl([
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "prefix <user_query>第一段"},
                    {"type": "input_text", "text": "第二段</user_query> suffix"},
                ],
            },
        ])
        snap = parse_text(payload)

        turns = parse_turns(snap.messages)

        assert turns == [{
            "seq": 0,
            "role": "user",
            "text": "第一段\n第二段",
            "ts": None,
        }]

    def test_parse_turns_pairs_user_and_assistant_by_jsonl_order(self):
        payload = _jsonl([
            {
                "type": "message",
                "role": "user",
                "content": "<user_query>问题 1</user_query>",
                "timestamp": 10,
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "回答 1"}],
                "timestamp": 11,
            },
            {
                "type": "message",
                "role": "user",
                "content": "没有标签的问题 2",
                "timestamp": 20,
            },
            {
                "type": "message",
                "role": "assistant",
                "content": "回答 2",
                "timestamp": 21,
            },
        ])
        snap = parse_text(payload)

        turns = parse_turns(snap.messages)

        assert [(t["seq"], t["role"], t["text"], t["ts"]) for t in turns] == [
            (0, "user", "问题 1", 10),
            (0, "assistant", "回答 1", 11),
            (1, "user", "没有标签的问题 2", 20),
            (1, "assistant", "回答 2", 21),
        ]

    def test_parse_turns_preserves_pure_assistant_round(self):
        payload = _jsonl([
            {
                "type": "message",
                "role": "assistant",
                "content": "开场说明",
                "timestamp": 30,
            },
        ])
        snap = parse_text(payload)

        turns = parse_turns(snap.messages)

        assert turns == [{
            "seq": 0,
            "role": "assistant",
            "text": "开场说明",
            "ts": 30,
        }]
