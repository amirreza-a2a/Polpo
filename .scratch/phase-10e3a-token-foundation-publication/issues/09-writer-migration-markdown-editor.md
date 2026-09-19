# 09: Writer Migration — MarkdownEditorService

**What to build:** Migrate user-initiated Markdown editing in `MarkdownEditorService.commit_source_text()` to delegate canonical document publication to `DocumentPublicationService.publish_version()`. After this ticket, user edits in the Markdown editor advance document versions through the crash-consistent publication protocol, enforcing OCC against `document_versions` and eliminating custom watermark checks and direct storage writes in the editor service.

**Blocked by:** 06: Crash-Consistent Document Publication Service.

**Status:** ready-for-agent

## Scope

- Modify `application/services/markdown_editor_service.py`: replace the custom watermark OCC validation, direct `self.storage.store()`, and direct `job_record.output_path = ...` update inside `commit_source_text()` with a call to `self.document_publication_service.publish_version()`.
- Inject `DocumentPublicationService` into `MarkdownEditorService` constructor.
- Update `DesktopAppContainer` wiring in `interfaces/desktop/composition.py` to pass the publication service to `MarkdownEditorService`.
- Map `StaleDocumentVersionError` from the publication service directly to the existing controller expectations.
- Update `commit_source_text()` return value to return the newly published target document version.

## Non-Goals

- Modifying `MarkdownViewerService` (reading canonical text remains unchanged).
- Modifying `MarkdownEditorController` interface (it already calls `commit_source_text(job_id, text, base_version)`).
- Visual occurrence replacement inside the editor (handled in 10E.3b / 10E.4).
- Modifying `MarkdownMergeService` (diff3 merge belongs to 10E.4).

## Files Likely Impacted

- Modify: `application/services/markdown_editor_service.py`
- Modify: `interfaces/desktop/composition.py`
- Create / Update: `tests/unit/test_markdown_editor_publication.py`

## Public Interfaces / Contracts

In `MarkdownEditorService.commit_source_text()`:
```python
def commit_source_text(self, job_id: int, new_text: str, base_version: int) -> int:
    """
    Commits user-edited Markdown text by delegating to DocumentPublicationService.
    Enforces OCC against document_versions.
    Returns the newly published document version integer.
    """
    record = self.document_publication_service.publish_version(
        job_id=job_id,
        base_version=base_version,
        markdown_text=new_text,
        staged_crops=[],  # Manual text edits do not stage new visual crops
        published_by="MARKDOWN_EDITOR",
    )
    return record.version
```

## Architectural Invariants

- Document version advancement during manual editing is managed exclusively by `DocumentPublicationService`.
- OCC is checked against `document_versions` in SQLite with `BEGIN IMMEDIATE`, not against artifact watermarks or filename parsing.
- `output_artifact_version_watermark` is not used for OCC or target version calculation.
- `jobs.output_path` is updated by the publication service, not by the editor service directly.

## Acceptance Criteria

- [ ] `commit_source_text()` delegates publication to `DocumentPublicationService.publish_version()`.
- [ ] Successful edit advances document version ($v \to v+1$) and records a row in `document_versions`.
- [ ] Concurrent modification raises `StaleDocumentVersionError` when `base_version` does not match the latest `document_versions` row.
- [ ] Quarantined documents raise `CanonicalDocumentIntegrityError`.
- [ ] In-flight publication raises `PublicationInProgressError`.
- [ ] Direct calls to `self.storage.store()` and direct SQLite updates in `commit_source_text()` are removed.
- [ ] All existing editor service tests pass or are updated to reflect publication service delegation.

## Tests Required

- `test_editor_commit_publishes_new_version()`: verify `publish_version()` called and returns incremented version.
- `test_editor_commit_stale_version_rejected()`: verify `StaleDocumentVersionError` propagated when base_version is stale.
- `test_editor_commit_creates_document_versions_row()`: verify new version row in `document_versions`.
- `test_editor_commit_quarantined_rejected()`: verify `CanonicalDocumentIntegrityError` when attempting to edit a quarantined job.

## Commit Boundary

Single commit: `refactor(editor): migrate MarkdownEditorService to publication authority`

## Rollback

Reverting restores the previous custom OCC and direct storage write implementation in `MarkdownEditorService`.

## Out of Scope

- ReviewWorkspaceSession or visual recrop integration (10E.3b).
- Component-aware semantic merge (10E.4).
- Modern prompt templates (10E.5).
