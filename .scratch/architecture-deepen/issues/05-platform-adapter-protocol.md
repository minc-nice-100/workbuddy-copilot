# 05 — Define PlatformAdapter Protocol for student-platform seam

Status: resolved

## What to build

Three platform adapters (`student_platform/macos.py`, `windows.py`, `workbuddy.py`) share an implicit duck-typed interface but have no shared contract. The target architecture says "no ABCs" — but a `typing.Protocol` is a static-only contract that costs zero at runtime.

Define a `PlatformAdapter` Protocol class that declares the shared interface. Each adapter class explicitly declares it implements the protocol. This gives mypy/pyright the ability to catch interface drift between adapters.

One adapter = hypothetical seam. Two adapters = real seam. Three adapters with no shared contract = drift risk.

## Acceptance criteria

- [ ] `PlatformAdapter` Protocol class defined in `student_platform/protocols.py`
- [ ] Protocol declares the shared interface: `probe()`, `list_sessions()`, `read_transcript()` (plus any other truly shared methods)
- [ ] `MacOSWorkBuddyData`, `WindowsWorkBuddyData`, `WorkBuddyDataAdapter` explicitly declare they implement the protocol
- [ ] `mypy` or `pyright` passes on the student_platform package (no new errors from the protocol)
- [ ] Zero runtime overhead — no ABC registration, no metaclass, no import-time side effects
- [ ] Existing tests pass unchanged

## Blocked by

None - can start immediately