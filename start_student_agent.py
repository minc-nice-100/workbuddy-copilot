#!/usr/bin/env python3
"""Start the platform-neutral headless student agent.

WorkBuddy-specific upload handling is supplied by a later platform adapter;
this entry point is still useful for hook delivery and smoke testing.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from copilot.student_core.agent import StudentAgent
from copilot.student_core.coordinator import StudentCoordinator
from copilot.student_core.spool import EventSpool
from copilot.student_core.transport import StudentTransport


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the WorkBuddy student agent")
    parser.add_argument("--base-url", default=os.environ.get("COPILOT_BASE_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--student-id", default=os.environ.get("COPILOT_STUDENT_ID", "student-1"))
    parser.add_argument("--token", default=os.environ.get("COPILOT_STUDENT_TOKEN", ""))
    parser.add_argument(
        "--spool-dir",
        default=os.environ.get("COPILOT_SPOOL_DIR", str(Path.home() / ".copilot" / "spool")),
    )
    parser.add_argument("--interval", type=float, default=float(os.environ.get("COPILOT_AGENT_INTERVAL", "1")))
    return parser


async def _run(args: argparse.Namespace) -> None:
    spool = EventSpool(args.spool_dir)
    transport = StudentTransport(
        args.base_url,
        student_id=args.student_id,
        token=args.token,
    )
    # WorkBuddyData upload orchestration is injected by a platform adapter in
    # a later phase.  Do not pretend this headless core can complete uploads.
    coordinator = StudentCoordinator(spool, transport, uploader=None)
    await StudentAgent(coordinator, interval=args.interval).run()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
