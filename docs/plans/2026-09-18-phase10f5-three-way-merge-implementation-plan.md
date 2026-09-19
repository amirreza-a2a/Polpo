# Phase 10F.5: Three-Way Merge & Visual Conflict Diff Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement deterministic, format-preserving three-way merge (`diff3`) and interactive in-buffer conflict resolution in the desktop Review Workspace, enabling seamless auto-merge of non-overlapping external updates (such as PDF visual region re-crops) and safe, guided manual resolution of overlapping text edits under strict Optimistic Concurrency Control (OCC).

**Architecture:**
1. **Core Domain (`core/markdown/merge.py`):** Pure functional line-based `diff3` three-way merge engine built on `difflib.SequenceMatcher` alignment against a common Base, producing `ThreeWayMergeResult` and `ConflictHunk` models without Qt, SQLite, or external VCS dependencies. Separates logical comparison normalization from exact line content/newline preservation.
2. **Application Layer (`application/services/markdown_merge_service.py`):** Deterministic, synchronous orchestration service that loads immutable Base and Canonical document artifacts from `IArtifactStorage`, executes core diff3, and queries read-only CommonMark AST line intervals to enrich conflict hunks with human-readable block context (e.g., *"Heading 2: Overview"*). Owned and scheduled off the GUI thread by the presentation executor.
3. **Presentation Model (`interfaces/desktop/models/conflict_session.py`):** Authoritative owner of transient conflict resolution state. Tracks active hunks, resolution choices, hunk navigation, collision-safe tagged marker projections (`<<<<<<< [LOCAL:hunk_0]`), clean candidate reconstruction, and metadata-based save gating.
4. **Presentation Controllers & Views (`interfaces/desktop/`):**
   - `MarkdownEditorController`: Tracks monotonic `merge_session_id`, executes clean auto-merges (re-basing to $N+1$ while keeping local edits dirty), gates Save against unresolved hunks via metadata, and handles second external advances ($N+1 \to N+2$) by extracting the clean candidate without marker leakage.
   - `MarkdownViewerController`: Manages `previewPaused` state; suspends live preview during active conflict resolution while retaining the last valid render under an amber overlay banner.
   - `ReviewWorkspaceSyncCoordinator`: Mutes continuous scroll synchronization while preview is paused.
   - `ReviewWorkspaceView.qml` & `ConflictResolutionBar.qml`: Hosts interactive conflict navigation and resolution toolbar above the editor.
   - `Main.qml`: Intercepts job-switch requests when dirty/conflicting, guarding against accidental data loss with an explicit confirmation dialog.

**Tech Stack:** Python 3.10+, PySide6 / PyQt6, `markdown-it-py`, CommonMark AST, QML QtQuick Controls 2.

**Authoritative ADR:** [`docs/adr/2026-09-18-adr-001-phase10f5-three-way-merge-conflict-resolution.md`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/docs/adr/2026-09-18-adr-001-phase10f5-three-way-merge-conflict-resolution.md)  
**Codebase Design:** [`docs/plans/2026-09-18-phase10f5-codebase-design.md`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/docs/plans/2026-09-18-phase10f5-codebase-design.md)

---

## Global Constraints & Invariants

- **Desktop-First, Local-First, Serverless (AGENTS.md Rules 2 & 3):** Zero remote network activity, zero local HTTP daemon, zero loopback servers. Outbound HTTPS for AI providers only.
- **Clean Architecture Boundaries (AGENTS.md Rule 6):** `core/` and `application/` remain 100% free of Qt, PySide6, PyQt6, and UI concepts.
- **OCC & Version Immutability (AGENTS.md Rules 8 & 9):** Canonical documents never overwrite historical artifacts. Canonical commits strictly use existing [`MarkdownEditorService.commit_source_text`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/application/services/markdown_editor_service.py#L63) with `base_version = current_canonical_version`.
- **Zero Lossy AST Serialization:** The merge engine operates exclusively on raw Markdown text lines. Markdown AST is strictly read-only for labeling. Never serialize an AST back to text.
- **Session-Only Scope (D01):** Uncommitted drafts and conflict sessions reside in RAM. Zero SQLite schema changes or draft table migrations in Phase 10F.5.
- **Tagged Collision-Safe Markers (D04):** System-generated conflict markers use tagged delimiters (e.g. `<<<<<<< [LOCAL:hunk_0]`) preventing collision with legitimate Markdown content containing `<<<<<<<`.
- **Preview Isolation (D05):** Raw conflict markers are never fed to the live preview renderer.
- **Clean Local Input for D06:** A second external advance ($N+1 \to N+2$) must never pass raw conflict markers into diff3; clean local candidate text is extracted from `ConflictSession`.

---

## 1. Repository Baseline

- **Repository Root:** `/home/amirreza-a2a/DevelopPOlpo/PolpoT`
- **Branch:** `master`
- **Current HEAD Commit:** `9a242f4` (`docs(phase10f): record phase 10f.5 architecture decisions`)
- **Baseline Test Verification:** Full suite passing (757 passed via `/home/amirreza-a2a/madarsol/venv/bin/pytest`).
- **Working Tree:** Clean, no uncommitted modifications.

---

## 2. Core Architecture Specifications

### 2.1 The Three Independent Merge Inputs
```text
BASE (Immutable Artifact)   <-- Stored on disk: output_{job}_v{base_version}.md
  +
LOCAL DRAFT (RAM Buffer)    <-- Stored in memory: MarkdownEditorController._source_text
  +
CURRENT CANONICAL (Disk)    <-- Stored on disk: output_{job}_v{canonical_version}.md
```

### 2.2 Auto-Merge State Semantics (D03)
When non-overlapping edits are cleanly merged:
```text
_active_version    = N+1                       # Re-based to latest canonical!
_saved_source_text = canonical content of vN+1 # Canonical baseline updated!
_source_text       = clean_merged_text         # Local edits preserved on top of N+1!
_is_dirty          = (_source_text != _saved_source_text) # True (still dirty!)
_has_conflict      = False                     # No conflict lock!
_active_conflict_session = None                # No session needed!
Save button        = ENABLED                   # User can continue editing or save!
Viewer active ver  = N+1                       # Viewer follows canonical version!
Draft revision     += 1                        # Incremented for live preview sync!
Notification       = "External updates merged seamlessly." (informational toast)
```
*Zero SQLite or disk writes occur during merge analysis.* The user's edits remain dirty against the newly advanced base $N+1$. An immediate subsequent keystroke modifies `_source_text`, retaining dirty state relative to $N+1$.

### 2.3 Authoritative Text vs. In-Buffer Representation (D04)
```text
[Clean Base] + [Clean Local Candidate] + [Clean Remote]
                       │
                       ▼ three_way_merge()
            [ThreeWayMergeResult]
                       │
                       ▼
               [ConflictSession] (Authoritative State Owner)
              /                 \
             ▼                   ▼
[in_buffer_representation]    [authoritative_clean_candidate]
(contains tagged markers)     (never contains markers)
Loaded into TextArea          Used for D06 re-merges and final OCC Save
```

### 2.4 Second External Advance Flow (D06 & D08)
If Canonical advances $N+1 \to N+2$ while resolving an $N$ vs $N+1$ conflict:
1. Extract `clean_candidate = ConflictSession.generate_candidate_markdown()`:
   - For clean spans: emit clean text.
   - For resolved hunks: emit recorded `resolved_text`.
   - For unresolved hunks: emit original `hunk.local_text` (user's edits prior to conflict).
   - *Invariant:* `clean_candidate` contains ZERO conflict markers.
2. Mark active `ConflictSession` invalidated (`is_invalidated = True`).
3. Increment `MarkdownEditorController._merge_session_id += 1`.
4. Trigger fresh `analyze_three_way_merge` with `Base = v{N+1}`, `Local = clean_candidate`, `Remote = v{N+2}`.
5. In-flight callbacks matching old `session_id` are discarded.

### 2.5 Metadata-Based Save Gating
Save gating does NOT inspect `_source_text` for substrings like `"<<<<<<<"`. Instead:
```python
if self._has_conflict or (
    self._active_conflict_session is not None
    and not self._active_conflict_session.is_fully_resolved()
):
    self._error_message = "Cannot save: unresolved conflicts exist."
    self.errorChanged.emit()
    return
```
When all hunks are resolved, `ConflictSession.generate_candidate_markdown()` produces the clean Markdown string. The editor sets `_source_text = candidate_text`, verifies `_active_conflict_session.is_fully_resolved()`, sets `_has_conflict = False`, and invokes `commit_source_text(job_id, _source_text, base_version=canonical_version)`.

### 2.6 diff3 Algorithmic Specification
`SequenceMatcher` is strictly a two-way matching primitive. The core three-way merge in `core/markdown/merge.py` is constructed as follows:
1. **Line Tokenization & Normalization:**
   - Detect newline convention: `\r\n` (CRLF) vs `\n` (LF).
   - Detect trailing newline presence in Base and Local.
   - Normalize strings into lists of logical lines without line endings for comparison.
2. **Two-Way Alignment:**
   - Compute matching blocks $M_{BL} = \text{SequenceMatcher}(None, B, L).\text{get\_matching\_blocks}()$.
   - Compute matching blocks $M_{BR} = \text{SequenceMatcher}(None, B, R).\text{get\_matching\_blocks}()$.
   - Derive change intervals on Base: $I_L = [(b_{start}, b_{end}, l_{start}, l_{end})]$ and $I_R = [(b_{start}, b_{end}, r_{start}, r_{end})]$.
3. **Span Partitioning & Categorization Matrix:**
   - **Local-only change:** $I_L$ non-empty, $I_R$ empty $\to$ `CLEAN_LOCAL`. Emit $L$.
   - **Remote-only change:** $I_R$ non-empty, $I_L$ empty $\to$ `CLEAN_REMOTE`. Emit $R$.
   - **Identical concurrent change:** $I_L$ and $I_R$ cover same base span, and $L == R$ $\to$ `CLEAN_SAME`. Emit $L$ once.
   - **Disjoint changes:** Spans do not overlap $\to$ apply both in order.
   - **Adjacent changes:** Edits at line $10$ and $11$ $\to$ if base anchor matches, emit both cleanly; if ambiguous match boundary, group into `CONFLICT`.
   - **Insert/insert at same offset:** If $L == R$ $\to$ `CLEAN_SAME`; if $L \neq R$ $\to$ `CONFLICT`.
   - **Replace/replace:** Overlapping base spans with $L \neq R$ $\to$ `CONFLICT`.
   - **Delete/modify & Modify/delete:** Overlapping base spans $\to$ `CONFLICT`.
   - **Repeated identical lines & blank lines:** Anchored by nearest surrounding matching blocks.
   - **Markdown separators (`---`):** Aligned via surrounding context lines.
   - **EOF insertions/deletions:** Insertions at end of file treated as span at $|B|$; clean if one-sided or identical, conflict if differing.
   - **Empty files:** Handled as zero-length sequences without division-by-zero or index errors.
4. **Source Fidelity Output Preservation:**
   - Lines are re-joined using the document's original newline style.
   - If original text had a trailing newline, it is preserved. If not, omitted.
   - Leading indentation and whitespace within lines are never modified.

### 2.7 Conflict Resolution Action Semantics
When resolving a `ConflictHunk`:
- **Accept Local:** Sets hunk text to `hunk.local_text`.
- **Accept Incoming:** Sets hunk text to `hunk.remote_text`.
- **Accept Both:**
  - Replace/replace: Concatenates `hunk.local_text + "\n" + hunk.remote_text`.
  - Insert/insert: Concatenates `hunk.local_text + "\n" + hunk.remote_text`.
  - Delete/modify: Emits `hunk.remote_text` (since local deleted the block, accepting both retains remote modifications).
- **Manual Edit:** When user types directly inside the editor between `<<<<<<< [LOCAL:hunk_{i}]` and `>>>>>>> [CANONICAL:hunk_{i}]`, `ConflictSession` detects changes to hunk $i$, captures the text as `CUSTOM_TEXT`, and marks hunk $i$ resolved.
- **Next/Previous Conflict:** Moves `currentHunkIndex` and triggers `MarkdownEditorController.scrollToLine(hunk.start_line)`.
- **Cancel Resolution:** Reverts editor buffer to `_saved_source_text`, invalidates session, and clears conflict state.

### 2.8 Preview Pause / Resume Contract (D05)
- **Pause Ownership:** `MarkdownViewerController.previewPaused: bool` and `previewPausedReason: str`.
- **Overlay Ownership:** `MarkdownView.qml` renders a centered amber banner over a dimmed preview pane: `⚠️ Preview paused during conflict resolution`.
- **Scroll Sync Integration:** `ReviewWorkspaceSyncCoordinator.report_source_scroll_progress` checks `viewer_controller.previewPaused`; if True, preview scrolling is muted.
- **Resumption:** When `ConflictSession.is_fully_resolved()` becomes True, the editor emits `mergeSessionResolved(clean_candidate_text)`. Coordinator calls `viewer.setPreviewPaused(False)` and invokes `viewer.scheduleLivePreview(job_id, clean_candidate_text, canonical_version)`.

### 2.9 Job-Switch Interception Point (D07)
In `interfaces/desktop/qml/Main.qml`, `openReviewWorkspace(jobId)` is the single entry point called from `jobController.open_review_requested` and history selection:
```qml
function openReviewWorkspace(jobId) {
    if (typeof markdownEditorController !== "undefined" && markdownEditorController &&
        markdownEditorController.activeJobId > 0 &&
        markdownEditorController.activeJobId !== jobId &&
        (markdownEditorController.isDirty || markdownEditorController.hasConflict)) {
        pendingReviewJobId = jobId;
        jobSwitchConfirmModal.open();
        return;
    }
    _executeOpenReviewWorkspace(jobId);
}
```
- **Cancel Action:** `pendingReviewJobId = 0; jobSwitchConfirmModal.close();` (User remains on current job).
- **Discard & Switch Action:** `markdownEditorController.clear(); _executeOpenReviewWorkspace(pendingReviewJobId); jobSwitchConfirmModal.close();`

---

## 3. Implementation Tasks

### Task 1: Core Pure-Python diff3 Merge Engine
**Files:**
- Create: `core/markdown/merge.py`
- Test: `tests/unit/test_markdown_diff3_merge.py`

**Interfaces:**
- Consumes: Standard library `difflib.SequenceMatcher`, `re`, `dataclasses`, `enum`.
- Produces: `three_way_merge(base_text: str, local_text: str, remote_text: str) -> ThreeWayMergeResult`, `ConflictHunk`, `HunkType`, `HunkResolution`.

- [ ] **Step 1.1: Write failing core merge tests (`test_markdown_diff3_merge.py`)**
  Implement test cases:
  - `T-MERGE-01`: Clean local-only change against base.
  - `T-MERGE-02`: Clean remote-only change against base.
  - `T-MERGE-03`: Clean disjoint changes (local at line 5, remote at line 50).
  - `T-MERGE-04`: Identical concurrent change deduplicated cleanly.
  - `T-MERGE-05`: Overlapping replace/replace flagged as conflict.
  - `T-MERGE-06`: Delete vs modify collision flagged as conflict.
  - `T-MERGE-07`: Modify vs delete collision flagged as conflict.
  - `T-MERGE-08`: Differing insertions at identical line offset flagged as conflict.
  - `T-MERGE-09`: Adjacent non-overlapping edits merged cleanly without interleaving.
  - `T-MERGE-10`: Repeated blank lines and markdown separators do not misalign diff spans.
  - `T-MERGE-11`: Whitespace and indentation preserved exactly.
  - `T-MERGE-12`: CRLF vs LF line endings normalized to LF for comparison, original preserved on output.
  - `T-MERGE-13`: Trailing newline presence/absence preserved deterministically.
  - `T-MERGE-14`: EOF insertion without trailing newline handled cleanly.
  - `T-MERGE-15`: Empty document base/local/remote handled safely.
  - `T-MERGE-16`: Markdown region token updates (`![[crop_...|region_id=...]]`) merged cleanly.
  - `T-MERGE-17`: Markdown text legitimately containing `<<<<<<<` markers handled collision-safely without spurious conflict boundaries.

- [ ] **Step 1.2: Run tests to verify failure**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_markdown_diff3_merge.py -v`
  Expected: FAIL with `ModuleNotFoundError: No module named 'core.markdown.merge'`

- [ ] **Step 1.3: Implement `core/markdown/merge.py`**
  - Implement `HunkType(Enum)`: `CLEAN_UNCHANGED`, `CLEAN_LOCAL`, `CLEAN_REMOTE`, `CLEAN_SAME`, `CONFLICT`.
  - Implement `HunkResolution(Enum)`: `UNRESOLVED`, `ACCEPT_LOCAL`, `ACCEPT_REMOTE`, `ACCEPT_BOTH`, `CUSTOM_TEXT`.
  - Implement `@dataclass(frozen=True) class ConflictHunk`: line spans, base/local/remote tuples, ast context fields.
  - Implement `@dataclass(frozen=True) class ThreeWayMergeResult`: `has_conflicts`, `clean_text`, `hunks`, counts.
  - Implement `three_way_merge()` with two-way `SequenceMatcher` projections, span partitioning, and output preservation.

- [ ] **Step 1.4: Run tests to verify they pass**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_markdown_diff3_merge.py -v`
  Expected: PASS (17 tests)

- [ ] **Step 1.5: Commit core merge engine**
  ```bash
  git add core/markdown/merge.py tests/unit/test_markdown_diff3_merge.py
  git commit -m "feat(phase10f): add pure python diff3 three-way merge engine"
  ```

---

### Task 2: Application Merge Service & DTO Contracts
**Files:**
- Create: `application/dtos/merge_dto.py`
- Create: `application/services/markdown_merge_service.py`
- Test: `tests/unit/test_markdown_merge_service.py`

**Interfaces:**
- Consumes: `core.markdown.merge.three_way_merge`, `IArtifactStorage`, `IUnitOfWorkFactory`, `MarkdownViewerService`.
- Produces: `MarkdownMergeService.analyze_three_way_merge(...) -> MergeAnalysisResultDTO`, `ConflictHunkDTO`.

- [ ] **Step 2.1: Write failing application service tests (`test_markdown_merge_service.py`)**
  Implement test cases:
  - `T-MERGE-20`: Artifact retrieval loads base $vN$ and canonical $vM$ from storage.
  - `T-MERGE-21`: Missing base artifact falls back to empty base string cleanly without crashing.
  - `T-MERGE-22`: Clean auto-merge returns `MergeAnalysisResultDTO` with `has_conflicts=False`.
  - `T-MERGE-23`: Overlapping edits return `MergeAnalysisResultDTO` with `has_conflicts=True`.
  - `T-MERGE-24`: Conflict hunks enriched with AST context (node type, heading label, region ID).
  - `T-MERGE-25`: Missing/unparseable AST falls back to line-based label without failing merge.
  - `T-MERGE-26`: Synchronous execution does not mutate SQLite or storage (zero-write invariant).

- [ ] **Step 2.2: Run tests to verify failure**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_markdown_merge_service.py -v`
  Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 2.3: Implement DTOs and `MarkdownMergeService`**
  - Implement `application/dtos/merge_dto.py` with `ConflictHunkDTO` and `MergeAnalysisResultDTO`.
  - Implement `application/services/markdown_merge_service.py`:
    - `analyze_three_way_merge(job_id, base_version, local_text, canonical_version, merge_session_id)`:
      - Retrieves `output_{job}_v{base_version}.md` and `output_{job}_v{canonical_version}.md`.
      - Runs `three_way_merge(base_text, local_text, canonical_text)`.
      - Queries AST intervals via `viewer_service.render_text` to assign `ast_label`.
      - Returns `MergeAnalysisResultDTO`.

- [ ] **Step 2.4: Run tests to verify they pass**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_markdown_merge_service.py -v`
  Expected: PASS (7 tests)

- [ ] **Step 2.5: Commit application merge service**
  ```bash
  git add application/dtos/merge_dto.py application/services/markdown_merge_service.py tests/unit/test_markdown_merge_service.py
  git commit -m "feat(phase10f): add application merge service and DTO contracts"
  ```

---

### Task 3: ConflictSession Presentation Model
**Files:**
- Create: `interfaces/desktop/models/conflict_session.py`
- Test: `tests/unit/test_conflict_session.py`

**Interfaces:**
- Consumes: `application.dtos.merge_dto.MergeAnalysisResultDTO`, `ConflictHunkDTO`.
- Produces: `ConflictSession(QObject)` with properties `currentHunkIndex`, `totalConflicts`, `currentConflictLabel`, `canSave`, methods `generate_in_buffer_markdown()`, `generate_candidate_markdown()`, `resolve_hunk()`, `invalidate()`.

- [ ] **Step 3.1: Write failing conflict session tests (`test_conflict_session.py`)**
  Implement test cases:
  - `T-MERGE-30`: Initializes with hunks, current hunk index set to 0.
  - `T-MERGE-31`: `generate_in_buffer_markdown()` outputs tagged conflict markers (`<<<<<<< [LOCAL:hunk_0]`).
  - `T-MERGE-32`: `resolve_hunk(0, "local")` records choice, marks hunk resolved.
  - `T-MERGE-33`: `resolve_hunk(0, "both")` concatenates local and remote for replace/replace.
  - `T-MERGE-34`: `resolve_hunk(0, "both")` on delete/modify emits remote modifications.
  - `T-MERGE-35`: `is_fully_resolved()` returns False until all hunks resolved, then True.
  - `T-MERGE-36`: `generate_candidate_markdown()` produces clean Markdown with zero markers.
  - `T-MERGE-37`: `generate_candidate_markdown()` with partial resolutions falls back to Local for unresolved hunks (D06 clean extraction).
  - `T-MERGE-38`: Tagged delimiter prevents collision with legitimate markdown `<<<<<<<`.
  - `T-MERGE-39`: `invalidate()` flags session as invalidated.

- [ ] **Step 3.2: Run tests to verify failure**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_conflict_session.py -v`
  Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3.3: Implement `ConflictSession` (`interfaces/desktop/models/conflict_session.py`)**
  - Inherits from `QObject`.
  - Signals: `sessionChanged`, `currentHunkIndexChanged`, `canSaveChanged`.
  - Implements tagged delimiter generator: `<<<<<<< [LOCAL:hunk_{i}]`, `=======`, `>>>>>>> [CANONICAL:hunk_{i}]`.
  - Implements clean candidate generator extracting resolved or local fallback lines.
  - Implements navigation: `next_hunk()`, `prev_hunk()`.

- [ ] **Step 3.4: Run tests to verify they pass**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_conflict_session.py -v`
  Expected: PASS (10 tests)

- [ ] **Step 3.5: Commit conflict session model**
  ```bash
  git add interfaces/desktop/models/conflict_session.py tests/unit/test_conflict_session.py
  git commit -m "feat(phase10f): add conflict session presentation model"
  ```

---

### Task 4: Editor Controller Integration, Auto-Merge & Save Gating
**Files:**
- Modify: `interfaces/desktop/controllers/markdown_editor_controller.py`
- Modify: `interfaces/desktop/app.py`
- Test: `tests/unit/test_markdown_editor_conflict.py`

**Interfaces:**
- Consumes: `MarkdownMergeService`, `ConflictSession`.
- Produces: `MarkdownEditorController` with `mergeSessionActive`, `currentConflictLabel`, `autoMergeNotification`, slots `acceptCurrentHunkLocal()`, `acceptCurrentHunkIncoming()`, `acceptCurrentHunkBoth()`, `nextConflictHunk()`, `prevConflictHunk()`.

- [ ] **Step 4.1: Write failing editor controller conflict tests (`test_markdown_editor_conflict.py`)**
  Implement test cases:
  - `T-MERGE-40`: When dirty and external advance arrives, increments `_merge_session_id` and submits background task to executor.
  - `T-MERGE-41`: Clean auto-merge: sets `_source_text = MERGED`, `_saved_source_text = canonical_text`, `_active_version = N+1`, `_is_dirty = True`, `_has_conflict = False`, emits `autoMergeNotified`.
  - `T-MERGE-42`: Overlapping conflict: instantiates `ConflictSession`, sets `_has_conflict = True`, sets `_source_text` to in-buffer tagged markers.
  - `T-MERGE-43`: Save is strictly blocked while `_active_conflict_session` has unresolved hunks.
  - `T-MERGE-44`: When all hunks resolved, `canSave` is True; `save()` passes `base_version = canonical_version` to `commit_source_text()`.
  - `T-MERGE-45`: Second canonical advance ($N+1 \to N+2$) invalidates active session, extracts clean candidate from S1, triggers S2 against $N+2$ without passing markers.
  - `T-MERGE-46`: Discard clears active conflict session, resets `_has_conflict`, reloads canonical.
  - `T-MERGE-47`: Stale merge analysis result (matching old `session_id`) is dropped cleanly.

- [ ] **Step 4.2: Run tests to verify failure**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_markdown_editor_conflict.py -v`
  Expected: FAIL with missing attributes

- [ ] **Step 4.3: Implement editor controller integration**
  - Update `MarkdownEditorController.__init__`: accept `merge_service: Optional[MarkdownMergeService] = None`.
  - Add attributes: `_merge_session_id = 0`, `_active_conflict_session = None`, `_auto_merge_notification = ""`.
  - Add signals: `mergeSessionStateChanged`, `autoMergeNotified`.
  - Add properties: `mergeSessionActive`, `currentConflictIndex`, `totalConflicts`, `currentConflictLabel`, `canSaveConflict`, `autoMergeNotification`.
  - Update `notifyCanonicalDocumentAdvance(new_doc_ver)`:
    - If `_is_dirty`: increment `_merge_session_id`, submit `merge_service.analyze_three_way_merge` to `_executor`.
  - Implement `_on_internal_merge_analyzed(session_id, result_dto)`:
    - Validate `session_id == self._merge_session_id`.
    - If clean: apply auto-merge state semantics.
    - If conflict: activate `ConflictSession`, project in-buffer markers.
  - Implement resolution slots: `acceptCurrentHunkLocal()`, `acceptCurrentHunkIncoming()`, `acceptCurrentHunkBoth()`, `nextConflictHunk()`, `prevConflictHunk()`.
  - Update `save()`: metadata-based gating; pass `base_version = session.canonical_version`.
  - In `app.py`: instantiate `MarkdownMergeService` and pass to `MarkdownEditorController`.

- [ ] **Step 4.4: Run tests to verify they pass**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_markdown_editor_conflict.py -v`
  Expected: PASS (8 tests)

- [ ] **Step 4.5: Commit editor controller integration**
  ```bash
  git add interfaces/desktop/controllers/markdown_editor_controller.py interfaces/desktop/app.py tests/unit/test_markdown_editor_conflict.py
  git commit -m "feat(phase10f): integrate conflict session, auto-merge, and save gating in editor controller"
  ```

---

### Task 5: Preview Pause / Resume & Overlay Coordination
**Files:**
- Modify: `interfaces/desktop/controllers/markdown_viewer_controller.py`
- Modify: `interfaces/desktop/coordinators/review_workspace_sync_coordinator.py`
- Modify: `interfaces/desktop/app.py`
- Modify: `interfaces/desktop/qml/views/MarkdownView.qml`
- Test: `tests/unit/test_markdown_preview_pause.py`

**Interfaces:**
- Consumes: `MarkdownViewerController`, `ReviewWorkspaceSyncCoordinator`.
- Produces: `MarkdownViewerController.previewPaused`, `previewPausedReason`, `setPreviewPaused(paused, reason)`.

- [ ] **Step 5.1: Write failing preview pause tests (`test_markdown_preview_pause.py`)**
  Implement test cases:
  - `T-MERGE-50`: `setPreviewPaused(True, reason)` halts live preview scheduling and sets properties.
  - `T-MERGE-51`: While paused, typing in editor does not dispatch live preview renders.
  - `T-MERGE-52`: While paused, `ReviewWorkspaceSyncCoordinator` mutes preview scroll synchronization.
  - `T-MERGE-53`: `setPreviewPaused(False)` resumes live preview and updates presentation model.
  - `T-MERGE-54`: Out-of-order preview callbacks completing after pause are dropped.

- [ ] **Step 5.2: Run tests to verify failure**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_markdown_preview_pause.py -v`
  Expected: FAIL with missing properties

- [ ] **Step 5.3: Implement preview pause & overlay**
  - In `MarkdownViewerController`:
    - Add `previewPaused: bool = False`, `previewPausedReason: str = ""`.
    - Add signal `previewPausedChanged`.
    - Add `@Slot(bool, str) setPreviewPaused(paused: bool, reason: str = "")`.
    - In `scheduleLivePreview`: if `self._preview_paused: return`.
  - In `ReviewWorkspaceSyncCoordinator`:
    - In `report_source_scroll_progress`: if `self.viewer_controller.previewPaused: return`.
  - In `interfaces/desktop/app.py`:
    - Wire `editor.mergeSessionStateChanged`:
      - If `editor.hasConflict`: `viewer.setPreviewPaused(True, "Preview paused during conflict resolution")`.
      - Else if all resolved: `viewer.setPreviewPaused(False)` and call `viewer.scheduleLivePreview(...)`.
  - In `MarkdownView.qml`:
    - Add `previewPausedOverlay` rectangle covering preview pane with dimmed background and pause notice.

- [ ] **Step 5.4: Run tests to verify they pass**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_markdown_preview_pause.py -v`
  Expected: PASS (5 tests)

- [ ] **Step 5.5: Commit preview pause and overlay**
  ```bash
  git add interfaces/desktop/controllers/markdown_viewer_controller.py interfaces/desktop/coordinators/review_workspace_sync_coordinator.py interfaces/desktop/app.py interfaces/desktop/qml/views/MarkdownView.qml tests/unit/test_markdown_preview_pause.py
  git commit -m "feat(phase10f): add preview pause state, overlay banner, and coordinator scroll muting"
  ```

---

### Task 6: QML Conflict Toolbar & Job Switch Guard
**Files:**
- Create: `interfaces/desktop/qml/components/ConflictResolutionBar.qml`
- Modify: `interfaces/desktop/qml/components/MarkdownEditorPane.qml`
- Modify: `interfaces/desktop/qml/Main.qml`
- Test: `tests/unit/test_review_workspace_conflict_qml.py`

**Interfaces:**
- Consumes: `MarkdownEditorController` QML properties.
- Produces: `ConflictResolutionBar.qml` interactive controls, `jobSwitchConfirmModal` in `Main.qml`.

- [ ] **Step 6.1: Write failing QML integration tests (`test_review_workspace_conflict_qml.py`)**
  Implement test cases:
  - `T-MERGE-60`: `ConflictResolutionBar` becomes visible when `editorController.mergeSessionActive == True`.
  - `T-MERGE-61`: Toolbar buttons invoke controller slots (`acceptCurrentHunkLocal`, etc.).
  - `T-MERGE-62`: Save button in editor pane disabled while conflict active and enabled when resolved.
  - `T-MERGE-63`: Auto-merge notification toast displays message.
  - `T-MERGE-64`: `openReviewWorkspace(other_job)` while dirty/conflicting triggers confirmation dialog; Cancel stays, Discard switches.

- [ ] **Step 6.2: Run tests to verify failure**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_review_workspace_conflict_qml.py -v`
  Expected: FAIL

- [ ] **Step 6.3: Implement QML components and bindings**
  - Create `interfaces/desktop/qml/components/ConflictResolutionBar.qml`:
    - Displays: `controller.currentConflictLabel` (e.g. `"Conflict 1 of 2 in Paragraph"`).
    - Buttons: `[Prev]`, `[Next]`, `[Accept Local]`, `[Accept Incoming]`, `[Accept Both]`.
  - Embed `ConflictResolutionBar` in `MarkdownEditorPane.qml` immediately above `sourceTextArea`.
  - Update `MarkdownEditorPane.qml` Save button binding: `enabled: controller && controller.isDirty && !controller.isSaving && !controller.hasConflict`.
  - In `Main.qml`:
    - Intercept `openReviewWorkspace(jobId)`: if dirty/conflict on current job, open `jobSwitchConfirmModal`.
    - Actions: `[Cancel]` aborts, `[Discard & Switch]` calls `markdownEditorController.clear()` and loads target job.

- [ ] **Step 6.4: Run tests to verify they pass**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_review_workspace_conflict_qml.py -v`
  Expected: PASS (5 tests)

- [ ] **Step 6.5: Commit QML toolbar and job-switch guard**
  ```bash
  git add interfaces/desktop/qml/components/ConflictResolutionBar.qml interfaces/desktop/qml/components/MarkdownEditorPane.qml interfaces/desktop/qml/Main.qml tests/unit/test_review_workspace_conflict_qml.py
  git commit -m "feat(phase10f): add conflict resolution toolbar and job-switch confirmation dialog"
  ```

---

### Task 7: Full System Verification & Regression Gate
**Files:** All test suites across the repository.

- [ ] **Step 7.1: Run full automated pytest suite**
  Run: `/home/amirreza-a2a/madarsol/venv/bin/pytest -v`  
  *Gate: All tests (existing 757 + 52 new Phase 10F.5 tests = 809 total) MUST pass with 0 failures, 0 errors.*

- [ ] **Step 7.2: Verify git diff hygiene**
  Run: `git diff --check`  
  *Gate: Clean, 0 whitespace warnings.*

- [ ] **Step 7.3: Verify architectural boundaries**
  - Confirm `core/` contains zero Qt/PySide6/PyQt6/SQLite imports.
  - Confirm `application/` contains zero Qt/PySide6/PyQt6/SQLite imports.
  - Confirm zero raw conflict markers persisted in SQLite.

---

## 4. Comprehensive Test Matrix (T-MERGE-01 through T-MERGE-67)

| Test ID | Module | Purpose & Expected Behavior |
| :--- | :--- | :--- |
| `T-MERGE-01` | Core (`merge.py`) | Clean local-only edit against Base merges without conflict. |
| `T-MERGE-02` | Core (`merge.py`) | Clean remote-only edit (e.g. region token update) merges without conflict. |
| `T-MERGE-03` | Core (`merge.py`) | Disjoint edits (local line 5, remote line 50) apply cleanly in sequence. |
| `T-MERGE-04` | Core (`merge.py`) | Identical concurrent edits on same line are deduplicated cleanly (`CLEAN_SAME`). |
| `T-MERGE-05` | Core (`merge.py`) | Overlapping differing modifications on same lines produce a `CONFLICT` hunk. |
| `T-MERGE-06` | Core (`merge.py`) | Delete vs. modify on overlapping line span flags a `CONFLICT`. |
| `T-MERGE-07` | Core (`merge.py`) | Modify vs. delete on overlapping line span flags a `CONFLICT`. |
| `T-MERGE-08` | Core (`merge.py`) | Differing insertions at the exact same base line offset flag a `CONFLICT`. |
| `T-MERGE-09` | Core (`merge.py`) | Adjacent non-overlapping edits at lines $10$ and $11$ merge cleanly. |
| `T-MERGE-10` | Core (`merge.py`) | Repeated blank lines and markdown separators do not cause diff phase shifts. |
| `T-MERGE-11` | Core (`merge.py`) | Exact line indentation and whitespace preserved without normalization. |
| `T-MERGE-12` | Core (`merge.py`) | CRLF line endings normalized for diff3; original CRLF preserved on output. |
| `T-MERGE-13` | Core (`merge.py`) | Presence or absence of trailing newline preserved deterministically. |
| `T-MERGE-14` | Core (`merge.py`) | Insertion at EOF without trailing newline merges cleanly without extra lines. |
| `T-MERGE-15` | Core (`merge.py`) | Empty document Base/Local/Remote handled safely without index errors. |
| `T-MERGE-16` | Core (`merge.py`) | Markdown visual region token updates merge cleanly. |
| `T-MERGE-17` | Core (`merge.py`) | Source text containing literal `<<<<<<<` inside code fences does not confuse delimiters. |
| `T-MERGE-20` | Application (`service`) | Base $vN$ and Canonical $vM$ artifacts loaded asynchronously from storage. |
| `T-MERGE-21` | Application (`service`) | Missing base artifact falls back to empty string without raising error. |
| `T-MERGE-22` | Application (`service`) | Clean merge returns DTO with `has_conflicts=False` and merged text. |
| `T-MERGE-23` | Application (`service`) | Overlapping merge returns DTO with `has_conflicts=True` and hunks. |
| `T-MERGE-24` | Application (`service`) | Conflict hunks enriched with AST context (node type, heading text, region ID). |
| `T-MERGE-25` | Application (`service`) | Unparseable/malformed AST falls back to line-based label cleanly. |
| `T-MERGE-26` | Application (`service`) | Analysis executes zero SQLite transactions and creates zero files. |
| `T-MERGE-30` | Presentation (`session`) | Session initializes with hunks, `currentHunkIndex = 0`. |
| `T-MERGE-31` | Presentation (`session`) | `generate_in_buffer_markdown()` formats tagged collision-safe markers. |
| `T-MERGE-32` | Presentation (`session`) | `resolve_hunk(0, "local")` sets local text and marks hunk resolved. |
| `T-MERGE-33` | Presentation (`session`) | `resolve_hunk(0, "both")` on replace/replace concatenates local and remote. |
| `T-MERGE-34` | Presentation (`session`) | `resolve_hunk(0, "both")` on delete/modify emits remote modifications. |
| `T-MERGE-35` | Presentation (`session`) | `is_fully_resolved()` returns False until all hunks resolved, then True. |
| `T-MERGE-36` | Presentation (`session`) | `generate_candidate_markdown()` produces clean text with zero markers. |
| `T-MERGE-37` | Presentation (`session`) | Partial resolutions fallback to Local text for unresolved hunks (D06 extraction). |
| `T-MERGE-38` | Presentation (`session`) | Delimiter generation prevents collision with legitimate markdown markers. |
| `T-MERGE-39` | Presentation (`session`) | `invalidate()` flags session as invalidated. |
| `T-MERGE-40` | Controller (`editor`) | External advance while dirty triggers background merge analysis. |
| `T-MERGE-41` | Controller (`editor`) | Clean auto-merge: sets text, updates saved source to canonical, re-bases version to $N+1$, retains dirty flag, emits toast notification. |
| `T-MERGE-42` | Controller (`editor`) | Overlapping conflict: activates `ConflictSession`, sets `_has_conflict = True`, loads tagged markers into `_source_text`. |
| `T-MERGE-43` | Controller (`editor`) | Save is strictly blocked while `ConflictSession` has unresolved hunks. |
| `T-MERGE-44` | Controller (`editor`) | When all hunks resolved, `canSave` is True; `save()` passes `base_version = canonical_ver` to OCC. |
| `T-MERGE-45` | Controller (`editor`) | D06: Second canonical advance ($N+1 \to N+2$) invalidates active session, extracts clean candidate from S1, triggers S2 against $N+2$ without markers. |
| `T-MERGE-46` | Controller (`editor`) | Discard clears active conflict session, resets conflict flag, reloads canonical. |
| `T-MERGE-47` | Controller (`editor`) | Stale merge analysis result (matching old `session_id`) is dropped cleanly. |
| `T-MERGE-50` | Controller (`viewer`) | `setPreviewPaused(True, reason)` halts live preview scheduling. |
| `T-MERGE-51` | Controller (`viewer`) | While paused, editor keystrokes do not trigger live preview renders. |
| `T-MERGE-52` | Coordinator (`sync`) | While paused, `ReviewWorkspaceSyncCoordinator` mutes preview scroll synchronization. |
| `T-MERGE-53` | Controller (`viewer`) | `setPreviewPaused(False)` resumes live preview and updates presentation model. |
| `T-MERGE-54` | Controller (`viewer`) | Stale in-flight preview renders completing after pause are discarded. |
| `T-MERGE-60` | QML (`toolbar`) | `ConflictResolutionBar` becomes visible when `mergeSessionActive == True`. |
| `T-MERGE-61` | QML (`toolbar`) | Toolbar buttons trigger controller resolution slots. |
| `T-MERGE-62` | QML (`editor`) | Save button disabled while conflicts active, enabled when resolved. |
| `T-MERGE-63` | QML (`toast`) | Auto-merge notification toast displays message and auto-fades. |
| `T-MERGE-64` | QML (`main`) | `openReviewWorkspace(other_job)` while dirty shows confirmation dialog; Cancel stays, Discard switches. |
| `T-MERGE-65` | QML (`editor`) | TextArea undo/redo operates normally inside conflict marker blocks. |
| `T-MERGE-66` | QML (`preview`) | Preview overlay rectangle covers preview pane with dimmed amber notice while paused. |
| `T-MERGE-67` | QML (`main`) | Window close while dirty/conflicting prompts user for confirmation. |

---

## 5. File Impact Summary

### Files to Create
1. `core/markdown/merge.py`: Pure line-based `diff3` three-way merge engine and dataclasses.
2. `application/dtos/merge_dto.py`: Transport-neutral DTOs for merge results and conflict hunks.
3. `application/services/markdown_merge_service.py`: Application service loading artifacts and enriching AST context.
4. `interfaces/desktop/models/conflict_session.py`: Presentation model managing active hunks and resolution state.
5. `interfaces/desktop/qml/components/ConflictResolutionBar.qml`: QML toolbar for conflict navigation and actions.
6. `tests/unit/test_markdown_diff3_merge.py`: Core diff3 unit tests (T-MERGE-01..17).
7. `tests/unit/test_markdown_merge_service.py`: Application service tests (T-MERGE-20..26).
8. `tests/unit/test_conflict_session.py`: Conflict session model tests (T-MERGE-30..39).
9. `tests/unit/test_markdown_editor_conflict.py`: Editor controller integration tests (T-MERGE-40..47).
10. `tests/unit/test_markdown_preview_pause.py`: Preview pause and overlay tests (T-MERGE-50..54).
11. `tests/unit/test_review_workspace_conflict_qml.py`: QML integration tests (T-MERGE-60..67).

### Files to Modify
1. `interfaces/desktop/controllers/markdown_editor_controller.py`: Add `merge_session_id`, background merge dispatch, auto-merge state transition, hunk resolution slots, metadata Save gating.
2. `interfaces/desktop/controllers/markdown_viewer_controller.py`: Add `previewPaused`, `previewPausedReason`, live preview suppression.
3. `interfaces/desktop/coordinators/review_workspace_sync_coordinator.py`: Mute preview scrolling while preview is paused.
4. `interfaces/desktop/app.py`: Construct `MarkdownMergeService`, wire editor merge signals to viewer preview pause/resume.
5. `interfaces/desktop/qml/components/MarkdownEditorPane.qml`: Embed `ConflictResolutionBar`, update Save gating binding.
6. `interfaces/desktop/qml/views/MarkdownView.qml`: Add `previewPausedOverlay`.
7. `interfaces/desktop/qml/Main.qml`: Guard `openReviewWorkspace` with `jobSwitchConfirmModal`.

### Files Explicitly Forbidden From Modification
- `core/entities/job.py`, `core/entities/visual_region.py` (Zero entity schema mutations).
- `infrastructure/persistence/sqlite/` (Zero SQLite migrations or schema modifications).
- `application/services/apply_review_service.py`, `application/services/markdown_editor_service.py` (Existing OCC save reused as-is).

---

## 6. Self-Review & Readiness Gate

1. **Can an implementation agent implement diff3 without inventing conflict semantics?**  
   *Yes.* Section 2.6 specifies the exact SequenceMatcher alignment, span partitioning, and categorization matrix for all 15 corner cases.
2. **Can an implementation agent resolve a hunk without relying on marker-string parsing?**  
   *Yes.* Section 2.3 & 2.7 define `ConflictSession` as the authoritative model where hunks are stored and resolved by index, not by text parsing.
3. **Can an implementation agent handle D06 without passing marker text into diff3?**  
   *Yes.* Section 2.4 specifies the exact reconstruction algorithm that falls back to Local for unresolved hunks and emits zero marker lines.
4. **Can an implementation agent implement Save gating without corrupting legitimate Markdown?**  
   *Yes.* Section 2.5 establishes metadata-based gating (`_active_conflict_session.is_fully_resolved()`) rather than substring matching.
5. **Can an implementation agent integrate job-switch protection at the actual repository signal?**  
   *Yes.* Section 2.9 pins the exact interception point to `openReviewWorkspace(jobId)` in `Main.qml`.
6. **Can an implementation agent implement async stale-result rejection without guessing thread ownership?**  
   *Yes.* Task 4 pins execution to `MarkdownEditorController._executor` with `session_id == self._merge_session_id` validation on the GUI thread.
7. **Can all critical state transitions be implemented from the plan alone?**  
   *Yes.* Section 2.2 defines auto-merge state precisely, and Section 4 maps every test ID to concrete expected behavior.

---

### Final Classification

`READY FOR IMPLEMENTATION`
