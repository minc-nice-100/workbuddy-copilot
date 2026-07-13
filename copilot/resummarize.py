"""Regenerate prompt-scoped AI reply summaries for recent sessions."""
from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .config import load_config
from .llm import summarize_reply
from .store import Store

log = logging.getLogger("copilot.resummarize")

SummaryFunc = Callable[[dict, str, str], Awaitable[str]]


async def summarize_latest_sessions(
    config: dict[str, Any],
    *,
    latest: int = 2,
    concurrency: int = 2,
    store: Store | None = None,
    summarizer: SummaryFunc = summarize_reply,
) -> dict[str, int]:
    """Summarize every user round in the latest N sessions."""
    repo = store or Store(config["store"]["db_path"])
    sessions = repo.get_recent_sessions(limit=latest)
    stats = {
        "sessions": len(sessions),
        "rounds": 0,
        "prompts": 0,
        "summarized": 0,
        "skipped": 0,
    }
    semaphore = asyncio.Semaphore(max(1, int(concurrency or 1)))

    def matching_prompt(prompts: list[dict[str, Any]], round_row: dict[str, Any]) -> dict[str, Any] | None:
        round_seq = int(round_row.get("seq") or 0)
        round_text = str(round_row.get("content") or "")
        for prompt in prompts:
            if int(prompt.get("seq_in_session") or 0) == round_seq:
                return prompt
        for prompt in prompts:
            if str(prompt.get("content") or "") == round_text:
                return prompt
        return None

    async def handle_message_round(
        session_id: str,
        round_row: dict[str, Any],
        prompts: list[dict[str, Any]],
    ) -> None:
        message_id = int(round_row["id"])
        prompt_text = str(round_row.get("content") or "")
        full_reply = str(round_row.get("reply") or "")
        prompt = matching_prompt(prompts, round_row)
        existing_summary = str(round_row.get("summary") or "").strip()
        if existing_summary:
            if prompt:
                repo.upsert_ai_summary(
                    int(prompt["id"]),
                    session_id,
                    str(round_row.get("student_id") or prompt.get("student_id") or ""),
                    existing_summary,
                )
            stats["skipped"] += 1
            log.info(
                "skip message round with existing summary session=%s message_id=%s seq=%s",
                session_id,
                message_id,
                round_row.get("seq"),
            )
            return
        if not full_reply.strip():
            stats["skipped"] += 1
            log.info(
                "skip message round without assistant reply session=%s message_id=%s seq=%s",
                session_id,
                message_id,
                round_row.get("seq"),
            )
            return

        async with semaphore:
            summary = await summarizer(config, prompt_text, full_reply)
        summary = summary.strip()
        if not summary:
            stats["skipped"] += 1
            log.info(
                "skip empty generated summary session=%s message_id=%s seq=%s",
                session_id,
                message_id,
                round_row.get("seq"),
            )
            return

        repo.set_message_summary(message_id, summary)
        if prompt:
            repo.upsert_ai_summary(
                int(prompt["id"]),
                session_id,
                str(prompt.get("student_id") or round_row.get("student_id") or ""),
                summary,
            )
        stats["summarized"] += 1

    async def handle_prompt(session_id: str, prompt: dict[str, Any], existing_summary: str = "") -> None:
        prompt_text = str(prompt.get("content") or "")
        prompt_seq = int(prompt.get("seq_in_session") or 0)
        if existing_summary.strip():
            stats["skipped"] += 1
            log.info(
                "skip prompt with existing summary session=%s prompt_id=%s seq=%s",
                session_id,
                prompt.get("id"),
                prompt_seq,
            )
            return
        full_reply = repo.get_prompt_reply(session_id, prompt_seq)
        if not full_reply.strip():
            stats["skipped"] += 1
            log.info(
                "skip prompt without assistant reply session=%s prompt_id=%s seq=%s",
                session_id,
                prompt.get("id"),
                prompt_seq,
            )
            return

        async with semaphore:
            summary = await summarizer(config, prompt_text, full_reply)
        summary = summary.strip()
        if not summary:
            stats["skipped"] += 1
            log.info(
                "skip empty generated summary session=%s prompt_id=%s seq=%s",
                session_id,
                prompt.get("id"),
                prompt_seq,
            )
            return

        repo.upsert_ai_summary(
            int(prompt["id"]),
            session_id,
            str(prompt.get("student_id") or ""),
            summary,
        )
        stats["summarized"] += 1

    tasks: list[asyncio.Task[None]] = []
    for session in sessions:
        session_id = str(session.get("session_id") or "")
        if not session_id:
            continue
        prompts = repo.get_prompts_by_session(session_id)
        prompt_summaries = {
            int(summary["prompt_id"]): str(summary.get("content") or "")
            for summary in repo.get_ai_summaries_by_session(session_id)
            if summary.get("prompt_id") is not None
        }
        stats["prompts"] += len(prompts)
        message_rounds = repo.get_message_rounds_by_session(session_id)
        if message_rounds:
            stats["rounds"] += len(message_rounds)
            for round_row in message_rounds:
                tasks.append(asyncio.create_task(handle_message_round(session_id, round_row, prompts)))
        else:
            stats["rounds"] += len(prompts)
            for prompt in prompts:
                existing_summary = prompt_summaries.get(int(prompt["id"]), "")
                tasks.append(asyncio.create_task(handle_prompt(session_id, prompt, existing_summary)))

    if tasks:
        await asyncio.gather(*tasks)
    return stats


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Regenerate AI reply summaries for recent sessions.")
    parser.add_argument("--latest", type=int, default=2, help="Number of recent sessions to process.")
    parser.add_argument("--concurrency", type=int, default=2, help="Maximum concurrent LLM calls.")
    parser.add_argument("--config", default=None, help="Optional config.json path.")
    return parser.parse_args()


async def _amain() -> dict[str, int]:
    args = _parse_args()
    config = load_config(args.config)
    stats = await summarize_latest_sessions(
        config,
        latest=args.latest,
        concurrency=args.concurrency,
    )
    print(
        "resummarize complete: "
        f"sessions={stats['sessions']} rounds={stats['rounds']} prompts={stats['prompts']} "
        f"summarized={stats['summarized']} skipped={stats['skipped']}"
    )
    return stats


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
