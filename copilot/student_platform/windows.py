"""Windows-owned WorkBuddy discovery with an explicit W0 evidence boundary.

Nothing in this module infers WorkBuddy's private project-directory encoding or
an active session.  The shared ``WorkBuddyDataAdapter`` can read an explicit
config directory; this module only selects from the documented Windows roots
and refuses rollout claims until a real-machine W0 manifest is present.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Literal

from .workbuddy import WorkBuddyDataAdapter


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "workbuddy" / "windows" / "manifest.json"
)
_REQUIRED_W0_FIELDS = frozenset(
    {"workbuddy_version", "config_dir", "hook_command", "transcript_mapping"}
)


@dataclass(frozen=True)
class WindowsConfigDirCandidate:
    """One known config root selected without expanding an unknown path."""

    path: Path | None
    source: str | None
    exists: bool = False


@dataclass(frozen=True)
class WindowsProbeResult:
    """Typed W0 outcome; ``ready`` still is not a Windows rollout approval."""

    status: Literal["ready", "blocked"]
    message: str
    config: WindowsConfigDirCandidate
    manifest_path: Path
    rollout_ready: bool = False

    @property
    def verdict(self) -> str:
        """Human-readable W0 gate suitable for logs and operator output."""
        return f"{self.status.upper()}: {self.message}"


def _is_existing_directory(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _existing_programdata_roots(programdata: str | None) -> list[Path]:
    if not programdata:
        return []
    users_root = Path(programdata) / "WorkBuddy" / "users"
    if not _is_existing_directory(users_root):
        return []
    try:
        user_roots = sorted(users_root.iterdir(), key=lambda item: item.name.casefold())
    except OSError:
        return []
    return [
        candidate / ".workbuddy"
        for candidate in user_roots
        if _is_existing_directory(candidate / ".workbuddy")
    ]


def _existing_workbuddy_env_roots(system_drive: str | None) -> list[Path]:
    """Enumerate, but never fabricate, WorkBuddy's documented fallback root."""

    if not system_drive:
        return []
    users_root = Path(system_drive) / "WorkBuddy-env"
    if not _is_existing_directory(users_root):
        return []
    try:
        user_roots = sorted(users_root.iterdir(), key=lambda item: item.name.casefold())
    except OSError:
        return []
    return [
        candidate / ".workbuddy"
        for candidate in user_roots
        if _is_existing_directory(candidate / ".workbuddy")
    ]


def probe_windows_config_dir(
    environ: Mapping[str, str] | None = None,
) -> WindowsConfigDirCandidate:
    """Return only an explicit or existing official WorkBuddy config root.

    ``WORKBUDDY_CONFIG_DIR`` is authoritative even before the target directory
    exists, because a managed installer can deliberately create that explicit
    location.  All fallback candidates must already exist; the probe never
    manufactures a path from a guessed username, installation location, or
    project-directory encoding.
    """

    env = os.environ if environ is None else environ
    explicit = env.get("WORKBUDDY_CONFIG_DIR")
    if explicit:
        path = Path(explicit)
        return WindowsConfigDirCandidate(
            path=path,
            source="WORKBUDDY_CONFIG_DIR",
            exists=_is_existing_directory(path),
        )

    userprofile = env.get("USERPROFILE")
    if userprofile:
        default_root = Path(userprofile) / ".workbuddy"
        if _is_existing_directory(default_root):
            return WindowsConfigDirCandidate(
                path=default_root,
                source="USERPROFILE/.workbuddy",
                exists=True,
            )

    roots = _existing_programdata_roots(env.get("PROGRAMDATA") or env.get("ProgramData"))
    if roots:
        return WindowsConfigDirCandidate(
            path=roots[0], source="ProgramData/WorkBuddy/users", exists=True
        )
    roots = _existing_workbuddy_env_roots(env.get("SystemDrive") or env.get("SYSTEMDRIVE"))
    if roots:
        return WindowsConfigDirCandidate(
            path=roots[0], source="SystemDrive/WorkBuddy-env", exists=True
        )
    return WindowsConfigDirCandidate(path=None, source=None, exists=False)


def _manifest_state(path: Path) -> Literal["missing", "incomplete", "ready"]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "missing"
    except (OSError, json.JSONDecodeError):
        return "incomplete"
    if not isinstance(raw, dict):
        return "incomplete"
    if all(isinstance(raw.get(field), str) and raw[field].strip() for field in _REQUIRED_W0_FIELDS):
        return "ready"
    return "incomplete"


class WindowsWorkBuddyProbe:
    """Gate the Windows adapter on real-machine W0 evidence, not assumptions."""

    def __init__(
        self,
        *,
        manifest_path: str | os.PathLike[str] = DEFAULT_MANIFEST_PATH,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.manifest_path = Path(manifest_path)
        self.environ = environ

    def probe(self) -> WindowsProbeResult:
        config = probe_windows_config_dir(self.environ)
        evidence = _manifest_state(self.manifest_path)
        if evidence == "missing":
            return WindowsProbeResult(
                status="blocked",
                message="real-machine evidence missing",
                config=config,
                manifest_path=self.manifest_path,
            )
        if evidence != "ready":
            return WindowsProbeResult(
                status="blocked",
                message="real-machine evidence incomplete",
                config=config,
                manifest_path=self.manifest_path,
            )
        if config.path is None:
            return WindowsProbeResult(
                status="blocked",
                message="WorkBuddy config directory not discovered",
                config=config,
                manifest_path=self.manifest_path,
            )
        if not config.exists:
            return WindowsProbeResult(
                status="blocked",
                message="WorkBuddy config directory is missing",
                config=config,
                manifest_path=self.manifest_path,
            )
        return WindowsProbeResult(
            status="ready",
            message="W0 evidence recorded; W1 and real-machine rollout remain required",
            config=config,
            manifest_path=self.manifest_path,
        )


class WindowsWorkBuddyData(WorkBuddyDataAdapter):
    """Windows name binding for explicit, already-discovered WorkBuddy data.

    Transcript lookup is inherited from the content-indexed adapter, so the
    Windows implementation never tries to derive a project directory from cwd.

    Satisfies the PlatformAdapter protocol through WorkBuddyDataAdapter.
    """
