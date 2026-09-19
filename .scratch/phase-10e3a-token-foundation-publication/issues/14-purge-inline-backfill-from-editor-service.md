# 14: Purge Inline Backfill Full-Table Scans from MarkdownEditorService

**What to build:** Remove unintended, unindexed full-database backfill scans across all jobs during single-document `load_source_text()` and `commit_source_text()` calls in `MarkdownEditorService`, restoring the architectural boundary where legacy document backfill runs strictly during startup bootstrap.

**Blocked by:** 13: Composition Root Isolation & Quarantine Docstring Precision for ApplyReviewService.

**Status:** ready-for-agent

## Scope & Boundary

- In `application/services/markdown_editor_service.py`:
  - Remove the duplicated inline `from application.services.legacy_document_backfill import backfill_legacy_document_versions` and its global scan from `load_source_text()` and `commit_source_text()`.
  - In `load_source_text()`, read `latest_doc.version` if present; if not present, parse version from `output_path` without mutating the database.
  - In `commit_source_text()`, validate against `latest_doc.version` and delegate directly to `DocumentPublicationService.publish_version()` (or `publish_initial()` if unversioned).
- In `tests/unit/test_markdown_editor_publication.py`: update `test_editor_load_source_text_backfills_legacy_job` to test bootstrap-driven backfill instead of expecting per-request full-table scans.

## Non-Goals

- Removing `backfill_legacy_document_versions()` from the application startup sequence (it remains authoritative in `composition.py:initialize()`).
- Changing optimistic concurrency control semantics or error classes (`StaleDocumentVersionError`, `CanonicalDocumentIntegrityError`).

## Files Likely Impacted

- Modify: `application/services/markdown_editor_service.py` — remove inline backfill calls and imports.
- Modify: `tests/unit/test_markdown_editor_publication.py` — update legacy load test to reflect startup bootstrap backfill.
- Modify: `tests/unit/test_markdown_editor_service.py` — ensure unit tests pass without inline backfill side-effects.

## Acceptance Criteria

- [ ] Confirmed via `composition.py:initialize()` that bootstrap-level backfill runs before the app becomes usable, making the inline per-request calls in `MarkdownEditorService` strictly redundant.
- [ ] `load_source_text()` and `commit_source_text()` never trigger `backfill_legacy_document_versions()` or multi-job database scans.
- [ ] Inline imports of `legacy_document_backfill` are completely removed from `markdown_editor_service.py`.
- [ ] Single-job optimistic concurrency control and version advancement remain fully functional without global scans.
- [ ] Legacy document backfill continues to execute deterministically during startup bootstrap via `DesktopAppContainer.initialize()`.

## Tests Required

- `test_editor_service_does_not_trigger_global_backfill`: Run `load_source_text()` and `commit_source_text()` with a spy/mock on `backfill_legacy_document_versions`, asserting it is called 0 times.
- Existing `test_markdown_editor_publication.py` and `test_markdown_editor_service.py` pass cleanly.
