# 07: Desktop Bootstrap Integration (Backfill + Reconciliation Wiring)

**What to build:** Wire the legacy document version backfill, publication intent reconciliation, and ApplyReviewService runtime disconnection into the desktop application bootstrap sequence. After this ticket, the application startup order is deterministic and correct: migration → legacy backfill → crash reconciliation → stale-job/schedule recovery → workers/schedulers. ApplyReviewService's canonical Markdown write entry point is disconnected at the composition level.

**Blocked by:** 06: Crash-Consistent Document Publication Service.

**Status:** complete

## Scope

- Modify `DesktopAppContainer.__init__()` to construct `DocumentPublicationService` with its dependencies.
- Modify `DesktopAppContainer.initialize()` to call backfill and reconciliation in the correct order between migration and stale-job recovery.
- Disconnect `ApplyReviewService`'s canonical-write runtime entry point by passing `apply_review_service=None` to `DocumentViewerController` construction.
- Wire `CropArtifactStagingService` into the container.

## Non-Goals

- Implementing the publication service (ticket 06 — already done by this point).
- Implementing the backfill (ticket 05 — already done).
- Migrating Pipeline 1 or MarkdownEditorService (tickets 08, 09).
- Any QML changes.

## Files Likely Impacted

- Modify: `interfaces/desktop/composition.py` — add publication service and staging service construction.
- Modify: `interfaces/desktop/app.py` — change `DocumentViewerController` construction to pass `apply_review_service=None`.
- Create: `tests/unit/test_desktop_bootstrap_integration.py`

## Bootstrap Execution Order (approved)

```python
def initialize(self) -> None:
    # 1. Schema migrations (applies 004)
    self.migration_runner.run_migrations()
    # 2. Legacy document version backfill (idempotent)
    self.document_publication_service.backfill_legacy_document_versions()
    # 3. Reconcile crashed publication intents
    self.document_publication_service.reconcile_startup_intents()
    # 4. Reconcile stale processing jobs and missed schedules
    self.job_recovery_service.reconcile_stale_jobs()
    self.job_recovery_service.reconcile_missed_schedules()
    self._initialized = True
```

## ApplyReviewService Disconnection

In `app.py`, the controller construction changes from:

```python
document_viewer_controller = DocumentViewerController(
    viewer_service=container.document_viewer_service,
    apply_review_service=container.apply_review_service,
)
```

to:

```python
document_viewer_controller = DocumentViewerController(
    viewer_service=container.document_viewer_service,
    apply_review_service=None,  # Canonical write path disabled until 10E.3b
)
```

This is already a supported code path — `DocumentViewerController.__init__` accepts `Optional[ApplyReviewService]` and guards all apply calls on `if not self.apply_review_service: return`.

### What This Preserves

- `ApplyReviewService` source code remains in the repository.
- `DesktopAppContainer` still instantiates `self.apply_review_service` (available for tests and 10E.3b).
- `DocumentViewerController` retains all non-publication functionality (page loading, region selection, zoom, navigation).

### What This Disables

- `_trigger_async_apply()` returns immediately without calling `apply_reviews()`.
- `apply_region_sync()` returns `None` immediately.
- Zero canonical Markdown mutations via `ApplyReviewService` at runtime.

## Architectural Invariants

- Startup order is migration → backfill → publication reconciliation → stale-job recovery → workers.
- `DocumentPublicationService` is the sole active runtime canonical Markdown publisher.
- `ApplyReviewService` source remains, but its canonical-write entry point is not wired to any active controller.

## Acceptance Criteria

- [x] `DocumentPublicationService` is constructed in `DesktopAppContainer.__init__()`.
- [x] `CropArtifactStagingService` is constructed in `DesktopAppContainer.__init__()`.
- [x] `initialize()` calls backfill → reconciliation in correct order between migrations and stale-job recovery.
- [x] `DocumentViewerController` is constructed with `apply_review_service=None`.
- [x] Application starts and initializes cleanly on both fresh and existing databases.
- [x] All existing tests pass. No regressions.

## Tests Required

- `test_bootstrap_order()`: verify initialization calls occur in the documented sequence.
- `test_document_viewer_controller_disconnected()`: verify controller constructed with `apply_review_service=None`.
- `test_bootstrap_fresh_database()`: full initialization on a clean database.
- `test_bootstrap_existing_database()`: full initialization on a database with existing jobs.

## Commit Boundary

Single commit: `feat(desktop): wire backfill and publication reconciliation into application bootstrap`

## Rollback

Reverting restores the original bootstrap sequence and re-enables ApplyReviewService wiring. Migration 004 tables remain in the database but are unused.

## Out of Scope

- Pipeline 1 migration (ticket 08).
- MarkdownEditorService migration (ticket 09).
- Any QML or UI changes.
- Full 10E.3b controller migration.
