"""导师观察台 timeline 聚合纯函数。

将 prompts / ai_summaries / analyses 三表数据合并为统一时间线，
按 created_at 排序，供导师侧 UI 渲染。
"""
from __future__ import annotations

from typing import Any


def format_timeline_item(raw: dict[str, Any]) -> dict[str, Any]:
    """统一单条事件格式：type/content/created_at + 各类型特有字段。

    保留原始 dict 中已有的字段（如 severity/topic/prompt_id/seq_in_session 等），
    仅确保 type/content/created_at 三个核心字段存在。
    """
    item: dict[str, Any] = dict(raw)  # 浅拷贝，保留特有字段
    item.setdefault("type", "unknown")
    item.setdefault("content", raw.get("content", ""))
    item.setdefault("created_at", raw.get("created_at"))
    return item


def merge_timeline(
    prompts: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
) -> list[dict]:
    """三表数据合并按 created_at 排序。

    每个输入是 list[dict]，输出统一格式 list[dict]。
    若输入项未带 type 字段，按来源表补默认 type。
    """
    combined: list[dict[str, Any]] = []
    for row in prompts:
        item = dict(row)
        item.setdefault("type", "prompt")
        combined.append(item)
    for row in summaries:
        item = dict(row)
        item.setdefault("type", "ai_summary")
        combined.append(item)
    for row in analyses:
        item = dict(row)
        item.setdefault("type", "analysis")
        combined.append(item)
    combined.sort(key=lambda r: r.get("created_at", 0.0))
    return combined
