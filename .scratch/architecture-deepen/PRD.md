# Architecture Deepen — PRD

> Source: `/improve-codebase-architecture` review, 2026-07-13
> Report: `C:\Users\WU\AppData\Local\Temp\architecture-review-20260713-232020.html`

## Background

Architecture review of the controller-service-repository stack identified five deepening candidates. The vocabulary follows the `/codebase-design` skill: **module**, **interface**, **depth**, **seam**, **adapter**, **leverage**, **locality**.

## Candidates

1. **Collapse SessionQueryService into Store** — 100-line pass-through, every method = one Store call + dict→dataclass. Delete it.
2. **Move upload state→wire mapping into UploadRequestService** — 40-line `_upload_request_to_response` shim in the controller masks diverged internal/external model.
3. **Extract UploadAnalysisService from controller** — 200+ lines of background analysis orchestration in the route file.
4. **Split Store by domain aggregate** — 40+ methods in one 2300-line file, deep but unnavigable.
5. **Define PlatformAdapter Protocol** — three adapters, zero shared contract. `typing.Protocol` costs nothing at runtime.

## Decisions

- Issue tracker: GitHub Issues disabled for this repo; using local markdown under `.scratch/`.
- Triage labels: `ready-for-agent` (fully specified, AFK-ready).
- Dependency order: 01, 02, 05 first (no blockers); 03 (blocked by 02); 04 (blocked by 01+02).