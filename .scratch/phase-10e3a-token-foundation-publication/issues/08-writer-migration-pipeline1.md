# 08: Writer Migration — Pipeline 1 Initial Document

**What to build:** Migrate Pipeline 1 AI document execution in `JobExecutionService` to publish its initial canonical Markdown output through `DocumentPublicationService.publish_initial()`. After this ticket, initial document creation from completed PDF conversions is routed through the single authoritative publication gateway, creating immutable `document_versions` records and eliminating direct filesystem output storage in Pipeline 1.

**Blocked by:** 06: Crash-Consistent Document Publication Service.

**Status:** ready-for-agent

## Scope

- Modify `application/services/job_execution.py`: replace the direct `self.storage.store()` and `job.output_path = ...` block (lines ~300-345) in `execute_next_job()` with a call to `self.document_publication_service.publish_initial()`.
- Inject `DocumentPublicationService` into `JobExecutionService` via its constructor.
- Update `DesktopAppContainer` wiring in `interfaces/desktop/composition.py` to pass the publication service to `JobExecutionService`.
- Preserve auto-Pipeline 2 trigger semantics (`job.auto_pipeline2` scheduling `Pipeline2Job` with input set to the published canonical URI).

## Non-Goals

- Migrating Pipeline 2 — Pipeline 2 writes to the separate `pipeline2_jobs` table with artifact type `PIPELINE2_MARKDOWN` and is intentionally excluded from the canonical document publication authority in Phase 10E.3a.
- Modifying `MarkdownEditorService` (ticket 09).
- Modifying `ApplyReviewService` (quarantined and disconnected at runtime in ticket 07).

## Files Likely Impacted

- Modify: `application/services/job_execution.py`
- Modify: `interfaces/desktop/composition.py`
- Create / Update: `tests/unit/test_job_execution_publication.py`

## Public Interfaces / Contracts

In `JobExecutionService`:
```python
# Instead of:
# output_handle = self.storage.store(...)
# job.output_path = output_handle.uri
# job.output_artifact_version_watermark = 1
# uow.jobs.update_progress(...)

# Use:
record = self.document_publication_service.publish_initial(
    job_id=job.id,
    markdown_text=full_document,
    published_by="PIPELINE_1_EXECUTION",
)
job.output_path = record.output_path
# output_artifact_version_watermark is NOT touched as a document version
```

## Architectural Invariants

- Initial document publication creates document version 1 in `document_versions` through `publish_initial()`.
- Pipeline 1 does NOT directly call `self.storage.store()` for `ArtifactType.OUTPUT_MARKDOWN`.
- `output_artifact_version_watermark` is not used or written as a document-version source.
- Pipeline 2 execution path in `execute_next_pipeline2_job()` remains untouched and continues using `pipeline2_jobs` / `PIPELINE2_MARKDOWN`.

## Acceptance Criteria

- [ ] Pipeline 1 calls `DocumentPublicationService.publish_initial()` upon completing document conversion.
- [ ] Completed job has `jobs.output_path` set to the canonical published URI.
- [ ] `document_versions` has a row with `version = 1`, `integrity_status = 'VALID'`, and real SHA-256 matching the file.
- [ ] Auto-Pipeline 2 trigger correctly picks up the published URI as `input_path`.
- [ ] Cancellation checks before publication remain intact.
- [ ] Pipeline 2 execution logic is not modified.
- [ ] All existing pipeline execution tests pass or are updated to reflect the publication service delegation.

## Tests Required

- `test_pipeline1_publishes_initial_document()`: verify `publish_initial()` is called and produces version 1 in `document_versions`.
- `test_pipeline1_auto_pipeline2_receives_published_uri()`: verify downstream Pipeline 2 receives valid input path.
- `test_pipeline1_cancellation_aborts_before_publication()`: cancelled job does not call `publish_initial()`.
- `test_pipeline2_unmodified_direct_execution()`: verify Pipeline 2 execution still operates independently.

## Commit Boundary

Single commit: `refactor(pipelines): migrate Pipeline 1 initial document creation to publication authority`

## Rollback

Reverting restores direct `storage.store()` and direct SQLite progress update in `JobExecutionService`.

## Out of Scope

- Pipeline 2 canonical publication (remains separate).
- MarkdownEditorService migration (ticket 09).
- ReviewWorkspace or visual recrop workflows.
