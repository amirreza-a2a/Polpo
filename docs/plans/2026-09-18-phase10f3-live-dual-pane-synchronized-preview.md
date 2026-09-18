# Phase 10F.3 — Live Dual-Pane Synchronized Preview Architecture Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement real-time, asynchronous live preview updates and a dual-pane side-by-side editing view in the desktop Review Workspace without compromising the local-first single-user OCC persistence model, without blocking the Qt GUI thread, and with zero leakage between transient draft states and canonical document artifacts.

**Architecture:** Presentation-driven synchronization using a debounced timer (250ms) in `MarkdownViewerController` connected to editor buffer changes, executing pure in-memory AST projection via `MarkdownViewerService.render_preview()` on a background worker thread. State separation guarantees that transient preview rendering never mutates canonical versions, never executes SQLite writes, never touches disk artifacts, and never emits signals that could trigger false OCC conflicts. A nested `SplitView` in `ReviewWorkspaceView.qml` coordinates seamless switching between Rendered Preview (Tab 0), Source Editor (Tab 1), and Dual-Pane Side-by-Side (Tab 2) with interactive splitter resizing. Under external canonical document advance while the editor is dirty, the transient draft preview model is strictly preserved, active canonical version metadata advances, and the editor flags an OCC conflict without clobbering uncommitted draft work.

**Tech Stack:** Python 3.12, PySide6 / PyQt6 (via `interfaces/desktop/qt_compat.py`), Qt Quick Controls 2 (`SplitView`, `TextArea`, `ListView`), SQLite (read-only UoW during preview rendering).

**Spec:** `docs/plans/2026-09-18-phase10f3-live-dual-pane-synchronized-preview.md`

## Global Constraints

- **Strict Clean Architecture:** Presentation code (`interfaces/desktop/`) communicates with business logic exclusively through application services (`application/services/`). No direct SQLite or storage access from controllers.
- **Local-First, Serverless:** No FastAPI, no HTTP daemon, no loopback server, no remote dependencies. Outbound HTTPS for AI providers only.
- **OCC Integrity:** Canonical versions advance only via `MarkdownEditorService.commit_source_text()`. Transient live preview must NEVER mutate `MarkdownViewerController._active_version` or emit `activeVersionChanged`.
- **Zero Secret Leakage:** No plaintext credentials or sensitive tokens in models, DTOs, or logs.
- **Cooperative Cancellation & Race Freedom:** Monotonic typing tokens (`_draft_revision`), job-scoped generation matching, and strict latest revision matching prevent stale renders from overwriting newer user edits.
- **GUI Responsiveness:** All document parsing, AST generation, and image region resolution execute on background worker threads; GUI thread only applies visual updates.

---

## 1. Strict Boundary: Canonical State vs Transient Preview State

Phase 10F.3 establishes an absolute architectural partition between **Canonical Viewer State** and **Transient Live Preview State**.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        CANONICAL VIEWER STATE                          │
│ - Immutable disk artifact: output_{job_id}_v{version}.md               │
│ - SQLite database record: jobs.output_path & active_markdown_version   │
│ - Controller property: MarkdownViewerController.activeVersion          │
│ - Version Badge in QML: "v1", "v2"                                     │
│ - Save / Discard / OCC Authority                                       │
│ - Mutated ONLY by: loadDocument(), reload(), on_editor_saved()         │
└────────────────────────────────────────────────────────────────────────┘
                                   ▲
                                   │ STRICT SEPARATION
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     TRANSIENT LIVE PREVIEW STATE                       │
│ - In-memory source buffer: MarkdownEditorController._source_text       │
│ - Ephemeral AST projection: MarkdownDocumentDTO (version = base_ver)   │
│ - Controller properties: previewErrorMessage, hasPreviewError          │
│ - Revision token: draft_revision: int (monotonically increasing)       │
│ - Active draft flag: _has_active_draft: bool                           │
│ - Visual Model: MarkdownDocumentModel item list & layout roles         │
│ - ZERO database writes, ZERO disk writes, ZERO canonical reservations  │
│ - Mutated ONLY by: scheduleLivePreview() -> _on_internal_preview_loaded│
└────────────────────────────────────────────────────────────────────────┘
```

### 1.1 Field & Resource Permission Matrix

| Field / Property / Resource | Permitted to Mutate During Live Preview? | Authority / Scope | Concrete Failure If Mutated |
| :--- | :--- | :--- | :--- |
| `MarkdownViewerController.activeVersion` | **FORBIDDEN** | Canonical Viewer State | Emits `activeVersionChanged`, causing `MarkdownEditorController.notifyCanonicalDocumentAdvance` to flag a false OCC conflict (`hasConflict = True`) on a dirty editor buffer, disabling Save! |
| `MarkdownViewerController.activeVersionChanged` | **FORBIDDEN** | Presentation Signal | Triggers false external advance handling in `wire_review_workspace_sync`. |
| `jobs.output_path` (SQLite) | **FORBIDDEN** | Persistence Layer | Corrupts canonical job record with uncommitted draft path; violates crash recovery. |
| `jobs.active_markdown_version` (SQLite) | **FORBIDDEN** | Persistence Layer | Desynchronizes database version from committed artifact on disk. |
| `output_{job_id}_v{N}.md` (Disk Storage) | **FORBIDDEN** | Immutable Artifact Storage | Creates orphan disk files on every keystroke; exhausts disk space. |
| `MarkdownEditorService.commit_source_text()` | **FORBIDDEN** | Application Persistence Port | Reserves a new canonical version number prematurely; violates OCC contract. |
| `ApplyReviewService` | **FORBIDDEN** | Canonical Review Port | Advances review lifecycle before user explicitly confirms save. |
| `MarkdownEditorController._source_text` | **FORBIDDEN** | Editor Private Buffer | Clobbers user's live typing buffer and destroys cursor position. |
| `MarkdownDocumentModel._items` | **PERMITTED** | Presentation QML ListView | Expected: Updates rendered AST nodes for QML delegate rendering. |
| `MarkdownDocumentModel._region_to_*` | **PERMITTED** | Presentation Indices | Expected: Rebuilds visual region lookup indices for click/scroll targeting. |
| `MarkdownViewerController._draft_revision` | **PERMITTED** | Transient State Token | Expected: Tracks latest typing revision for stale result rejection. |
| `MarkdownViewerController.previewErrorMessage` | **PERMITTED** | Presentation Error Property | Expected: Surfaces transient Markdown syntax or parsing errors to the UI. |
| `MarkdownViewerController.hasPreviewError` | **PERMITTED** | Presentation Error Property | Expected: Drives visibility of the inline preview warning banner. |

### 1.2 Verification of `MarkdownDocumentModel`
- `MarkdownDocumentModel` inherits from `QAbstractListModel`.
- It defines roles strictly for block presentation (`NodeIdRole`, `NodeTypeRole`, `ContentRole`, `SegmentsRole`, `RegionsRole`, `ImageUriRole`, etc.).
- It contains **zero** properties or roles representing document version, file path, or database identifiers. QML delegates read only block item roles.
- In `reconcile_document(document_dto)`: `document_dto.version` is completely ignored. Only `document_dto.nodes` and `document_dto.region_to_occurrences` are read to update `_items` and the region lookup dicts.
- `apply_transient_preview(document_dto)` invokes `reconcile_document(document_dto)` without touching any controller property or emitting any version advance signals.

---

## 2. Preview Model State Machine

`MarkdownViewerController` manages preview lifecycle via a deterministic 7-state machine:

```
                          ┌───────────────────────────┐
                          │   STATE_CANONICAL_PREVIEW │
                          └─────────────┬─────────────┘
                                        │
                         Typing in Editor Buffer (debounced)
                                        │
                                        ▼
                          ┌───────────────────────────┐
             ┌───────────►│ STATE_DRAFT_PREVIEW_PENDING│
             │            └─────────────┬─────────────┘
             │                          │
        New Keystroke             Render Task Finished
             │                          │
             │             ┌────────────┴────────────┐
             │             │                         │
             │       Parse Success             Parse Error
             │             │                         │
             │             ▼                         ▼
             │  ┌──────────────────────────┐ ┌──────────────────────────┐
             └──┤STATE_DRAFT_PREVIEW_RENDER│ │STATE_DRAFT_PREVIEW_FAILED│
                └──────────┬───────┬───────┘ └──────────┬───────┬───────┘
                           │       │                    │       │
              Save Success │       │ External Advance   │       │ External Advance
              or Discard   │       │ (Editor Dirty)     │       │ (Editor Dirty)
                           │       ▼                    │       ▼
                           │  ┌─────────────────────────┴─────────────┐
                           │  │      STATE_DRAFT_PREVIEW_CONFLICT     │
                           │  └───────────────────┬───────────────────┘
                           │                      │ Discard Click
                           ▼                      ▼
                ┌───────────────────────────────────────┐
                │     STATE_CANONICAL_RELOAD_PENDING    │
                └───────────────────┬───────────────────┘
                                    │ Job Switch
                                    ▼
                ┌───────────────────────────────────────┐
                │          STATE_JOB_SWITCHED           │
                └───────────────────────────────────────┘
```

### 2.1 State Definitions
1. **`STATE_CANONICAL_PREVIEW`:** Canonical document is loaded from disk and displayed in the model. Editor buffer is clean or unedited. `hasPreviewError = False`.
2. **`STATE_DRAFT_PREVIEW_PENDING`:** User has edited text. Debounce timer is running or background render worker is executing. Previous preview model remains visible.
3. **`STATE_DRAFT_PREVIEW_RENDERED`:** Background render worker completed successfully for the latest typed draft revision. In-memory AST projection is applied to `MarkdownDocumentModel`. `hasPreviewError = False`.
4. **`STATE_DRAFT_PREVIEW_FAILED`:** Background render worker encountered an error (malformed syntax or exception). The previous valid `MarkdownDocumentModel` is preserved (never blanked). `hasPreviewError = True`, `previewErrorMessage` is populated, and an inline warning banner is displayed.
5. **`STATE_DRAFT_PREVIEW_CONFLICT`:** Canonical document storage was advanced externally ($N \to N+1$) while the editor was dirty. `viewer.activeVersion` is $N+1$; `editor.hasConflict` is True. `MarkdownDocumentModel` continues displaying the draft preview (never overwritten by canonical content); Save is blocked.
6. **`STATE_CANONICAL_RELOAD_PENDING`:** An explicit Save succeeded, Discard was clicked, or clean external canonical document advance occurred. An asynchronous load of the canonical artifact is in flight.
7. **`STATE_JOB_SWITCHED`:** Active job ID changed. All pending debounce timers and in-flight tasks are cancelled. `MarkdownDocumentModel` is cleared.

### 2.2 State Transition Table

| Current State | Event | Condition | Next State | Actions Taken |
| :--- | :--- | :--- | :--- | :--- |
| `CANONICAL_PREVIEW` | User types in editor | `job_id > 0` | `DRAFT_PREVIEW_PENDING` | `_draft_revision += 1`, `_has_active_draft = True`, start/restart `_live_preview_timer` (250ms). |
| `DRAFT_PREVIEW_PENDING` | User continues typing | `job_id > 0` | `DRAFT_PREVIEW_PENDING` | `_draft_revision += 1`, restart `_live_preview_timer` (250ms). |
| `DRAFT_PREVIEW_PENDING` | Debounce timer fires | Pending text valid | `DRAFT_PREVIEW_PENDING` | Dispatch background worker task with `(job_id, draft_rev, text, base_ver)`. |
| `DRAFT_PREVIEW_PENDING` | Worker completes (success) | `job_id == _active_job_id` AND `draft_rev == _draft_revision` | `DRAFT_PREVIEW_RENDERED` | Apply transient preview to model; `hasPreviewError = False`, `previewErrorMessage = ""`. |
| `DRAFT_PREVIEW_PENDING` | Worker completes (error) | `job_id == _active_job_id` AND `draft_rev == _draft_revision` | `DRAFT_PREVIEW_FAILED` | Retain previous model; `hasPreviewError = True`, `previewErrorMessage = error_str`. |
| `DRAFT_PREVIEW_PENDING` | Worker completes (stale) | `draft_rev != _draft_revision` OR `job_id != _active_job_id` | `DRAFT_PREVIEW_PENDING` | Drop result immediately (no model update, no signal emission). |
| `DRAFT_PREVIEW_FAILED` | User continues typing | `job_id > 0` | `DRAFT_PREVIEW_PENDING` | `_draft_revision += 1`, restart `_live_preview_timer` (250ms). |
| `DRAFT_PREVIEW_RENDERED` or `PENDING` | External canonical advance | `isDirty == True` | `DRAFT_PREVIEW_CONFLICT` | Update `_active_version = N+1`, emit `activeVersionChanged`. Editor flags `hasConflict = True`. Model update is SKIPPED; draft preview is PRESERVED. |
| `CANONICAL_PREVIEW` | External canonical advance | `isDirty == False` | `CANONICAL_RELOAD_PENDING` | Dispatch `loadDocument(job_id)`. Updates `_active_version`, reloads canonical model, editor reloads source text. |
| `DRAFT_PREVIEW_CONFLICT`| User continues typing | `job_id > 0` | `DRAFT_PREVIEW_PENDING` | `_draft_revision += 1`, restart debounce timer. Conflict flag remains active. |
| `DRAFT_PREVIEW_CONFLICT`| User clicks Save | `hasConflict == True` | `DRAFT_PREVIEW_CONFLICT` | Save blocked in editor; conflict error displayed; draft text and draft preview preserved. |
| `DRAFT_PREVIEW_CONFLICT`| User clicks Discard | User confirms discard | `CANONICAL_RELOAD_PENDING` | Editor clears dirty/conflict; editor reloads canonical source $N+1$; viewer loads canonical artifact $N+1$. |
| Any Draft State | User clicks Save (success) | `saved(N+1)` emitted | `CANONICAL_RELOAD_PENDING` | Stop debounce timer; `_has_active_draft = False`; dispatch canonical `loadDocument(job_id)`. |
| Any Draft State | User clicks Save (failure) | OCC conflict or I/O error | Unchanged (`DRAFT_PREVIEW_RENDERED` or `FAILED`) | Editor enters conflict/error state; draft text and preview model remain intact. |
| Any Draft State | User clicks Discard | `discarded` emitted | `CANONICAL_RELOAD_PENDING` | Stop debounce timer; `_has_active_draft = False`; `_draft_revision += 1`; dispatch immediate render of saved text. |
| Any State | User switches Job | `activeJobId` changes | `JOB_SWITCHED` | Stop debounce timer; `_has_active_draft = False`; `_request_id += 1`; clear model; load new job. |
| `CANONICAL_RELOAD_PENDING`| Canonical load completes | `req_id == _request_id` | `CANONICAL_PREVIEW` | Update `_active_version`, set canonical model, emit `activeVersionChanged`. |

### 2.3 Universal State Invariants
1. **Model Non-Destructive Invariant:** No transient preview failure, stale task, or external canonical advance may ever reset `MarkdownDocumentModel` to empty while the editor is dirty.
2. **Version Badge Invariant:** `MarkdownViewerController.activeVersion` reflects ONLY the committed canonical version. It NEVER changes in states `DRAFT_PREVIEW_PENDING`, `DRAFT_PREVIEW_RENDERED`, or `DRAFT_PREVIEW_FAILED`.
3. **OCC Isolation Invariant:** `activeVersionChanged` is NEVER emitted during live preview rendering.

---

## 3. Stale Result Resolution: Strict Latest Matching (Option B)

### 3.1 Analysis of Option A vs Option B

- **Option A: Monotonic Acceptance (`draft_revision >= _last_applied_draft_revision`)**
  - Scenario: User types Revision 5. Timer fires, dispatching slow background task for Revision 5. User immediately types Revisions 6, 7, 8. Revision 8 is pending.
  - If Revision 5 finishes before Revision 8 dispatches: Under Option A, `5 >= 0` is True, so Revision 5 is accepted and rendered.
  - Defect: The editor shows Revision 8 text, but the preview briefly flashes obsolete Revision 5 content, causing a visible display back-glitch before Revision 8 finally renders.
- **Option B: Strict Latest Matching (`draft_revision == self._draft_revision`)**
  - Scenario: In the same case, when Revision 5 completes, `draft_revision (5) != self._draft_revision (8)`.
  - Action: The result is dropped immediately without updating the model.
  - Result: Zero intermediate obsolete flicker. The preview updates directly from the previous state to Revision 8 once ready.

### 3.2 Chosen Specification: Option B
```python
@Slot(int, int, object)
def _on_internal_preview_loaded(self, job_id: int, draft_revision: int, document_dto) -> None:
    if self._is_shutdown:
        return
    if job_id != self._active_job_id:
        return  # Job mismatch (user switched jobs)
    if draft_revision != self._draft_revision:
        return  # Strict latest matching: newer revision already typed, drop obsolete result

    # Accepted: apply pure presentation update
    self._last_applied_draft_revision = draft_revision
    self._model.apply_transient_preview(document_dto)
    self._has_document = True
    self._has_preview_error = False
    self._preview_error_message = ""
    self.hasPreviewErrorChanged.emit()
    self.previewErrorChanged.emit()
    self.documentChanged.emit()
```

---

## 4. Comprehensive Revision Lifecycle Specifications

### 4.1 Typing -> Debounce -> Dispatch -> Completion
1. User types a keystroke in `sourceTextArea`.
2. `MarkdownEditorController.set_source_text()` updates `_source_text` and emits `sourceTextChanged`.
3. `wire_review_workspace_sync` invokes `MarkdownViewerController.scheduleLivePreview(job_id, text, base_version)`.
4. In `scheduleLivePreview`:
   - `self._draft_revision += 1`
   - `self._has_active_draft = True`
   - Stores `_pending_preview_text = text`, `_pending_preview_job_id = job_id`, `_pending_preview_base_version = base_version`.
   - Restarts `_live_preview_timer` with 250ms timeout.
5. While user keeps typing within 250ms, timer restarts repeatedly; zero worker tasks are spawned.
6. When user pauses for 250ms, `_live_preview_timer` fires:
   - Captures snapshot: `rev_id = self._draft_revision`, `job_id = self._pending_preview_job_id`.
   - Submits `_task` to `_executor`.
7. Worker executes `MarkdownViewerService.render_preview(job_id, text, base_version)` inside a read-only Unit of Work.
8. Worker emits `_internalPreviewLoaded.emit(job_id, rev_id, dto)` across the Qt thread boundary.
9. GUI thread receives signal in `_on_internal_preview_loaded`:
   - Verifies `job_id == self._active_job_id`, `rev_id == self._draft_revision`.
   - Calls `self._model.apply_transient_preview(dto)`.
   - Preview updates smoothly in QML.

### 4.2 External Canonical Advance with Dirty Editor Draft
1. Canonical storage advances externally from Version $N$ to Version $N+1$ (e.g. background processing, external tool, or region recrop).
2. `MarkdownViewerController` canonical reload path executes (e.g. `loadDocument(job_id)` or `_sync_document_structure(job_id)`).
3. In `_on_internal_doc_loaded` (or `_on_internal_reconcile_loaded`):
   - Sets `self._active_version = N+1`.
   - Emits `self.activeVersionChanged.emit()`.
   - Checks `self._has_active_draft`:
     - Because `_has_active_draft` is True, `self._model.set_document(document_dto)` is **SKIPPED**!
     - The visible draft preview in `self._model` is strictly **PRESERVED**!
     - In-flight draft preview tasks are **NOT** invalidated!
4. In `wire_review_workspace_sync`:
   - `_on_viewer_version_changed` invokes `markdown_editor_controller.notifyCanonicalDocumentAdvance(N+1)`.
   - Because `isDirty` is True, `MarkdownEditorController` enters conflict state: `hasConflict = True`, `conflictMessage = ...`.
   - Save button is disabled; conflict banner is displayed.
5. User continues editing:
   - New keystrokes continue updating `_source_text` and triggering `scheduleLivePreview`.
   - Render tasks execute against `base_version = N` and update `self._model`.
   - The user never loses uncommitted work.

### 4.3 Save Click (Save Success)
1. User clicks "Save" or presses `Ctrl+S`.
2. `MarkdownEditorController.save()` begins:
   - Sets `isSaving = True`.
   - Calls `MarkdownEditorService.commit_source_text(job_id, text, base_version)`.
3. Application service reserves next version $N+1$, writes `output_{job_id}_v{N+1}.md`, updates SQLite `jobs.output_path` and `jobs.active_markdown_version = N+1`.
4. `MarkdownEditorController._on_internal_saved`:
   - Updates `_saved_source_text = text`, `_active_version = N+1`, `isDirty = False`, `isSaving = False`.
   - Emits `saved(N+1)`.
5. In `wire_review_workspace_sync`, `_on_editor_saved` calls `markdown_viewer_controller.loadDocument(job_id)`.
6. In `loadDocument(job_id)`:
   - `_live_preview_timer.stop()` (cancels any pending debounce).
   - `self._has_active_draft = False`.
   - `self._request_id += 1`.
   - Sets `_is_loading = True`.
   - Submits background load of canonical artifact $N+1$.
7. Canonical loader completes:
   - Sets `self._active_version = N+1`.
   - Because `_has_active_draft` is False, calls `self._model.set_document(dto)`.
   - Emits `activeVersionChanged`, `documentChanged`.
8. In `wire_review_workspace_sync`, `_on_viewer_version_changed` calls `markdown_editor_controller.notifyCanonicalDocumentAdvance(N+1)`.
   - Because editor `_active_version` is already $N+1$ and `isDirty` is False, no conflict is triggered.
9. No visual flicker occurs because the rendered AST of the committed artifact is identical to the active draft preview.

### 4.4 Save Click (Save Failure - OCC Conflict or Disk Error)
1. User clicks "Save".
2. If `hasConflict` is True: `MarkdownEditorController.save()` blocks immediately and sets error message.
3. If `commit_source_text()` raises `StaleDocumentVersionError` (conflict) or `IOError` (disk error):
   - `MarkdownEditorController._on_internal_save_error` sets `isSaving = False`.
   - If conflict: sets `hasConflict = True`, `conflictMessage = ...`, emits `conflictChanged`.
   - If error: sets `errorMessage = ...`, emits `errorChanged`.
   - `saved` signal is **NOT** emitted.
4. `markdown_viewer_controller.loadDocument()` is **NOT** called.
5. In `MarkdownViewerController`:
   - State remains in `STATE_DRAFT_PREVIEW_CONFLICT` or `STATE_DRAFT_PREVIEW_RENDERED`.
   - Pending debounce timer and in-flight draft preview tasks are unaffected.
6. The editor draft text remains in `_source_text`, and the live preview model remains visible and intact. Draft work is never lost.

### 4.5 Discard Click (Including After External Advance)
1. User clicks "Discard".
2. `MarkdownEditorController.discard()`:
   - Restores `_source_text = _saved_source_text`.
   - Resets `isDirty = False`, `hasConflict = False`.
   - Emits `sourceTextChanged`, `dirtyChanged`, `conflictChanged`, `discarded`.
3. In `wire_review_workspace_sync`:
   - `_on_editor_discarded` checks if `markdown_viewer_controller.activeVersion > markdown_editor_controller.activeVersion`:
     - If True (an external advance occurred while dirty): calls `markdown_editor_controller.loadSource(job_id)` to load the new canonical source $N+1$ from storage, updating editor `_active_version = N+1`.
     - Calls `markdown_viewer_controller.loadDocument(job_id)` to reload canonical artifact $N+1$ into the preview model.
     - Sets `_has_active_draft = False`.
   - If False (normal discard without external advance):
     - Calls `markdown_viewer_controller.cancelPendingLivePreviewAndReconcile(job_id, saved_text, base_version)`.
4. In `cancelPendingLivePreviewAndReconcile()`:
   - `_live_preview_timer.stop()` (aborts pending debounce).
   - `self._has_active_draft = False`.
   - `self._draft_revision += 1` (invalidates in-flight dirty draft tasks).
   - Dispatches immediate render task for `saved_text` under the new `_draft_revision`.
5. Any slow dirty preview worker completing after discard has `draft_rev != self._draft_revision` and is dropped.
6. The clean preview renders and updates the model immediately.

### 4.6 Job Switching
1. User switches from Job 10 to Job 11 in Queue/History.
2. `loadDocument(11)` and `loadSource(11)` are invoked.
3. In `MarkdownViewerController.loadDocument(11)`:
   - `_live_preview_timer.stop()`.
   - `self._has_active_draft = False`.
   - `self._request_id += 1`.
   - `self._active_job_id = 11`.
   - `self._model.set_document(None)`.
4. Any in-flight preview from Job 10 finishes:
   - `job_id (10) != self._active_job_id (11)` -> Dropped.
   - Zero state leaks across jobs.

### 4.7 Controller Shutdown
1. Window closes or application terminates: `MarkdownViewerController.shutdown()` is called.
2. `_is_shutdown = True`.
3. `_live_preview_timer.stop()`.
4. `self._has_active_draft = False`.
5. `self._request_id += 1`.
6. `self._executor.shutdown(wait=False, cancel_futures=True)`.
7. Signals are safely disconnected; zero dangling background threads.

---

## 5. Base Version Semantics Proof

### 5.1 Why `base_version` Is Needed
`MarkdownViewerService.render_preview(job_id, raw_text, base_version)` accepts `base_version` because:
1. When Markdown text contains image references, visual region resolution (`resolve_image_regions()`) locates bounding boxes and raster crops associated with the visual regions of the job.
2. `MarkdownDocumentDTO` is constructed with `version=base_version` to preserve the editor's base version identity.

### 5.2 Determinism Under External Canonical Advance
- When canonical storage advances externally from Version $N$ to $N+1$, `markdown_editor_controller.activeVersion` remains $N$ while dirty.
- In `visual_regions` table (`002_visual_regions.sql`), regions are identified by immutable canonical UUIDs (`region_id`), which are persistent across Markdown text revisions.
- Visual region tokens in the draft text (`region_id="<uuid>"`) resolve correctly against the job's active regions.
- `render_preview` creates a pure in-memory `MarkdownDocumentDTO` with `version = N`.
- The draft preview continues rendering stably without requiring database migration or altering canonical version records.

### 5.3 Proof: Zero OCC / SQLite Mutation
```python
def render_preview(
    self,
    job_id: int,
    raw_text: str,
    base_version: int = 1,
) -> MarkdownDocumentDTO:
    with self.uow_factory.create() as uow:
        job = uow.jobs.get_by_id(job_id)
        if not job:
            raise EntityNotFoundError("Job", job_id)
        active_regions = uow.visual_regions.get_by_job_id(job_id)
        output_path = job.output_path

    clean_path = output_path[7:] if (output_path and output_path.startswith("file://")) else output_path
    base_dir = os.path.dirname(clean_path) if clean_path else None

    return self.render_text(
        raw_text=raw_text,
        active_regions=active_regions,
        job_id=job_id,
        version=base_version,
        base_dir=base_dir,
    )
```
- The Unit of Work performs exactly two read queries (`jobs.get_by_id` and `visual_regions.get_by_job_id`).
- `uow.commit()` is never called; no SQL write statements are executed.
- Neither `ApplyReviewService` nor `MarkdownEditorService.commit_source_text()` is invoked.
- `jobs.output_path` and `jobs.active_markdown_version` are completely unmutated.
- Neither QML nor `_on_internal_preview_loaded` reads `documentDTO.version` to alter `_active_version`.

---

## 6. Verified QML SplitView Layout & Contract

### 6.1 Audit of Qt Quick Controls 2 `SplitView`
Testing offscreen in PyQt6 demonstrated that:
1. If multiple child items have `SplitView.fillWidth: true`, Qt gives **100% of the extra width to the first child**, starving subsequent children to width 0.
2. Declarative bindings on `visible` and `preferredWidth` alone do not reliably force `SplitView` to recalculate its internal item geometry when toggling between tabs.
3. Explicit programmatic geometry management via a JavaScript helper function (`updateSplitLayout(tab)`) that sets `visible`, `width`, and `SplitView.preferredWidth` guarantees pixel-perfect layout across all three modes.

### 6.2 Verified QML Architecture in `ReviewWorkspaceView.qml`

```qml
// Mode Selector Segmented Bar in rightPane
Row {
    spacing: 4

    Button {
        id: previewTabBtn
        objectName: "previewTabButton"
        text: "Rendered Preview"
        implicitHeight: 26
        flat: rightPane.currentTab !== 0
        highlighted: rightPane.currentTab === 0
        onClicked: rightPane.setTab(0)
    }

    Button {
        id: editorTabBtn
        objectName: "editorTabButton"
        text: "Source Editor"
        implicitHeight: 26
        flat: rightPane.currentTab !== 1
        highlighted: rightPane.currentTab === 1
        onClicked: rightPane.setTab(1)
    }

    Button {
        id: splitTabBtn
        objectName: "splitTabButton"
        text: "Dual Pane"
        implicitHeight: 26
        flat: rightPane.currentTab !== 2
        highlighted: rightPane.currentTab === 2
        onClicked: rightPane.setTab(2)
    }
}

// Inner SplitView inside rightPane replacing StackLayout
SplitView {
    id: rightSplitView
    objectName: "rightSplitView"
    Layout.fillWidth: true
    Layout.fillHeight: true
    orientation: Qt.Horizontal

    function updateSplitLayout(tab) {
        var handleW = 4;
        var totalW = width;
        if (totalW <= 0) return;

        if (tab === 0) {
            // Mode 0: Preview Only (100% width)
            markdownEditorPane.visible = false;
            markdownEditorPane.width = 0;
            markdownEditorPane.SplitView.preferredWidth = 0;

            markdownView.visible = true;
            markdownView.width = totalW;
            markdownView.SplitView.preferredWidth = totalW;
        } else if (tab === 1) {
            // Mode 1: Editor Only (100% width)
            markdownEditorPane.visible = true;
            markdownEditorPane.width = totalW;
            markdownEditorPane.SplitView.preferredWidth = totalW;

            markdownView.visible = false;
            markdownView.width = 0;
            markdownView.SplitView.preferredWidth = 0;
        } else if (tab === 2) {
            // Mode 2: Dual-Pane Side-by-Side (50% / 50%)
            var half = Math.floor((totalW - handleW) / 2);
            markdownEditorPane.visible = true;
            markdownEditorPane.width = half;
            markdownEditorPane.SplitView.preferredWidth = half;

            markdownView.visible = true;
            markdownView.width = totalW - handleW - half;
            markdownView.SplitView.preferredWidth = totalW - handleW - half;
        }
    }

    onWidthChanged: {
        if (width > 0) {
            if (rightPane.currentTab === 0) {
                markdownView.width = width;
                markdownView.SplitView.preferredWidth = width;
            } else if (rightPane.currentTab === 1) {
                markdownEditorPane.width = width;
                markdownEditorPane.SplitView.preferredWidth = width;
            }
        }
    }

    handle: Rectangle {
        implicitWidth: 4
        visible: rightPane.currentTab === 2
        color: SplitHandle.pressed ? "#3b82f6" : (SplitHandle.hovered ? "#60a5fa" : "#2a2a35")
    }

    MarkdownEditorPane {
        id: markdownEditorPane
        objectName: "markdownEditorPane"
        SplitView.minimumWidth: visible ? 150 : 0
    }

    MarkdownView {
        id: markdownView
        objectName: "markdownView"
        SplitView.minimumWidth: visible ? 150 : 0
    }
}
```

### 6.3 Inline Warning Banner in `ReviewWorkspaceView.qml`
Positioned immediately above `rightSplitView`:
```qml
Rectangle {
    id: previewErrorBanner
    objectName: "previewErrorBanner"
    Layout.fillWidth: true
    implicitHeight: 28
    color: "#3b1c1c"
    border.color: "#ef4444"
    border.width: 1
    visible: markdownViewerController && markdownViewerController.hasPreviewError

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 10
        anchors.rightMargin: 10

        Text {
            text: "⚠️ Live preview error: " + (markdownViewerController ? markdownViewerController.previewErrorMessage : "")
            color: "#fca5a5"
            font.pixelSize: 11
            elide: Text.ElideRight
            Layout.fillWidth: true
        }
    }
}
```

---

## 7. Hardened Test Matrix (22 Explicit Test IDs)

| Test ID | Test File | Target Scenario | Concrete Failure Asserted If Broken |
| :--- | :--- | :--- | :--- |
| **T-F3-CANONICAL-01** | `test_markdown_live_preview.py` | Typing keystrokes advances `_draft_revision` but leaves `activeVersion` strictly unchanged on both controllers. | Catches false canonical advance during live preview. |
| **T-F3-CANONICAL-02** | `test_markdown_live_preview.py` | After typing in editor, inspect SQLite `jobs.output_path` and verify it equals the original path. | Catches uncommitted draft text overwriting persistent database records. |
| **T-F3-CANONICAL-03** | `test_markdown_live_preview.py` | `render_preview()` executes zero SQLite write queries (`uow.commit()` never called; verify mock UoW calls). | Enforces read-only database access during preview rendering. |
| **T-F3-CANONICAL-04** | `test_markdown_live_preview.py` | Count files in artifact storage directory before and after live preview render; assert count is unchanged. | Catches creation of orphan preview files on disk. |
| **T-F3-RACE-01** | `test_markdown_live_preview.py` | Simulate Revision 1 completing after Revision 2 under Option B; assert Revision 1 is dropped and Revision 2 is retained. | Eliminates out-of-order background task completion races. |
| **T-F3-RACE-02** | `test_markdown_live_preview.py` | Simulate Job A preview task arriving after switching to Job B; assert task is dropped and Job B model is untouched. | Prevents cross-job document state leakage. |
| **T-F3-SAVE-01** | `test_markdown_live_preview.py` | Click Save; verify canonical version advances to $N+1$, disk artifact is created, and in-flight draft preview is reconciled. | Validates clean transition from transient draft preview to canonical committed state. |
| **T-F3-SAVE-02** | `test_markdown_live_preview.py` | Trigger Save with OCC conflict; verify editor enters conflict state, draft text is preserved, and preview model is not blanked. | Ensures failed save preserves draft work and does not clobber preview with stale canonical content. |
| **T-F3-DISCARD-01** | `test_markdown_live_preview.py` | Type draft text, click Discard; verify debounce timer stops, `_draft_revision` increments, in-flight draft is dropped, and saved text renders immediately. | Prevents in-flight dirty preview from reappearing after discard. |
| **T-F3-ERROR-01** | `test_markdown_live_preview.py` | Inject parsing exception in `render_preview()`; verify previous valid model is retained and `hasPreviewError` is True with message. | Prevents full-pane viewer crash on transient syntax errors. |
| **T-F3-STATE-01** | `test_markdown_live_preview.py` | Call `apply_transient_preview()`; assert `model.rowCount()` reflects draft AST without altering `activeVersion`. | Verifies model method updates presentation nodes without touching canonical version metadata. |
| **T-F3-STATE-02** | `test_markdown_live_preview.py` | Call `render_preview()` with Markdown containing image region tokens; verify visual region URIs resolve against base version regions. | Confirms token resolution operates correctly during draft rendering. |
| **T-F3-EXT-01** | `test_markdown_live_preview.py` | Dirty editor + external canonical advance ($N \to N+1$) preserves visible draft preview in `MarkdownDocumentModel`. | Fails if canonical reload overwrites draft preview model while editor is dirty. |
| **T-F3-EXT-02** | `test_markdown_live_preview.py` | Dirty editor + external canonical advance updates canonical `activeVersion` to $N+1$ and triggers editor conflict without replacing draft preview. | Fails if canonical activeVersion does not update or if draft preview is lost. |
| **T-F3-EXT-03** | `test_markdown_live_preview.py` | In-flight pre-advance draft preview task completing after external canonical advance applies cleanly without regressing `activeVersion`. | Fails if external advance corrupts or drops in-flight draft renders for the active job. |
| **T-F3-EXT-04** | `test_markdown_live_preview.py` | After external advance + conflict, clicking Discard reloads canonical source $N+1$, reconciles preview model to $N+1$, and clears conflict state. | Fails if Discard cannot recover clean synchronization after external conflict. |
| **T-F3-QML-01** | `test_review_workspace_editor_integration.py` | In Tab 0 ("Rendered Preview"), verify `markdownView.width > 0` and `markdownEditorPane.width == 0`. | Verifies Mode 0 layout collapse. |
| **T-F3-QML-02** | `test_review_workspace_editor_integration.py` | In Tab 1 ("Source Editor"), verify `markdownEditorPane.width > 0` and `markdownView.width == 0`. | Verifies Mode 1 layout collapse. |
| **T-F3-QML-03** | `test_review_workspace_editor_integration.py` | In Tab 2 ("Dual Pane"), verify `markdownEditorPane.width > 0`, `markdownView.width > 0`, and splitter handle is visible. | Verifies Mode 2 side-by-side split layout. |
| **T-F3-QML-04** | `test_review_workspace_editor_integration.py` | Type text in Editor (Tab 1), switch to Tab 2, switch to Tab 0, switch back to Tab 1; verify text, cursor, and undo history are preserved. | Guarantees non-destructive tab switching without item re-instantiation. |
| **T-F3-QML-05** | `test_review_workspace_editor_integration.py` | Set `hasPreviewError = True` on controller; verify `previewErrorBanner` becomes visible with expected error text. | Verifies inline preview error banner rendering in QML. |
| **T-F3-QML-06** | `test_review_workspace_editor_integration.py` | In Tab 2, resize window width from 600 to 1000; verify both editor and preview expand responsively. | Verifies responsive window resizing in dual-pane mode. |

---

## 8. File Modification Inventory

### Production Files to Modify:
1. `interfaces/desktop/qt_compat.py`: Export `QTimer` from PySide6/PyQt6.
2. `application/services/markdown_viewer_service.py`: Add `render_preview(job_id, raw_text, base_version)`.
3. `interfaces/desktop/models/markdown_document_model.py`: Add `apply_transient_preview(document_dto)`.
4. `interfaces/desktop/controllers/markdown_viewer_controller.py`: Add debounced live preview scheduling, Option B revision matching, `_has_active_draft` guard against canonical model clobbering, transient error properties, and cancellation slots.
5. `interfaces/desktop/app.py`: Wire `sourceTextChanged` and `discarded` in `wire_review_workspace_sync`, including post-conflict canonical reload on discard.
6. `interfaces/desktop/qml/views/ReviewWorkspaceView.qml`: Replace `StackLayout` with `SplitView` featuring `updateSplitLayout(tab)`, add `splitTabButton`, and add `previewErrorBanner`.

### Test Files to Add / Modify:
1. `tests/unit/test_markdown_live_preview.py`: Create dedicated test suite implementing `T-F3-CANONICAL-01` through `04`, `T-F3-RACE-01`, `02`, `T-F3-SAVE-01`, `02`, `T-F3-DISCARD-01`, `T-F3-ERROR-01`, `T-F3-STATE-01`, `02`, and `T-F3-EXT-01` through `04` (16 unit tests).
2. `tests/unit/test_review_workspace_editor_integration.py`: Add QML tests `T-F3-QML-01` through `06` (6 integration tests).

### Strictly Protected Files (MUST NOT CHANGE):
- `core/` (All entities, exceptions, AST blocks, token resolvers)
- `infrastructure/` (SQLite migrations, persistence repositories, storage)
- `application/services/apply_review_service.py` (Canonical version reservation)
- `application/services/markdown_editor_service.py` (Canonical OCC text commit)
- `interfaces/desktop/controllers/markdown_editor_controller.py` (Editor buffer, search/replace, metrics, and highlighter lifecycle remain untouched)

---

## 9. Phased Implementation Tasks

### Task 1: Application Service & Model Foundation (`render_preview` & `apply_transient_preview`)
**Files:**
- Modify: `interfaces/desktop/qt_compat.py`
- Modify: `application/services/markdown_viewer_service.py`
- Modify: `interfaces/desktop/models/markdown_document_model.py`
- Test: `tests/unit/test_markdown_live_preview.py`

- [ ] **Step 1.1: Write failing tests `T-F3-CANONICAL-03`, `T-F3-CANONICAL-04`, `T-F3-STATE-01`, `T-F3-STATE-02`**
- [ ] **Step 1.2: Run tests to verify failure**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_markdown_live_preview.py -k "test_canonical_03 or test_canonical_04 or test_state" -v`
- [ ] **Step 1.3: Expose `QTimer` in `qt_compat.py`, implement `render_preview` in `MarkdownViewerService`, implement `apply_transient_preview` in `MarkdownDocumentModel`**
- [ ] **Step 1.4: Run tests to verify pass**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_markdown_live_preview.py -k "test_canonical_03 or test_canonical_04 or test_state" -v`
- [ ] **Step 1.5: Commit Task 1**
  ```bash
  git add interfaces/desktop/qt_compat.py application/services/markdown_viewer_service.py interfaces/desktop/models/markdown_document_model.py tests/unit/test_markdown_live_preview.py
  git commit -m "feat(phase10f): add render_preview service and transient model projection"
  ```

---

### Task 2: Asynchronous Controller Scheduling & Option B Race Protection
**Files:**
- Modify: `interfaces/desktop/controllers/markdown_viewer_controller.py`
- Test: `tests/unit/test_markdown_live_preview.py`

- [ ] **Step 2.1: Write failing tests `T-F3-CANONICAL-01`, `T-F3-RACE-01`, `T-F3-RACE-02`, `T-F3-ERROR-01`**
- [ ] **Step 2.2: Run tests to verify failure**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_markdown_live_preview.py -k "test_canonical_01 or test_race or test_error" -v`
- [ ] **Step 2.3: Implement `scheduleLivePreview`, `flushLivePreview`, `cancelPendingLivePreviewAndReconcile`, Option B `_on_internal_preview_loaded`, error handling, and transient properties in `MarkdownViewerController`**
- [ ] **Step 2.4: Run tests to verify pass**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_markdown_live_preview.py -k "test_canonical_01 or test_race or test_error" -v`
- [ ] **Step 2.5: Commit Task 2**
  ```bash
  git add interfaces/desktop/controllers/markdown_viewer_controller.py tests/unit/test_markdown_live_preview.py
  git commit -m "feat(phase10f): add debounced live preview and race protection to viewer controller"
  ```

---

### Task 3: Presentation Coordinator Wiring, Lifecycle & External Advance Hardening
**Files:**
- Modify: `interfaces/desktop/controllers/markdown_viewer_controller.py` (dirty draft guard in canonical loaders)
- Modify: `interfaces/desktop/app.py` (wiring live preview, discard reconciliation, and external advance handling)
- Test: `tests/unit/test_markdown_live_preview.py`

- [ ] **Step 3.1: Write failing tests `T-F3-CANONICAL-02`, `T-F3-SAVE-01`, `T-F3-SAVE-02`, `T-F3-DISCARD-01`, `T-F3-EXT-01`, `T-F3-EXT-02`, `T-F3-EXT-03`, `T-F3-EXT-04`**
- [ ] **Step 3.2: Run tests to verify failure**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_markdown_live_preview.py -k "test_canonical_02 or test_save or test_discard or test_ext" -v`
- [ ] **Step 3.3: Implement `_has_active_draft` guard in `MarkdownViewerController`, wire `sourceTextChanged` and `discarded` in `wire_review_workspace_sync` in `app.py`, and implement post-conflict canonical reload**
- [ ] **Step 3.4: Run tests to verify pass**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_markdown_live_preview.py -v`
- [ ] **Step 3.5: Commit Task 3**
  ```bash
  git add interfaces/desktop/controllers/markdown_viewer_controller.py interfaces/desktop/app.py tests/unit/test_markdown_live_preview.py
  git commit -m "feat(phase10f): wire editor live preview, discard reconciliation, and external advance protection"
  ```

---

### Task 4: QML Dual-Pane SplitView & Layout Coordination
**Files:**
- Modify: `interfaces/desktop/qml/views/ReviewWorkspaceView.qml`
- Test: `tests/unit/test_review_workspace_editor_integration.py`

- [ ] **Step 4.1: Write failing tests `T-F3-QML-01` through `T-F3-QML-06`**
- [ ] **Step 4.2: Run tests to verify failure**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_review_workspace_editor_integration.py -k "test_qml_splitview or test_qml_dual_pane" -v`
- [ ] **Step 4.3: Implement `updateSplitLayout(tab)`, `splitTabButton`, inner `SplitView`, and `previewErrorBanner` in `ReviewWorkspaceView.qml`**
- [ ] **Step 4.4: Run tests to verify pass**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_review_workspace_editor_integration.py -k "test_qml" -v`
- [ ] **Step 4.5: Commit Task 4**
  ```bash
  git add interfaces/desktop/qml/views/ReviewWorkspaceView.qml tests/unit/test_review_workspace_editor_integration.py
  git commit -m "feat(phase10f): add dual-pane splitview and mode tabs to review workspace"
  ```

---

### Task 5: Full Verification & Forensic Audit
- [ ] **Step 5.1: Run entire test suite**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest`
  Expected: 100% pass rate (>700 tests).
- [ ] **Step 5.2: Check git diff for whitespace and hygiene**
  Run: `git diff --check`
- [ ] **Step 5.3: Run offscreen QML smoke test**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_phase10e_hardening_qml_smoke.py`
