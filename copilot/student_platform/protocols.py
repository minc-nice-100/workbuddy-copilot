"""Static protocols for the student-platform adapter seam.

A typing.Protocol costs zero at runtime and gives static checkers
the ability to catch interface drift between adapters.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .workbuddy import (
        ActiveSessionResult,
        ProbeResult,
        TranscriptReadResult,
        WorkBuddySession,
    )


class PlatformAdapter(Protocol):
    """Structural interface shared by all platform data adapters.

    Every adapter that reads local WorkBuddy data must satisfy this
    protocol so that the shared student core can treat macOS and
    Windows implementations as interchangeable.
    """

    def probe(self) -> ProbeResult:
        """Validate the local DB contract and return typed capabilities."""
        ...

    def list_sessions(
        self, *, include_deleted: bool = False, limit: int = 1000
    ) -> list[WorkBuddySession]:
        """Return normalized session metadata."""
        ...

    def read_transcript(self, session_id: str) -> TranscriptReadResult:
        """Read a transcript found by the stable session id."""
        ...

    def detect_active_session(self) -> ActiveSessionResult:
        """Return the currently active session or a typed failure."""
        ...

    def list_workspaces(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        """Return raw workspace fields for legacy CLI compatibility."""
        ...