"""Narrow, OS-owned adapters for the shared student client."""

from .protocols import PlatformAdapter
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
    "PlatformAdapter",
    "ProbeResult",
    "TranscriptReadResult",
    "WorkBuddyDataAdapter",
    "WorkBuddyDataError",
    "WorkBuddySession",
]
