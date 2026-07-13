# 04 — Split Store by domain aggregate

Status: resolved

## What to build

Store is deep in the right way (non-trivial SQL behind simple call signatures) but the interface is enormous — 40+ public methods in one 2300-line file. Finding the right method requires scrolling through all of `store.py`.

Split into `SessionStore`, `MessageStore`, `UploadStore` — each backed by the same SQLite connection. AppContext wires all three. Each service gets only the sub-store it needs. Same SQL, same behaviour, smaller interfaces: 8 methods, 6 methods, 12 methods respectively.

This is a structural split, not a CQRS/event-sourcing change. All sub-stores share one `sqlite3.Connection`. The split is purely about interface navigation.

## Acceptance criteria

- [ ] `SessionStore` extracted from `store.py` — owns sessions, reports, analyses, prompts, ai_summaries, students, student_asks, prompt_configs tables (~8 public methods)
- [ ] `MessageStore` extracted — owns messages, mentor_messages tables (~6 methods)
- [ ] `UploadStore` extracted — owns upload_requests, upload_request_sessions, raw_transcripts tables (~12 methods)
- [ ] All sub-stores accept `db_path` and share one connection lifecycle (or a connection factory)
- [ ] `AppContext` wires all three sub-stores; each service receives only the sub-store it needs
- [ ] `AnalysisService` receives `SessionStore` instead of full `Store`
- [ ] `MessageService` receives `MessageStore` instead of full `Store`
- [ ] `UploadRequestService` receives `UploadStore` instead of full `Store`
- [ ] All existing tests pass — test fixtures updated to create sub-stores
- [ ] P0 server redlines scan still passes

## Blocked by

- 01-collapse-session-query-service (changes what Store methods are used)
- 02-move-upload-state-to-wire-mapping (changes upload-related Store usage patterns)