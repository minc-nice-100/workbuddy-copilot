# 01 — Collapse SessionQueryService into Store

Status: resolved

## What to build

Delete the 100-line `SessionQueryService` pass-through module. Every public method is a one-liner delegating to Store, wrapping `dict` rows in `Conversation`/`TimelineEntry`/`Student` dataclasses. Move the dict→dataclass conversion into the Store methods that return those rows, so Store returns typed objects directly. Update all callers (controllers, tests) to use Store instead of SessionQueryService.

The deletion test is unambiguous: nothing concentrates, complexity just finds its natural home in Store.

## Acceptance criteria

- [ ] Store methods (`get_sessions_by_student`, `get_timeline_by_session`, `students_overview`, `get_active_session_from_table`, `list_sessions_from_table`) return `Conversation`, `TimelineEntry`, `Student` dataclasses instead of `dict`
- [ ] `SessionQueryService` class is deleted from `services.py`
- [ ] Controller routes in `service.py` and `mentor/routes.py` use `Store` directly via `Depends(get_store)` instead of `Depends(get_session_service)`
- [ ] `get_session_service` dependency function is removed from `app_context.py`
- [ ] `SessionQueryService` is removed from `AppContext` and `build_context()`
- [ ] All existing tests pass unchanged (or with mechanical signature updates only)
- [ ] P0 server redlines scan still passes (zero violations)

## Blocked by

None - can start immediately