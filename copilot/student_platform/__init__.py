"""Narrow, OS-owned adapters for the shared student client."""

from .workbuddy import (
    ActiveSessionResult,
    AdapterFailure,
    ProbeResult,
    TranscriptReadResult,
    WorkBuddyDataAdapter,
    WorkBuddyDataError,
    WorkBuddySession,
)

__all__ = [
    "ActiveSessionResult",
    "AdapterFailure",
    "ProbeResult",
    "TranscriptReadResult",
    "WorkBuddyDataAdapter",
    "WorkBuddyDataError",
    "WorkBuddySession",
]
