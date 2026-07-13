# 02 — Move upload state-to-wire mapping into UploadRequestService

Status: resolved

## What to build

The 40-line `_upload_request_to_response` function in `service.py` is a compatibility shim that maps the internal dual-axis upload state machine (transfer_status + analysis_status) to a legacy single-axis view for API responses. This function lives in the controller but really belongs in the upload service.

Move `_upload_request_to_response` into `UploadRequestService` as `to_response(row)`. All upload request routes call `upload_svc.to_response(row)` instead of importing a controller-level helper. The dual-axis state model (transfer + analysis) becomes the authoritative API shape.

## Acceptance criteria

- [ ] `_upload_request_to_response` is removed from `service.py`
- [ ] `UploadRequestService.to_response(row)` returns the same wire-ready dict shape
- [ ] All controller routes that construct upload responses use `upload_svc.to_response()` instead of the module-level helper
- [ ] `_publish_upload_request_status` in `service.py` uses `upload_svc.to_response()` instead
- [ ] All existing upload request tests pass unchanged
- [ ] No new imports of upload-related helpers from `service.py` into controllers

## Blocked by

None - can start immediately