# 11: Staging Directory Path Alignment & Orphan Purge Reconciliation

**What to build:** Ensure crop artifact staging directories are created strictly under `<artifacts_dir>/.staging/<staging_id>/` rather than directly in the root artifacts folder, enabling `DocumentPublicationService._scan_orphan_staging()` to discover and automatically clean up abandoned staging directories older than 24 hours in production.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

## Scope & Boundary

- In `interfaces/desktop/composition.py:127`, update `CropArtifactStagingService` instantiation in `DesktopAppContainer` to pass `base_dir=artifacts_dir / ".staging"`.
- In `application/services/document_publication_service.py:62`, update the default fallback instantiation to `CropArtifactStagingService(self.artifacts_dir / ".staging")`.
- In `application/services/crop_artifact_staging_service.py`, ensure default or configured base directory resolves to `.staging`, preserving cross-platform path traversal validation and containment checks.
- **Explicit Decision / Accepted Risk:** Staging directories created under the pre-fix path (directly under `artifacts_dir`, not under `artifacts_dir/.staging`) will not be discovered by the corrected orphan scanner. This is an explicitly accepted/deferred risk given that the project has no production deployment yet — not an oversight. No data migration or legacy directory migration script is required for this ticket.

## Non-Goals

- Migrating pre-fix staging directories from `<artifacts_dir>/` to `<artifacts_dir>/.staging/`.
- Changing the staging directory format or naming conventions (`crop_{region_id}_v{version}.jpg`).
- Changing permanent artifact storage layout (`<artifacts_dir>/job_{id}/`).

## Files Likely Impacted

- Modify: `interfaces/desktop/composition.py` — pass `base_dir=artifacts_dir / ".staging"` to `CropArtifactStagingService`.
- Modify: `application/services/document_publication_service.py` — pass `self.artifacts_dir / ".staging"` in default constructor fallback.
- Modify: `application/services/crop_artifact_staging_service.py` — ensure containment logic and base directory defaults align cleanly.
- Modify: `tests/unit/test_crop_artifact_staging_service.py` — verify staging containment under `.staging`.
- Modify: `tests/unit/test_publication_crash_recovery.py` — verify orphan scan cleans directories under `.staging`.

## Acceptance Criteria

- [ ] All staged crops and staging directories are stored strictly under `<artifacts_dir>/.staging/<staging_id>/` (never bare `<artifacts_dir>/<staging_id>/`).
- [ ] `DocumentPublicationService._scan_orphan_staging()` successfully discovers and purges uncommitted staging directories older than 24 hours created by the real `CropArtifactStagingService`.
- [ ] Promotion of staged crops to the permanent job artifact directory (`<artifacts_dir>/job_{id}/...`) during `publish_version()` continues to function cleanly.
- [ ] Pre-fix staging directories directly under `<artifacts_dir>/` are documented as an accepted non-migrated risk with zero runtime crash impact.

## Tests Required

- `test_staging_directory_under_dot_staging_and_orphan_scanner_discovers_it`: Stage a crop using the real `CropArtifactStagingService` wired via `artifacts_dir / ".staging"`, assert that the resulting file path is located within `<artifacts_dir>/.staging/<staging_id>/`, artificially age the staging directory (`st_mtime` > 25 hours), invoke `reconcile_startup_intents()`, and assert that the orphan directory is removed cleanly.
