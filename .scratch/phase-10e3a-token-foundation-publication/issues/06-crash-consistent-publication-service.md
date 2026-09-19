# 06: Crash-Consistent Document Publication Service

**What to build:** The single authoritative publication gateway (`DocumentPublicationService`) for all canonical Markdown documents, implementing durable intent journaling, atomic filesystem activation, SQLite pointer advancement, artifact manifest verification, and deterministic startup crash reconciliation. After this ticket, there is exactly one code path in the entire application that can create or advance canonical document versions, and it is fully resilient to process crashes at any point in the protocol.

**Blocked by:** 01: Canonical Visual Token Grammar & Immutable Value Objects, 03: Crop Artifact Staging Service, 05: Legacy Document Version Backfill.

**Status:** complete

## Scope

- `DocumentPublicationService` with three public methods: `publish_initial()`, `publish_version()`, `reconcile_startup_intents()`.
- 4-phase crash-consistent publication protocol: Intent Reservation → Staging & Disk Flush → Atomic Disk Activation → Atomic SQLite Pointer Update.
- Full 6-case crash recovery matrix in `reconcile_startup_intents()`.
- OCC validation against `document_versions` (not artifact watermarks).
- Staged artifact manifest integrity verification (SHA-256, byte sizes) during publication and recovery.
- `PublicationInProgressError` when an active intent already exists for a job (no timeout/age heuristic).
- `CanonicalDocumentIntegrityError` for quarantined documents.
- `DocumentAlreadyExistsError` for `publish_initial()` on a job that already has history.
- `DocumentVersionRecord` result type.

## Non-Goals

- Pipeline 2 publication — Pipeline 2 writes to a separate table (`pipeline2_jobs`) with a different artifact type (`PIPELINE2_MARKDOWN`) and is outside this phase.
- Controller or QML changes.
- `ReviewWorkspaceSession`, commands, or merge logic (10E.3b/10E.4).
- Modifying `output_artifact_version_watermark` — the publication service never reads or writes this field.

## Files Likely Impacted

- Create: `application/services/document_publication_service.py`
- Create: `tests/unit/test_document_publication_service.py`
- Create: `tests/unit/test_publication_crash_recovery.py`

## Public Interfaces / Contracts

### `publish_initial(job_id, markdown_text, published_by) -> DocumentVersionRecord`

Preconditions (checked in this order):
1. Job exists.
2. No active publish intent for this job.
3. If `document_versions` row exists with `integrity_status = 'QUARANTINED'`: raise `CanonicalDocumentIntegrityError`.
4. If `document_versions` row exists with `integrity_status = 'VALID'`: raise `DocumentAlreadyExistsError`.
5. If `jobs.output_path` is non-null but no `document_versions` row exists: raise `CanonicalDocumentIntegrityError` (inconsistent state, backfill should have caught this).
6. Empty `markdown_text` (`""`) is legal and produces a valid v1 document.
7. Target version is always 1.
8. Not idempotent — calling twice raises `DocumentAlreadyExistsError`.

### `publish_version(job_id, base_version, markdown_text, staged_crops, published_by) -> DocumentVersionRecord`

- OCC: `base_version` must match `MAX(version) FROM document_versions WHERE job_id = ?`.
- Target version: `base_version + 1`.
- Rejects if active intent exists, if latest version is quarantined, or if base version is stale.

### `reconcile_startup_intents() -> List[RecoveryResult]`

Executed once during application bootstrap. Handles all 6 crash recovery scenarios.

### Concurrency Control

- All publication operations begin with `BEGIN IMMEDIATE`.
- `UNIQUE(job_id)` on `publish_intents` prevents concurrent publishers.
- Active intents are NEVER stolen based on elapsed time. Only startup reconciliation cleans up crashed intents.

### 4-Phase Protocol

1. **Intent Reservation:** `BEGIN IMMEDIATE` → OCC check → compute SHA-256 → build manifest → `INSERT publish_intents` → `COMMIT`.
2. **Staging & Flush:** Write `.tmp` file → copy staged crops to permanent paths → verify manifest checksums → `fsync` → `BEGIN IMMEDIATE` → `UPDATE intent status = 'FLUSHED'` → `COMMIT`.
3. **Atomic Activation:** `os.rename()` of `.tmp` to final canonical path.
4. **Pointer Commit:** `BEGIN IMMEDIATE` → `UPDATE jobs SET output_path` → `INSERT document_versions` → `DELETE publish_intents` → `COMMIT`.

### 6-Case Crash Recovery Matrix

1. PENDING intent, no tmp file → delete intent.
2. PENDING/FLUSHED intent, tmp exists, final absent → unlink tmp, revert promoted crops, delete intent.
3. Final exists + SHA-256 matches + DB at base → **forward-roll** Phase 4.
4. Final exists + SHA-256 mismatch → quarantine file, revert crops, delete intent.
5. Final exists + DB already at target → delete lingering intent.
6. No intents → no-op (idempotent).

### Artifact Recovery Invariant

- Recovery never deletes a pre-existing artifact. If destination exists and SHA-256 matches manifest: treated as already promoted. If destination exists and SHA-256 does NOT match: this is a pre-existing different artifact — do not delete; log error; roll back publication.

### Staging Lifecycle After Publication

- Successful publication: `discard_staging(staging_id)` called to clean up staging directory.
- Publication failure: staging files remain for retry.
- Crash recovery: staging cleaned after reconciliation completes.
- Orphan scan: `.staging/` directories older than 24h with no matching `publish_intents` row are cleaned during reconciliation.

## Architectural Invariants

- `DocumentPublicationService` MUST NOT read or write `output_artifact_version_watermark`.
- `DocumentPublicationService` MUST NOT reference `active_markdown_version`.
- Document version derived solely from `MAX(version) FROM document_versions`.
- `document_versions.sha256` contains real SHA-256 or NULL (quarantined). No sentinels.
- `jobs.output_path` is updated only as an active pointer, not as a version authority.
- Canonical artifact URIs are absolute `file://` URIs scoped to LocalStorageAdapter base tree. Intentionally local-machine scoped; not guaranteed portable across machines.

## Acceptance Criteria

- [x] `publish_initial()` enforces all 6 preconditions in the documented order.
- [x] `publish_version()` enforces OCC against `document_versions`, not artifact watermarks.
- [x] All 4 protocol phases execute in order with explicit `BEGIN IMMEDIATE` transactions.
- [x] `FLUSHED` status transition is durable (explicit SQLite transaction, fsync before).
- [x] `reconcile_startup_intents()` correctly handles all 6 crash scenarios.
- [x] Recovery never deletes pre-existing artifacts (matching SHA-256 treated as already promoted).
- [x] `PublicationInProgressError` raised on concurrent intent, never timeout-based.
- [x] `CanonicalDocumentIntegrityError` raised for quarantined documents.
- [x] Staging cleanup occurs after successful publication and after crash reconciliation.
- [x] Zero references to `output_artifact_version_watermark` or `active_markdown_version` in the service.
- [x] All new tests pass. No existing tests broken.

## Tests Required

- `test_publish_initial_happy_path()`: creates v1, records in document_versions.
- `test_publish_initial_empty_text()`: 0-byte document at v1 is valid.
- `test_publish_initial_already_exists()`: raises DocumentAlreadyExistsError.
- `test_publish_initial_quarantined()`: raises CanonicalDocumentIntegrityError.
- `test_publish_initial_orphan_pointer()`: non-null output_path with no document_versions raises integrity error.
- `test_publish_version_happy_path()`: advances from v1 to v2.
- `test_publish_version_occ_stale()`: raises StaleDocumentVersionError.
- `test_publish_version_concurrent_intent()`: raises PublicationInProgressError.
- `test_crash_recovery_case1_pending_no_file()`.
- `test_crash_recovery_case2_tmp_exists_rollback()`.
- `test_crash_recovery_case3_forward_roll()`.
- `test_crash_recovery_case4_checksum_mismatch()`.
- `test_crash_recovery_case5_already_completed()`.
- `test_crash_recovery_case6_idempotent_no_intents()`.
- `test_recovery_artifact_preexisting_sha_match()`: treated as already promoted.
- `test_recovery_artifact_preexisting_sha_mismatch()`: not deleted, rolled back.
- `test_staging_cleanup_after_publish()`: staging directory removed.
- `test_staging_orphan_scan()`: old staging dirs without intents cleaned.

## Commit Boundary

Single commit: `feat(publication): implement crash-consistent document publication service`

## Rollback

Reverting removes `DocumentPublicationService`. The schema (migration 004) remains. No other code depends on it yet (writer migrations are separate tickets).

## Out of Scope

- Desktop bootstrap wiring (ticket 07).
- Pipeline 1/2 migration (tickets 08).
- MarkdownEditorService migration (ticket 09).
- Controllers, QML, ReviewWorkspaceSession (10E.3b).
