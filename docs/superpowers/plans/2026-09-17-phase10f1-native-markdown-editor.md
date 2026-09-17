# Phase 10F.1 — Native Markdown Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the first vertical slice (Phase 10F.1) of the desktop native Markdown Editor in PolpoT, enabling users to view, edit, save, and discard raw Markdown text within the Review Workspace while preserving immutable versioned artifact semantics and preventing loss of human text edits during subsequent visual region changes.

**Architecture:** A native QML `TextArea` operating in `PlainText` mode bound to a new presentation `MarkdownEditorController`. The controller delegates loading and atomic artifact versioning to a pure `MarkdownEditorService`. Saving writes a new immutable `output_{job_id}_v{N+1}.md` and updates SQLite `jobs.output_path`. `ApplyReviewService` is corrected to derive from the current active canonical Markdown document rather than stale per-page slices. The existing `MarkdownView` preview pipeline is preserved and refreshed upon Save.

**Tech Stack:** Python 3.11+, PySide6 / PyQt6, QtQuick 2.15, QtQuick.Controls 2.15, SQLite (WAL mode), pytest.

**Spec:** [`phase10f-markdown-editor-architecture-audit.md`](file:///home/amirreza-a2a/.gemini/antigravity-cli/brain/b14c4bd5-60ed-4f79-b69b-56cba470838f/phase10f-markdown-editor-architecture-audit.md)

## Global Constraints
- Desktop-first, local-first, serverless architecture (AGENTS.md Rule 2).
- Zero remote backend, FastAPI, or HTTP server dependencies (AGENTS.md Rule 3).
- Zero Telegram dependencies (AGENTS.md Rule 4).
- Clean Architecture: `core/` and `application/` must remain 100% independent from Qt/PySide6 (AGENTS.md Rule 6).
- SQLite concurrency: connections are worker-local, transactions use `BEGIN IMMEDIATE` (AGENTS.md Rule 8).
- Secrets: zero raw API keys in serialized artifacts, DTOs, or logs (AGENTS.md Rule 10).
- Long-running operations must not execute on the Qt GUI thread (AGENTS.md Rule 12).
- Native QML `TextArea` must use `textFormat: TextEdit.PlainText`; `QTextDocument.setMarkdown()` and `toMarkdown()` must NOT be used for persistence.
- The editor operates on raw Markdown text, NOT on an AST serialization.
- All technical documentation, docstrings, and comments must be in English (AGENTS.md Rule 20).

---

## A. Implementation Objective

Deliver Phase 10F.1: an in-place, native QML Markdown editor within the right pane of `ReviewWorkspaceView.qml`. This slice enables users to edit document text with full fidelity (whitespace, wiki-links, HTML comments, arbitrary syntax), commit edits to a new immutable canonical version, discard uncommitted changes, and toggle between Rendered Preview and Source Editor. It also fixes `ApplyReviewService` so that later PDF visual region operations never overwrite user Markdown edits.

---

## B. Current Code Constraints

1. **Active State Pointer:** SQLite `jobs.output_path` points to the active canonical artifact (e.g. `file://.../output_{job_id}_v{N}.md`).
2. **Version Watermark:** SQLite `jobs.output_artifact_version_watermark` monotonically increments for every staged Markdown artifact; version numbers are never reused.
3. **Storage Abstraction:** [`IArtifactStorage`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/application/ports/storage.py) (`LocalStorageAdapter`) stores files by `ArtifactHandle`. Artifact files are immutable once written.
4. **Preview Engine:** [`MarkdownViewerController`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/interfaces/desktop/controllers/markdown_viewer_controller.py) loads `job.output_path` via [`MarkdownViewerService.load_document()`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/application/services/markdown_viewer_service.py) into [`MarkdownDocumentModel`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/interfaces/desktop/models/markdown_document_model.py). It manages generation tokens (`_request_id`) and structural reconciliation (`reconcile_document`).
5. **Existing Workspace:** [`ReviewWorkspaceView.qml`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/interfaces/desktop/qml/views/ReviewWorkspaceView.qml) uses a horizontal `SplitView` containing `DocumentViewerView` (left, PDF) and `MarkdownView` (right, preview).
6. **Defect in `ApplyReviewService`:** Lines 218–232 reconstruct Markdown from `page_{p}_v{N}.md` candidate handles. If an editor edits the unified `output_{job_id}_v{N}.md`, these page slices do not exist, causing `ApplyReviewService` to fall back to `page_{p}.md` (initial OCR) and erase all human edits on subsequent region changes.

---

## C. Canonical Markdown Ownership Changes

```text
                                OLD PIPELINE (Phase 10B/10E)
                                
       JobExecutionService                ApplyReviewService
         (Initial OCR)                      (Region Review)
               │                                   │
       stores page_{p}.md                  reads page_{p}_vN.md
               │                                   │
       unifies to output_{job}.md          unifies to output_{job}_vN.md
               │                                   │
               └───────────────┬───────────────────┘
                               ▼
                    job.output_path in SQLite
                               │
                               ▼
                     MarkdownViewerService
                        (Read-only View)

================================================================================

                              CORRECTED PIPELINE (Phase 10F.1)
                              
      JobExecutionService       MarkdownEditorService       ApplyReviewService
         (Initial OCR)              (Human Edits)             (Region Review)
               │                          │                          │
        writes initial v1         reads active vN            reads active vN
               │                  writes edited vN+1         updates tokens vN+1
               │                          │                          │
               └──────────────────────────┼──────────────────────────┘
                                          ▼
                               Exactly ONE Active Source:
                           job.output_path in SQLite
                           (output_{job_id}_v{N}.md)
                                          │
                                          ▼
                                MarkdownViewerService
                                (Preview Projection)
```

**Key Invariant:** There is exactly **one** active canonical Markdown document per job. Both `MarkdownEditorService` and `ApplyReviewService` consume the active canonical text from `job.output_path`, apply their respective modifications, and commit the next immutable version to `job.output_path`.

---

## D. ApplyReviewService Correction

### Required Changes to `application/services/apply_review_service.py`:
1. **Source Selection:** Replace the page-slice loop (lines 218–232) with a unified source loader:
   - Attempt to load the active canonical artifact pointed to by `job.output_path` (e.g. `output_{job_id}_v{curr_active_md_ver}.md`).
   - If `job.output_path` is missing or the artifact does not exist on disk, fall back to assembling from `page_{p}.md` (for legacy/un-migrated jobs).
2. **Token Mutation on Unified Text:**
   - For rejected regions: strip matching `tag_pattern_1` (`![[...|region_id=...]]`) and `tag_pattern_2` (`![[crop_...]]`).
   - For active modified regions: replace matching `tag_pattern_1` or `tag_pattern_2` with the updated token `![[{active_filename}|region_id={r.region_id}]]`.
   - For newly created manual regions (neither pattern matched):
     - Scan for `<!-- Page {r.page_number} -->`. If found, insert `\n\n{new_token}\n` immediately before the next `<!-- Page ... -->` comment or at the end of that page's text section.
     - If no `<!-- Page ... -->` comment exists (e.g. user removed it), append `\n\n{new_token}\n` to the end of the document.
3. **Staging & Commit:**
   - Store the updated unified text as `output_{job_id}_v{target_md_version}.md`.
   - Update `job_record.output_path` in SQLite.
   - Retain staging of `page_{p}_v{target_md_version}.md` only when page markers exist, for backwards compatibility.

---

## E. MarkdownEditorService

### Location: `application/services/markdown_editor_service.py`
### Dependencies: `uow_factory: IUnitOfWorkFactory`, `storage: IArtifactStorage`

```python
class MarkdownEditorService:
    def __init__(self, uow_factory: IUnitOfWorkFactory, storage: IArtifactStorage):
        self.uow_factory = uow_factory
        self.storage = storage

    def load_source_text(self, job_id: int) -> Tuple[str, int]:
        """
        Loads the active canonical Markdown text and its version number for job_id.
        Returns: (raw_text: str, active_version: int)
        Raises:
            EntityNotFoundError: if job does not exist.
            DomainError: if job has no output_path or artifact is missing.
        """

    def commit_source_text(
        self,
        job_id: int,
        raw_text: str,
        base_version: int,
    ) -> int:
        """
        Commits raw Markdown text as a new immutable canonical artifact.
        
        Guarantees:
          - Optimistic concurrency: verifies active version == base_version before commit.
            If active version in SQLite > base_version, raises StaleDocumentVersionError.
          - Durable Watermark: reserves next version number atomically in SQLite.
          - Immutable Staging: stores `output_{job_id}_v{new_version}.md` via IArtifactStorage.
          - Pre-commit validation: asserts staged artifact exists on disk before SQLite commit.
          - Atomic Commit: updates `job.output_path` to the new artifact URI.
        Returns: new_version: int
        """
```

---

## F. MarkdownEditorController

### Location: `interfaces/desktop/controllers/markdown_editor_controller.py`
### Inherits: `QObject`

### Properties:
- `sourceText` (`str`, notify `sourceTextChanged`)
- `isDirty` (`bool`, notify `isDirtyChanged`)
- `isLoading` (`bool`, notify `loadingChanged`)
- `isSaving` (`bool`, notify `savingChanged`)
- `errorMessage` (`str`, notify `errorChanged`)
- `activeJobId` (`int`, notify `activeJobIdChanged`)
- `activeVersion` (`int`, notify `activeVersionChanged`)
- `hasConflict` (`bool`, notify `hasConflictChanged`)
- `conflictMessage` (`str`, notify `conflictMessageChanged`)

### Public Slots:
- `loadSource(job_id: int)` — asynchronous via `ThreadPoolExecutor`
- `load_source_sync(job_id: int)` — synchronous for deterministic unit testing
- `setSourceText(text: str)` — updates buffer, recalculates `isDirty = (text != self._clean_source_text)`
- `save()` — asynchronous commit of `_current_text` with `base_version = _loaded_version`
- `save_sync()` — synchronous save for unit testing
- `discard()` — resets `_current_text = _clean_source_text`, resets `isDirty = False`, emits `sourceTextChanged`
- `clear()` — resets all internal state
- `shutdown()` — cancels executor tasks, shuts down thread pool

### Signals:
- `sourceTextChanged()`, `isDirtyChanged()`, `loadingChanged()`, `savingChanged()`, `errorChanged()`
- `activeJobIdChanged()`, `activeVersionChanged()`, `hasConflictChanged()`, `conflictMessageChanged()`
- `saved(int new_version)`
- `discarded()`
- `conflictDetected(int loaded_version, int current_active_version)`

---

## G. MarkdownEditorPane.qml

### Location: `interfaces/desktop/qml/components/MarkdownEditorPane.qml`

### Visual Structure:
```text
┌────────────────────────────────────────────────────────────────────────┐
│ [Save]  [Discard]   ● Modified   v3                     [UTF-8 Plain]  │  Toolbar (48px)
├────────────────────────────────────────────────────────────────────────┤
│ ⚠️ Document modified externally (v4 available). Save blocked. [Reload] │  Conflict Banner (if active)
├────────────────────────────────────────────────────────────────────────┤
│ # Document Heading                                                     │
│                                                                        │
│ This is raw editable markdown text.                                    │
│ ![[crop_1_abc_v2.jpg|region_id=abc]]                                   │
│                                                                        │
│                                                  ScrollView + TextArea │
└────────────────────────────────────────────────────────────────────────┘
```

### Key Elements:
- `id: markdownEditorPaneRoot`
- `property var controller: typeof markdownEditorController !== "undefined" ? markdownEditorController : null`
- **Toolbar:**
  - `Button { text: "Save"; enabled: controller && controller.isDirty && !controller.isSaving && !controller.hasConflict; onClicked: controller.save() }`
  - `Button { text: "Discard"; enabled: controller && controller.isDirty && !controller.isSaving; onClicked: controller.discard() }`
  - `Text { text: controller ? (controller.isDirty ? "● Modified" : "✓ Saved") : ""; color: controller && controller.isDirty ? "#f59e0b" : "#10b981" }`
  - `Rectangle { /* Version Badge */ Text { text: controller ? "v" + controller.activeVersion : "v1" } }`
- **Conflict Banner:**
  - Visible when `controller && controller.hasConflict`.
  - Shows warning message and a "Reload Latest" button calling `controller.loadSource(controller.activeJobId)`.
- **Editor Surface:**
  - `ScrollView` with `TextArea`:
    - `id: textArea`
    - `textFormat: TextEdit.PlainText`
    - `font.family: "Monospace"`
    - `font.pixelSize: 13`
    - `color: "#f3f4f6"`
    - `wrapMode: TextArea.WrapAtWordBoundaryOrAnywhere`
    - `selectByMouse: true`
    - `onTextChanged: if (controller && !controller.isLoading) controller.setSourceText(text)`
  - `Shortcut { sequence: StandardKey.Save; onActivated: if (controller && controller.isDirty && !controller.hasConflict) controller.save() }`

---

## H. ReviewWorkspace Integration

### Location: `interfaces/desktop/qml/views/ReviewWorkspaceView.qml`

Replace the static `MarkdownView` in the right pane of `SplitView` with a container holding a segmented control tab bar at the top:

```qml
Item {
    id: rightPaneContainer
    SplitView.minimumWidth: 260
    SplitView.preferredWidth: 500
    SplitView.fillWidth: true

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // Sub-navigation bar for Right Pane
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 36
            color: "#121218"
            border.color: "#272732"
            border.width: 1

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                spacing: 8

                // Tab: Preview
                Button {
                    text: "Rendered Preview"
                    flat: true
                    highlighted: rightPaneStack.currentIndex === 0
                    onClicked: rightPaneStack.currentIndex = 0
                }

                // Tab: Edit
                Button {
                    text: "Source Editor"
                    flat: true
                    highlighted: rightPaneStack.currentIndex === 1
                    onClicked: {
                        rightPaneStack.currentIndex = 1;
                        if (editorPane.controller && editorPane.controller.activeJobId !== markdownView.controller.activeJobId) {
                            editorPane.controller.loadSource(markdownView.controller.activeJobId);
                        }
                    }
                }

                Item { Layout.fillWidth: true }
            }
        }

        // Stacked View: Preview vs Edit
        StackLayout {
            id: rightPaneStack
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: 0

            MarkdownView {
                id: markdownView
                objectName: "markdownView"
            }

            MarkdownEditorPane {
                id: editorPane
                objectName: "markdownEditorPane"
            }
        }
    }
}
```

### Signal Wiring in `interfaces/desktop/app.py`:
```python
# When editor successfully saves, reload preview document automatically
markdown_editor_controller.saved.connect(
    lambda new_ver: markdown_viewer_controller.loadDocument(markdown_editor_controller.activeJobId)
)

# When PDF viewer commits a new region recrop:
def _on_region_artifact_committed(job_id, region_id, new_version, new_artifact_uri):
    if markdown_viewer_controller.activeJobId == job_id:
        markdown_viewer_controller.updateRegionArtifact(region_id, new_artifact_uri, new_version)
    if markdown_editor_controller.activeJobId == job_id:
        if not markdown_editor_controller.isDirty:
            # Clean buffer: auto-reload updated canonical text
            markdown_editor_controller.loadSource(job_id)
        else:
            # Dirty buffer: signal conflict to prevent overwriting new region crop
            markdown_editor_controller.notifyExternalAdvance(new_version)

document_viewer_controller.regionArtifactCommitted.connect(_on_region_artifact_committed)
```

---

## I. Save / Discard / Conflict Semantics

### 1. Load:
- `loadSource(job_id)` retrieves raw UTF-8 text and active version from `MarkdownEditorService`.
- Sets `_clean_source_text = raw_text`, `_current_text = raw_text`, `_loaded_version = active_version`.
- Resets `isDirty = False`, `hasConflict = False`.

### 2. Edit:
- User types in `TextArea`. QML calls `setSourceText(text)`.
- Controller sets `_current_text = text`, updates `isDirty = (_current_text != _clean_source_text)`.

### 3. Save:
- Triggered by `Ctrl+S` or "Save" button.
- If not `isDirty` or `isSaving`, no-op.
- Validates optimistic concurrency: `service.commit_source_text(job_id, _current_text, base_version=_loaded_version)`.
- If SQLite active version > `_loaded_version`: raises `StaleDocumentVersionError`, sets `hasConflict = True`, blocks save.
- On success: updates `_loaded_version = new_version`, `_clean_source_text = _current_text`, resets `isDirty = False`, emits `saved(new_version)`.

### 4. Discard:
- Triggered by "Discard" button.
- Replaces `_current_text = _clean_source_text`, resets `isDirty = False`, emits `sourceTextChanged()`.

### 5. Conflict Resolution:
- If external process (e.g. `ApplyReviewService`) advances active version while buffer is dirty:
  - `hasConflict` becomes `True`.
  - Save button is disabled.
  - User can click "Reload Latest" (discards local edits, loads external version) or user can manually copy edits, reload, and re-apply.

---

## J. Preview Refresh Semantics

1. In Phase 10F.1, typing in the editor does **not** trigger background parsing or preview model changes.
2. When the user saves (`saved(new_version)`), `MarkdownViewerController.loadDocument(job_id)` is invoked.
3. The preview pipeline reads the newly committed `output_{job_id}_v{new_version}.md`, parses it via `MarkdownItParser`, projects DTOs, and updates `MarkdownDocumentModel`.
4. Switching back to the "Rendered Preview" tab immediately shows the updated, saved document.

---

## K. Test Plan

### 1. Unit Tests — `ApplyReviewService` Source Correction
**File:** `tests/unit/test_phase10f_apply_review_source.py`
- `test_apply_reviews_preserves_user_authored_markdown()`: Verifies arbitrary human paragraphs in `output_1_v2.md` are retained when region crop V3 is generated.
- `test_apply_reviews_updates_existing_token_in_canonical_text()`: Verifies `![[crop_1_rid_v1.jpg|region_id=rid]]` is updated to `v2` without duplicating lines.
- `test_apply_reviews_inserts_new_manual_region_token_near_page_marker()`: Verifies manual region token is inserted in the correct page section.
- `test_apply_reviews_falls_back_to_page_slices_if_canonical_missing()`: Verifies legacy fallback when `job.output_path` is absent.

### 2. Unit Tests — `MarkdownEditorService`
**File:** `tests/unit/test_markdown_editor_service.py`
- `test_load_source_text_returns_exact_raw_bytes()`: Verifies whitespace, wiki-links, HTML comments, and special chars are returned verbatim.
- `test_commit_source_text_creates_new_versioned_artifact()`: Verifies watermark increments, `output_{job}_v{N+1}.md` exists, and SQLite `job.output_path` updates.
- `test_commit_source_text_rejects_stale_base_version()`: Verifies `StaleDocumentVersionError` when active version > base version.
- `test_commit_source_text_does_not_mutate_previous_artifact()`: Verifies historical version files remain unchanged.

### 3. Unit Tests — `MarkdownEditorController`
**File:** `tests/unit/test_markdown_editor_controller.py`
- `test_initial_state_clean_and_not_dirty()`: Verifies properties on clean load.
- `test_set_source_text_transitions_dirty_state()`: Verifies `isDirty` flips to `True` on change and back to `False` if reverted.
- `test_save_sync_clears_dirty_and_advances_version()`: Verifies save flow and signal emissions.
- `test_discard_restores_original_clean_text()`: Verifies buffer rollback on discard.
- `test_conflict_detection_when_external_version_advances()`: Verifies conflict state when notified of external version change while dirty.
- `test_shutdown_cancels_in_flight_tasks()`: Verifies clean executor termination.

### 4. Integration & QML Smoke Tests
**File:** `tests/unit/test_review_workspace_editor_integration.py`
- `test_app_container_wires_editor_service_and_controller()`: Verifies DI registration in `DesktopAppContainer`.
- `test_saved_signal_triggers_markdown_viewer_reload()`: Verifies preview reload upon editor save.
- `test_qml_editor_pane_loads_without_syntax_errors()`: Smoke test loading `MarkdownEditorPane.qml` in QML engine.

---

## L. File-by-File Change Matrix

| File | Status | Layer | Purpose |
|---|---|---|---|
| `application/services/apply_review_service.py` | Modify | Application | Correct source selection to read active canonical `output_{job}_vN.md` instead of reconstructing from `page_{p}` slices. |
| `application/services/markdown_editor_service.py` | **Create** | Application | Core application service for loading raw source and atomically committing versioned Markdown artifacts. |
| `core/exceptions/domain_exceptions.py` | Modify | Core | Add `StaleDocumentVersionError` domain exception. |
| `interfaces/desktop/controllers/markdown_editor_controller.py` | **Create** | Presentation | QObject controller managing editor buffer, dirty calculation, save/discard, and conflict state. |
| `interfaces/desktop/qml/components/MarkdownEditorPane.qml` | **Create** | Presentation (QML) | Native QML component with action toolbar and `PlainText` `TextArea`. |
| `interfaces/desktop/qml/views/ReviewWorkspaceView.qml` | Modify | Presentation (QML) | Add Preview/Edit segmented tabs in right pane to host both `MarkdownView` and `MarkdownEditorPane`. |
| `interfaces/desktop/composition.py` | Modify | Presentation / DI | Register `MarkdownEditorService` and wire into `DesktopAppContainer`. |
| `interfaces/desktop/app.py` | Modify | Presentation | Instantiate `MarkdownEditorController`, expose `markdownEditorController` to QML context, wire `saved` signal to viewer reload. |
| `interfaces/desktop/qml/Main.qml` | Modify | Presentation (QML) | In `openReviewWorkspace(jobId)`, call `markdownEditorController.loadSource(jobId)`. |
| `tests/unit/test_phase10f_apply_review_source.py` | **Create** | Tests | Unit tests for corrected `ApplyReviewService` source preservation. |
| `tests/unit/test_markdown_editor_service.py` | **Create** | Tests | Unit tests for `MarkdownEditorService`. |
| `tests/unit/test_markdown_editor_controller.py` | **Create** | Tests | Unit tests for `MarkdownEditorController`. |
| `tests/unit/test_review_workspace_editor_integration.py` | **Create** | Tests | Integration tests for container wiring, signal flows, and QML smoke tests. |

---

## M. Implementation Order & Detailed Tasks

### Task 1: Correct `ApplyReviewService` to Preserve Canonical Markdown Text

**Files:**
- Modify: `core/exceptions/domain_exceptions.py`
- Modify: `application/services/apply_review_service.py:211-285`
- Test: `tests/unit/test_phase10f_apply_review_source.py`

**Interfaces:**
- Consumes: `IArtifactStorage`, `IUnitOfWorkFactory`, `Job.output_path`
- Produces: Corrected `ApplyReviewService._execute_apply()` deriving from active `output_{job_id}_v{current}.md`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_phase10f_apply_review_source.py`:
```python
import os
import pytest
from core.entities.artifact import ArtifactHandle, ArtifactType, StorageBackendType
from core.entities.bounding_box import BoundingBox
from core.entities.job import Job, JobStatus
from core.entities.prompt import Prompt, PromptType
from core.entities.visual_region import RegionOrigin, ReviewStatus, SyncStatus, VisualRegion
from infrastructure.document.pymupdf_processor import PyMuPDFDocumentProcessor
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter
from application.services.apply_review_service import ApplyReviewService


def test_apply_reviews_preserves_user_authored_markdown(tmp_path):
    # Setup DB and Storage
    db_path = tmp_path / "test.db"
    mgr = SQLiteDatabaseManager(db_path)
    SQLiteMigrationRunner(mgr).run_migrations()
    uow_factory = SQLiteUnitOfWorkFactory(mgr)
    storage = LocalStorageAdapter(base_dir=str(tmp_path / "artifacts"))
    doc_proc = PyMuPDFDocumentProcessor()

    # Generate dummy 1-page PDF
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    page.draw_rect(fitz.Rect(50, 50, 150, 150), color=(1, 0, 0), fill=(1, 0, 0))
    pdf_bytes = doc.tobytes()
    doc.close()

    pdf_handle = storage.store(job_id=1, artifact_type=ArtifactType.SOURCE_PDF, filename="source.pdf", data=pdf_bytes)

    # Initial Markdown containing custom user-edited paragraphs
    user_edited_text = (
        "# Custom User Title\n\n"
        "This is a custom user-authored sentence that must NOT be erased.\n\n"
        "![[crop_1_reg1_v1.jpg|region_id=11111111-1111-1111-1111-111111111111]]\n\n"
        "Second custom user-authored paragraph."
    )
    output_handle = storage.store(
        job_id=1, artifact_type=ArtifactType.OUTPUT_MARKDOWN, filename="output_1_v1.md", data=user_edited_text.encode("utf-8")
    )

    with uow_factory.create() as uow:
        p = uow.prompts.save(Prompt(id=None, name="P", text="T", prompt_type=PromptType.PIPELINE_1, is_default=True))
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="source.pdf",
                file_path=pdf_handle.uri,
                total_pages=1,
                prompt_id=p.id,
                status=JobStatus.COMPLETED,
                output_path=output_handle.uri,
                output_artifact_version_watermark=1,
            )
        )
        region = uow.visual_regions.save(
            VisualRegion(
                region_id="11111111-1111-1111-1111-111111111111",
                job_id=job.id,
                page_number=1,
                box=BoundingBox(0.1, 0.1, 0.5, 0.5),
                origin=RegionOrigin.SYSTEM_DETECTED,
                display_order=1,
                sync_status=SyncStatus.DIRTY_RECROP_REQUIRED,
                active_artifact_version=1,
                artifact_version_watermark=1,
            )
        )
        uow.commit()

    service = ApplyReviewService(uow_factory=uow_factory, storage=storage, doc_processor=doc_proc)
    result = service.apply_reviews(job_id=job.id)

    assert result.success is True
    assert "output_1_v2.md" in result.output_markdown_uri

    # Retrieve v2 output and verify human edits survived
    v2_handle = ArtifactHandle(StorageBackendType.LOCAL_FS, result.output_markdown_uri, ArtifactType.OUTPUT_MARKDOWN, job.id, "output_1_v2.md")
    v2_text = storage.retrieve(v2_handle).decode("utf-8")

    assert "Custom User Title" in v2_text
    assert "This is a custom user-authored sentence that must NOT be erased." in v2_text
    assert "Second custom user-authored paragraph." in v2_text
    assert "crop_1_11111111-1111-1111-1111-111111111111_v2.jpg" in v2_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_phase10f_apply_review_source.py -v`  
Expected: FAIL (because `page_1_v1.md` doesn't exist, falling back to empty `page_1.md` and erasing human edits).

- [ ] **Step 3: Modify `apply_review_service.py`**

Update `_execute_apply()` in `application/services/apply_review_service.py` to:
1. First attempt to retrieve `job.output_path` as `base_text`.
2. Fallback to assembling `page_{p}.md` if `job.output_path` is missing.
3. For each region in `all_regions`:
   - If deleted: strip matching regex tokens from `base_text`.
   - If active: replace matching token with `new_token`.
   - If active and not matched in `base_text`: find `<!-- Page {r.page_number} -->` section and append `new_token` there (or at document end).
4. Store updated `base_text` as `output_{job_id}_v{target_md_version}.md`.
5. Update `job_record.output_path`.

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_phase10f_apply_review_source.py -v`  
Expected: PASS

- [ ] **Step 5: Run full existing test suite to ensure no regressions**

Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_phase10b_apply_review.py tests/unit/test_phase10e_recrop_sync_integration.py -v`  
Expected: PASS

---

### Task 2: Create `MarkdownEditorService` Application Service

**Files:**
- Modify: `core/exceptions/domain_exceptions.py`
- Create: `application/services/markdown_editor_service.py`
- Modify: `interfaces/desktop/composition.py:37,162`
- Test: `tests/unit/test_markdown_editor_service.py`

**Interfaces:**
- Consumes: `IUnitOfWorkFactory`, `IArtifactStorage`, `Job.output_path`
- Produces: `MarkdownEditorService.load_source_text(job_id) -> Tuple[str, int]`, `MarkdownEditorService.commit_source_text(job_id, raw_text, base_version) -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_markdown_editor_service.py`:
```python
import pytest
from core.entities.artifact import ArtifactHandle, ArtifactType, StorageBackendType
from core.entities.job import Job, JobStatus
from core.exceptions.domain_exceptions import EntityNotFoundError, DomainError, StaleDocumentVersionError
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter
from application.services.markdown_editor_service import MarkdownEditorService


def test_markdown_editor_service_load_and_commit(tmp_path):
    db_path = tmp_path / "test.db"
    mgr = SQLiteDatabaseManager(db_path)
    SQLiteMigrationRunner(mgr).run_migrations()
    uow_factory = SQLiteUnitOfWorkFactory(mgr)
    storage = LocalStorageAdapter(base_dir=str(tmp_path / "artifacts"))

    initial_text = "# Title\n\nRaw text with special chars: & < > ![[image.jpg|region_id=123]]\n"
    handle = storage.store(1, ArtifactType.OUTPUT_MARKDOWN, "output_1_v1.md", initial_text.encode("utf-8"))

    with uow_factory.create() as uow:
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="doc.pdf",
                file_path="file:///doc.pdf",
                status=JobStatus.COMPLETED,
                output_path=handle.uri,
                output_artifact_version_watermark=1,
            )
        )
        uow.commit()

    service = MarkdownEditorService(uow_factory=uow_factory, storage=storage)

    # 1. Load source text
    text, version = service.load_source_text(job.id)
    assert text == initial_text
    assert version == 1

    # 2. Commit updated text
    edited_text = initial_text + "\nAdded line."
    new_version = service.commit_source_text(job.id, edited_text, base_version=1)
    assert new_version == 2

    # 3. Verify SQLite output_path updated and new file exists
    with uow_factory.create() as uow:
        updated_job = uow.jobs.get_by_id(job.id)
        assert "output_1_v2.md" in updated_job.output_path
        assert updated_job.output_artifact_version_watermark == 2

    loaded_text, loaded_version = service.load_source_text(job.id)
    assert loaded_text == edited_text
    assert loaded_version == 2

    # 4. Stale base version rejection
    with pytest.raises(StaleDocumentVersionError):
        service.commit_source_text(job.id, "Stale update", base_version=1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_markdown_editor_service.py -v`  
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `StaleDocumentVersionError` and `MarkdownEditorService`**

1. In `core/exceptions/domain_exceptions.py`, add:
```python
class StaleDocumentVersionError(DomainError):
    """Raised when attempting to commit a document based on an outdated version."""
    def __init__(self, job_id: int, base_version: int, current_version: int):
        super().__init__(
            f"Cannot commit document for job {job_id}: base version {base_version} "
            f"is stale (active version is {current_version})."
        )
        self.job_id = job_id
        self.base_version = base_version
        self.current_version = current_version
```

2. Implement `application/services/markdown_editor_service.py` with `load_source_text` and `commit_source_text`.
3. Register `MarkdownEditorService` in `interfaces/desktop/composition.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_markdown_editor_service.py -v`  
Expected: PASS

---

### Task 3: Create `MarkdownEditorController`

**Files:**
- Create: `interfaces/desktop/controllers/markdown_editor_controller.py`
- Modify: `interfaces/desktop/composition.py`
- Modify: `interfaces/desktop/app.py`
- Test: `tests/unit/test_markdown_editor_controller.py`

**Interfaces:**
- Consumes: `MarkdownEditorService`
- Produces: `MarkdownEditorController` (QObject with `sourceText`, `isDirty`, `save()`, `discard()`, `saved` signal)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_markdown_editor_controller.py`:
```python
import pytest
from unittest.mock import MagicMock
from core.exceptions.domain_exceptions import StaleDocumentVersionError
from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController


def test_markdown_editor_controller_lifecycle():
    mock_service = MagicMock()
    mock_service.load_source_text.return_value = ("# Hello", 1)
    mock_service.commit_source_text.return_value = 2

    controller = MarkdownEditorController(editor_service=mock_service)

    # Initial state
    assert controller.isDirty is False
    assert controller.activeVersion == 1
    assert controller.activeJobId == 0

    # Load source synchronously
    controller.load_source_sync(10)
    assert controller.activeJobId == 10
    assert controller.activeVersion == 1
    assert controller.sourceText == "# Hello"
    assert controller.isDirty is False

    # Edit buffer
    controller.setSourceText("# Hello World")
    assert controller.isDirty is True
    assert controller.sourceText == "# Hello World"

    # Save synchronously
    saved_versions = []
    controller.saved.connect(saved_versions.append)
    controller.save_sync()

    mock_service.commit_source_text.assert_called_once_with(10, "# Hello World", 1)
    assert controller.isDirty is False
    assert controller.activeVersion == 2
    assert saved_versions == [2]

    # Edit again and discard
    controller.setSourceText("# Hello Mutated")
    assert controller.isDirty is True
    controller.discard()
    assert controller.isDirty is False
    assert controller.sourceText == "# Hello World"

    # External conflict detection
    controller.setSourceText("# Edit While External Advance")
    assert controller.isDirty is True
    controller.notifyExternalAdvance(3)
    assert controller.hasConflict is True

    controller.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_markdown_editor_controller.py -v`  
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `MarkdownEditorController`**

Create `interfaces/desktop/controllers/markdown_editor_controller.py` with:
- Thread-safe signals and slots
- `ThreadPoolExecutor` for asynchronous `loadSource` and `save`
- Synchronous `load_source_sync` and `save_sync` for unit testing
- `isDirty` detection: `_current_text != _clean_source_text`
- Conflict tracking: `hasConflict`, `conflictMessage`, `notifyExternalAdvance(current_active_version)`
- Clean `shutdown()` method

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_markdown_editor_controller.py -v`  
Expected: PASS

---

### Task 4: Create `MarkdownEditorPane.qml` Component

**Files:**
- Create: `interfaces/desktop/qml/components/MarkdownEditorPane.qml`
- Test: `tests/unit/test_review_workspace_editor_integration.py`

**Interfaces:**
- Consumes: `markdownEditorController` context property or injected `controller` property
- Produces: `MarkdownEditorPane` QML component

- [ ] **Step 1: Write QML smoke test**

Add to `tests/unit/test_review_workspace_editor_integration.py`:
```python
import pytest
from interfaces.desktop.qt_compat import QGuiApplication, QQmlApplicationEngine, QUrl
from pathlib import Path


@pytest.fixture(scope="module")
def qml_app():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    yield app


def test_qml_markdown_editor_pane_syntax(qml_app):
    engine = QQmlApplicationEngine()
    qml_path = Path(__file__).parents[2] / "interfaces" / "desktop" / "qml" / "components" / "MarkdownEditorPane.qml"
    component = engine.load(QUrl.fromLocalFile(str(qml_path)))
    assert not engine.rootObjects() == [] or component is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_review_workspace_editor_integration.py::test_qml_markdown_editor_pane_syntax -v`  
Expected: FAIL (file does not exist).

- [ ] **Step 3: Implement `MarkdownEditorPane.qml`**

Create `interfaces/desktop/qml/components/MarkdownEditorPane.qml` according to the design in Section G:
- Toolbar with Save, Discard, Dirty Indicator, Version Badge
- Conflict banner with "Reload Latest" button
- `ScrollView` wrapping `TextArea` in `TextEdit.PlainText` mode
- `StandardKey.Save` shortcut

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_review_workspace_editor_integration.py::test_qml_markdown_editor_pane_syntax -v`  
Expected: PASS

---

### Task 5: Integrate View/Edit Tabs into `ReviewWorkspaceView.qml` and Wire Signal Flow

**Files:**
- Modify: `interfaces/desktop/qml/views/ReviewWorkspaceView.qml`
- Modify: `interfaces/desktop/composition.py`
- Modify: `interfaces/desktop/app.py`
- Modify: `interfaces/desktop/qml/Main.qml`
- Test: `tests/unit/test_review_workspace_editor_integration.py`

**Interfaces:**
- Consumes: `MarkdownEditorController`, `MarkdownViewerController`, `DocumentViewerController`
- Produces: Dual-tab right pane (Preview / Edit) with automatic preview refresh upon save and external apply conflict notifications.

- [ ] **Step 1: Write integration test**

Add to `tests/unit/test_review_workspace_editor_integration.py`:
```python
def test_editor_save_refreshes_markdown_viewer(qml_app, tmp_path):
    from interfaces.desktop.composition import DesktopAppContainer
    from interfaces.desktop.app import wire_review_workspace_sync
    from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController
    from interfaces.desktop.controllers.markdown_viewer_controller import MarkdownViewerController
    from interfaces.desktop.controllers.document_viewer_controller import DocumentViewerController

    container = DesktopAppContainer(db_path=tmp_path / "app.db", artifacts_dir=tmp_path / "art")
    container.initialize()

    doc_ctrl = DocumentViewerController(container.document_viewer_service, container.apply_review_service)
    view_ctrl = MarkdownViewerController(container.markdown_viewer_service)
    edit_ctrl = MarkdownEditorController(container.markdown_editor_service)

    # Wire sync
    wire_review_workspace_sync(doc_ctrl, view_ctrl, edit_ctrl)

    # Mock viewer loadDocument
    view_ctrl.loadDocument = MagicMock()

    # Trigger editor saved
    edit_ctrl._active_job_id = 42
    edit_ctrl.saved.emit(2)

    # Assert viewer reloaded
    view_ctrl.loadDocument.assert_called_once_with(42)

    edit_ctrl.shutdown()
    view_ctrl.shutdown()
    doc_ctrl.shutdown()
    container.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_review_workspace_editor_integration.py::test_editor_save_refreshes_markdown_viewer -v`  
Expected: FAIL.

- [ ] **Step 3: Modify `composition.py`, `app.py`, `ReviewWorkspaceView.qml`, and `Main.qml`**

1. In `interfaces/desktop/composition.py`:
   - Instantiate `self.markdown_editor_service = MarkdownEditorService(self.uow_factory, self.storage)`.
2. In `interfaces/desktop/app.py`:
   - Instantiate `markdown_editor_controller = MarkdownEditorController(container.markdown_editor_service)`.
   - Update `wire_review_workspace_sync(doc_ctrl, view_ctrl, edit_ctrl)`.
   - Connect `edit_ctrl.saved.connect(lambda v: view_ctrl.loadDocument(edit_ctrl.activeJobId))`.
   - Expose `ctx.setContextProperty("markdownEditorController", markdown_editor_controller)`.
3. In `interfaces/desktop/qml/views/ReviewWorkspaceView.qml`:
   - Add tab bar toggle (`Rendered Preview` vs `Source Editor`) in the right pane wrapping `MarkdownView` and `MarkdownEditorPane` inside a `StackLayout`.
4. In `interfaces/desktop/qml/Main.qml`:
   - In `openReviewWorkspace(jobId)`: call `markdownEditorController.loadSource(jobId)` alongside viewer loading.

- [ ] **Step 4: Run integration test to verify it passes**

Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_review_workspace_editor_integration.py -v`  
Expected: PASS

- [ ] **Step 5: Run full test suite regression**

Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/ -v`  
Expected: All 618+ tests PASS.

---

## N. Commit Breakdown

### Commit 1: `refactor(phase10f): source apply reviews from canonical markdown artifact`
- Modifies: `application/services/apply_review_service.py`
- Tests: `tests/unit/test_phase10f_apply_review_source.py`
- Scope: Fixes `ApplyReviewService` to derive token updates from the active canonical document rather than stale `page_{p}` files.

### Commit 2: `feat(phase10f): add markdown editor service and controller`
- Adds: `core/exceptions/domain_exceptions.py` (`StaleDocumentVersionError`)
- Adds: `application/services/markdown_editor_service.py`
- Adds: `interfaces/desktop/controllers/markdown_editor_controller.py`
- Modifies: `interfaces/desktop/composition.py`
- Tests: `tests/unit/test_markdown_editor_service.py`, `tests/unit/test_markdown_editor_controller.py`
- Scope: Application and presentation foundations for raw Markdown editing, version reservation, and conflict detection.

### Commit 3: `feat(phase10f): add native qml editor pane and review workspace tab`
- Adds: `interfaces/desktop/qml/components/MarkdownEditorPane.qml`
- Modifies: `interfaces/desktop/qml/views/ReviewWorkspaceView.qml`
- Modifies: `interfaces/desktop/app.py`
- Modifies: `interfaces/desktop/qml/Main.qml`
- Tests: `tests/unit/test_review_workspace_editor_integration.py`
- Scope: QML view integration, Preview/Edit tabs in right pane, and signal wiring for automatic preview refresh on save.

---

## O. Risks and Mitigations

| Risk | Impact | Mitigation in Plan |
|---|---|---|
| **User edits Markdown, then creates region in PDF viewer** | Prior code would erase user Markdown edits on apply | **Task 1** fixes `ApplyReviewService` to derive directly from active canonical text, preserving human edits. |
| **Concurrent Save during active region crop** | Stale version overwriting or race condition | `StaleDocumentVersionError` optimism in `MarkdownEditorService` rejects save if SQLite active version > base version. |
| **Qt Markdown parser mangles wiki links** | Loss of `![[crop...\|region_id=...]]` tokens | QML `TextArea` strictly configured with `textFormat: TextEdit.PlainText`; zero calls to `setMarkdown()` / `toMarkdown()`. |
| **User switches jobs with unsaved edits** | Loss of text | `MarkdownEditorController.isDirty` tracked; job switch checks dirty flag or clears cleanly. |
| **QML TextArea memory leak or unclosed thread tasks** | Crash on application quit | `MarkdownEditorController.shutdown()` cleanly terminates `ThreadPoolExecutor` and is hooked to `app.aboutToQuit`. |

---

## P. Explicit Non-Goals (Strictly Deferred)

The following features are **explicitly out of scope** for Phase 10F.1:
- Live debounced preview while typing.
- Syntax highlighting (`QSyntaxHighlighter`).
- Synchronized cursor/scroll tracking between editor and preview.
- Three-way text merge conflict resolution.
- Local draft scratch files.
- Structural AST editing.

---

## Q. Definition of Done

Phase 10F.1 is complete when:
1. Raw Markdown text loaded from `job.output_path` survives editing, saving, and re-loading with zero syntax normalization (exact whitespace, punctuation, wiki-links, HTML comments preserved).
2. The editor is a native QML `TextArea` in `PlainText` mode embedded within `ReviewWorkspaceView.qml`.
3. Clicking Save or pressing `Ctrl+S` stages `output_{job_id}_v{N+1}.md` and updates SQLite `job.output_path` atomically.
4. Stale editor saves cannot silently overwrite a newer canonical document.
5. `ApplyReviewService` operations preserve user-authored Markdown edits when subsequent visual regions are created, resized, or deleted.
6. Existing region recrop synchronization and new region live synchronization continue to function without regression.
7. All unit and integration tests pass green (including existing 618 tests).
8. Manual verification in the running desktop application confirms seamless switching between "Rendered Preview" and "Source Editor", dirty flag updates, and preview refresh upon Save.
