"""解析 WorkBuddy 对话 JSONL 文件，提取结构化消息。

JSONL 每行一个 JSON 对象。我们关心 type=message 的行（用户/助手消息），
其他类型（reasoning/function_call/file-history-snapshot/ai-title）暂只统计计数，
后续分析策略迭代时可以再扩展。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Message:
    role: str  # user / assistant / system
    text: str
    timestamp: int | None = None
    raw_id: str | None = None


@dataclass
class TranscriptSnapshot:
    messages: list[Message] = field(default_factory=list)
    tool_calls: int = 0
    reasoning_steps: int = 0
    session_id: str | None = None
    cwd: str | None = None
    ai_title: str | None = None
    total_lines: int = 0

    def to_text(self, last_n: int | None = None) -> str:
        msgs = self.messages[-last_n:] if last_n else self.messages
        return "\n\n".join(f"[{m.role}] {m.text}" for m in msgs)


TEXT_CONTENT_TYPES = {"text", "input_text", "output_text"}
USER_QUERY_RE = re.compile(r"<user_query>(.*?)</user_query>", re.DOTALL)


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                itype = item.get("type")
                if itype in TEXT_CONTENT_TYPES:
                    parts.append(item.get("text", ""))
                elif itype == "tool_use":
                    name = item.get("name", "tool")
                    parts.append(f"<tool_use:{name}>")
                elif itype == "tool_result":
                    parts.append("<tool_result>")
        return "\n".join(p for p in parts if p)
    return ""


def parse_text(text_or_bytes: str | bytes | bytearray | None) -> TranscriptSnapshot:
    """Parse JSONL transcript content already uploaded to the server."""
    snap = TranscriptSnapshot()
    if text_or_bytes is None:
        return snap

    if isinstance(text_or_bytes, (bytes, bytearray)):
        text = bytes(text_or_bytes).decode("utf-8", errors="replace")
    else:
        text = str(text_or_bytes)

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        snap.total_lines += 1
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        t = obj.get("type")
        if t == "message":
            role = obj.get("role", "unknown")
            content = _extract_text(obj.get("content", []))
            ts = obj.get("timestamp")
            mid = obj.get("id")
            if content:
                snap.messages.append(
                    Message(role=role, text=content, timestamp=ts, raw_id=mid)
                )
            if not snap.session_id:
                snap.session_id = obj.get("sessionId")
            if not snap.cwd:
                snap.cwd = obj.get("cwd")
        elif t == "reasoning":
            snap.reasoning_steps += 1
        elif t == "function_call":
            snap.tool_calls += 1
        elif t == "ai-title":
            snap.ai_title = obj.get("aiTitle")

    return snap


def extract_user_query(text: str) -> str:
    """Extract the tagged user query, falling back to the full uploaded text."""
    raw = str(text or "")
    match = USER_QUERY_RE.search(raw)
    if match:
        return match.group(1).strip()
    return raw.strip()


def parse_turns(message_rows: list[Message | dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert uploaded message rows into stable user/assistant timeline turns.

    A user message starts a new round; the following assistant message shares
    that round number. Assistant-only rows are preserved as their own round.
    """
    turns: list[dict[str, Any]] = []
    current_seq = 0
    started = False
    last_role = ""

    for row in message_rows:
        if isinstance(row, Message):
            role = row.role
            text = row.text
            ts = row.timestamp
        else:
            role = str(row.get("role") or "")
            text = str(row.get("text") or row.get("content") or "")
            ts = row.get("timestamp") or row.get("ts")

        if role not in {"user", "assistant"}:
            continue
        text = extract_user_query(text) if role == "user" else text.strip()
        if not text:
            continue

        if role == "user":
            if started:
                current_seq += 1
            started = True
        elif not started:
            started = True
        elif last_role == "assistant":
            current_seq += 1

        turns.append({
            "seq": current_seq,
            "role": role,
            "text": text,
            "ts": ts,
        })
        last_role = role

    return turns


def parse_transcript(path: str | Path) -> TranscriptSnapshot:
    """Compatibility wrapper for local tools/tests; server code must not use it."""
    p = Path(path).expanduser()
    if not p.exists():
        return TranscriptSnapshot()
    with p.open("rb") as f:
        return parse_text(f.read())

    return snap


def recent_messages(path: str | Path, last_n: int = 16) -> list[Message]:
    """只取最近 N 条 message，用于喂给 LLM。"""
    snap = parse_transcript(path)
    return snap.messages[-last_n:]
