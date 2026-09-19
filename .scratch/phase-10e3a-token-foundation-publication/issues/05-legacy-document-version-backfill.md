# 05: Legacy Document Version Backfill (Python, Idempotent)

**What to build:** An idempotent Python-level backfill routine that inspects all existing jobs with active canonical output pointers, verifies file existence, computes real SHA-256 checksums from disk, and inserts proper `document_versions` rows. Missing/unreadable files are quarantined with `integrity_status = 'QUARANTINED'` and `sha256 = NULL`. After this ticket, every pre-existing job has a deterministic document version history record, and no job can simultaneously have an active output pointer yet be treated as document version 0.

**Blocked by:** 04: SQLite Schema 004 — Tables & Indexes Only.

**Status:** ready-for-agent

## Scope

- Python backfill function `backfill_legacy_document_versions()` on `DocumentPublicationService` (or a dedicated backfill utility).
- For each job with `jobs.output_path IS NOT NULL AND TRIM(output_path) != ''`:
  - Resolve file path from URI.
  - If file exists and is readable (including 0-byte files): compute real SHA-256, derive version from canonical filename via `parse_canonical_markdown_version()`, insert `document_versions` row with `integrity_status = 'VALID'`, `published_by = 'LEGACY_BACKFILL'`.
  - If file is missing, unreadable, or path unresolvable: insert `document_versions` row with `sha256 = NULL`, `integrity_status = 'QUARANTINED'`, `published_by = 'LEGACY_BACKFILL'`. Update `jobs.error_message` with diagnostic.
- `INSERT OR IGNORE` for idempotency — repeated runs never duplicate rows.
- `created_at` uses `job.updated_at` if available, otherwise backfill execution time. Explicitly documented as approximate migration-time metadata, not historical publication timestamps.

## Non-Goals

- Reconstructing intermediate historical versions that were overwritten in-place before the immutable version chain existed.
- Reconstructing exact original publication timestamps.
- Publication service implementation (ticket 06).
- Modifying any existing canonical Markdown files on disk.

## Files Likely Impacted

- Create or extend: backfill function (location determined by implementer — either in publication service or a dedicated utility)
- Create: `tests/unit/test_legacy_document_backfill.py`

## Architectural Invariants

- A job cannot simultaneously have `jobs.output_path != NULL` and be treated as document version 0. The backfill guarantees every such job has a `document_versions` row.
- Empty (0-byte) but readable canonical Markdown files receive their real SHA-256 (`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`) and `integrity_status = 'VALID'`.
- Missing/unreadable files receive `sha256 = NULL` and `integrity_status = 'QUARANTINED'`. No fake/sentinel SHA-256 strings are ever stored.
- Document version is derived from the canonical filename via `parse_canonical_markdown_version()`, never from `output_artifact_version_watermark`.
- Backfill is idempotent: safe to run multiple times.

## Acceptance Criteria

- [ ] All existing jobs with non-null `output_path` and existing readable files receive a `document_versions` row with real SHA-256 and `integrity_status = 'VALID'`.
- [ ] 0-byte readable canonical files are backfilled normally with their real SHA-256.
- [ ] Missing/unreadable canonical files receive `integrity_status = 'QUARANTINED'` and `sha256 = NULL`.
- [ ] Quarantined jobs have `jobs.error_message` updated with a diagnostic string.
- [ ] Running backfill twice produces no duplicate rows (`INSERT OR IGNORE`).
- [ ] Jobs with `output_path IS NULL` are skipped (remain at version 0).
- [ ] Version is derived from `parse_canonical_markdown_version()`, not from artifact watermarks.
- [ ] All new tests pass. No existing tests broken.

## Tests Required

- `test_backfill_existing_job_with_file()`: verify row inserted with real SHA-256.
- `test_backfill_empty_file_valid()`: 0-byte file gets real hash and VALID status.
- `test_backfill_missing_file_quarantined()`: missing file gets NULL sha256 and QUARANTINED.
- `test_backfill_unreadable_file_quarantined()`: permission-denied file quarantined.
- `test_backfill_idempotent()`: second run inserts zero additional rows.
- `test_backfill_null_output_path_skipped()`: jobs without output are not backfilled.
- `test_backfill_version_from_filename()`: version derived from canonical filename pattern.
- `test_backfill_quarantine_sets_error_message()`: verify jobs.error_message updated.

## Commit Boundary

Single commit: `feat(persistence): implement idempotent legacy document version backfill`

## Rollback

Reverting removes the backfill code. Already-inserted `document_versions` rows remain in the database (they are harmless and would need manual SQL deletion if desired).

## Out of Scope

- Publication service (ticket 06).
- Bootstrap wiring of when backfill runs (ticket 07).
- Recovering or repairing quarantined files.
