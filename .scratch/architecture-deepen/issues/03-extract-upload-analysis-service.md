# 03 — Extract UploadAnalysisService from the controller

Status: resolved

## What to build

`_analyze_uploaded_session_background`, `_retry_upload_request_analysis_background`, and `_recover_pending_reports` are ~200 lines of business orchestration living in the controller (`service.py`). The controller should be an HTTP codec, not an analysis state machine.

Extract a new `UploadAnalysisService` module that owns the full background analysis lifecycle: enqueue, retry, and crash recovery. It depends on `Store`, `AnalysisService`, `UploadRequestService`, and `EventBus` — all injected via constructor. The controller calls `upload_analysis_svc.enqueue(session_id, sha)` and the new module handles the rest.

## Acceptance criteria

- [ ] New `copilot/upload_analysis.py` module with `UploadAnalysisService` class
- [ ] `UploadAnalysisService` accepts `store`, `analysis_svc`, `upload_svc`, `event_bus` via constructor injection
- [ ] `_analyze_uploaded_session_background` logic moved into `UploadAnalysisService.analyze_session()`
- [ ] `_retry_upload_request_analysis_background` logic moved into `UploadAnalysisService.retry_request()`
- [ ] `_recover_pending_reports` logic moved into `UploadAnalysisService.recover_pending()`
- [ ] Controller routes in `service.py` delegate to `UploadAnalysisService` instead of module-level async functions
- [ ] `_mark_upload_child_and_publish` and `_publish_upload_request_status` helpers moved alongside (or remain as thin wrappers if they are genuinely controller-level concerns)
- [ ] All existing upload analysis tests pass
- [ ] New unit tests for `UploadAnalysisService` with fake Store + fake LLM (same pattern as `test_analysis_service.py`)

## Blocked by

- 02-move-upload-state-to-wire-mapping (cleaner extraction after `to_response()` is in UploadRequestService)