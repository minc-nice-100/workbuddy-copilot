"""测试 llm.py 新增 ai_reply_summary 字段。"""
import pytest

from copilot.llm import _parse_json_content, _fallback, SYSTEM_PROMPT
from copilot.transcript import TranscriptSnapshot


class TestParseJsonContent:
    """_parse_json_content 解析 ai_reply_summary 字段。"""

    def test_parse_with_ai_reply_summary(self):
        content = '{"topic":"t","understanding":"high","off_topic":false,"stuck_at":"","is_technical":true,"severity":"info","diagnosis":"d","suggestion":"s","progress":"p","guidance":"g","alert":"","ai_reply_summary":"AI解释了asyncio.gather的用法"}'
        result = _parse_json_content(content)
        assert "ai_reply_summary" in result
        assert result["ai_reply_summary"] == "AI解释了asyncio.gather的用法"

    def test_parse_without_ai_reply_summary_backward_compat(self):
        content = '{"topic":"t","understanding":"high","off_topic":false,"stuck_at":"","is_technical":true,"severity":"info","diagnosis":"d","suggestion":"s","progress":"p","guidance":"g","alert":""}'
        result = _parse_json_content(content)
        assert "ai_reply_summary" in result
        assert result["ai_reply_summary"] == ""

    def test_ai_reply_summary_truncated_to_150(self):
        long_summary = "摘要" * 100
        content = f'{{"topic":"t","understanding":"high","ai_reply_summary":"{long_summary}"}}'
        result = _parse_json_content(content)
        assert len(result["ai_reply_summary"]) <= 150


class TestFallback:
    """_fallback 返回包含 ai_reply_summary。"""

    def test_fallback_contains_ai_reply_summary(self):
        snap = TranscriptSnapshot(messages=[], tool_calls=0, reasoning_steps=0, session_id="", ai_title="")
        result = _fallback(snap, "Stop")
        assert "ai_reply_summary" in result
        assert result["ai_reply_summary"] == ""

    def test_fallback_other_fields_intact(self):
        snap = TranscriptSnapshot(messages=[{"role":"user","content":"hi"}], tool_calls=0, reasoning_steps=0, session_id="", ai_title="标题")
        result = _fallback(snap, "Stop")
        assert result["topic"] == "标题"
        assert result["understanding"] == "unknown"


class TestSystemPrompt:
    """SYSTEM_PROMPT 包含摘要任务。"""

    def test_prompt_contains_ai_reply_summary_keyword(self):
        assert "ai_reply_summary" in SYSTEM_PROMPT

    def test_prompt_contains_summary_task(self):
        assert "摘要" in SYSTEM_PROMPT
