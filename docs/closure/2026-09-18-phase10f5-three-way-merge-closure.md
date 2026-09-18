# Phase 10F.5 Closure Report: Three-Way Merge & Visual Conflict Diff Resolution

- **Status:** CLOSED
- **Date:** 2026-09-18
- **Phase:** 10F.5 — Three-Way Merge / Visual Conflict Diff Resolution
- **Scope:** Desktop Review Workspace (`MarkdownEditorPane`, `MarkdownView`, `ReviewWorkspaceView`)
- **Baseline Commit:** `9a242f4` (Phase 10F.4 formal closure baseline)
- **Closure HEAD:** `a1a02f9`
- **Classification:** **`CLOSED`**

---

## 1. Executive Summary & Objective

Phase 10F.5 delivers deterministic, format-preserving three-way merge (`diff3`) and interactive in-buffer visual conflict diff resolution within the PolpoT desktop Review Workspace.

Prior to Phase 10F.5, external canonical document advances (such as PDF visual region re-crops or background pipeline commits) colliding with uncommitted local editor drafts resulted in crude conflict locks or potential overwrite hazards under Optimistic Concurrency Control (OCC).

Phase 10F.5 successfully resolves this by establishing:
1. Seamless, automatic merging of non-overlapping concurrent changes (e.g., visual region token edits colliding with disjoint user text edits) without user intervention.
2. Safe, guided in-buffer manual conflict resolution for overlapping edits using collision-safe tagged delimiters, an interactive presentation toolbar, and strict metadata-based save gating.
3. Clean preview coordination where rendered preview and synchronized scroll tracking suspend gracefully during active conflicts, preventing raw conflict markers from corrupting Markdown AST renderers.

---

## 2. Implementation Scope & Commit History

The implementation spanned 14 commits across core algorithms, application services, presentation models, Qt controllers, QML views, and comprehensive unit/integration test suites:

- `9dba969` — `feat(phase10f): add pure python diff3 three-way merge engine`
- `c67be29` — `fix(phase10f): address Task 1 review findings for diff3 merge engine`
- `94349eb` — `feat(phase10f): add application merge service and DTO contracts`
- `d216817` — `feat(phase10f): add conflict session presentation model`
- `d514ddb` — `feat(phase10f): integrate conflict session, auto-merge, and save gating in editor controller`
- `7a10eee` — `fix(phase10f): address Task 4 review findings for editor controller conflict integration`
- `2f92716` — `feat(phase10f): add preview pause state, overlay banner, and coordinator scroll muting`
- `3514220` — `fix(phase10f): address Task 5 review polish observations`
- `537da00` — `feat(phase10f): add conflict resolution toolbar and job-switch confirmation dialog`
- `cb545db` — `fix(phase10f): address Task 6 review observations for save gating and test teardown`
- `0e0c1e1` — `test(phase10f): remove unused imports in test_markdown_merge_service.py`
- `49dd6ef` — `style(phase10f): clean up trailing whitespace in diff3 engine and tests`
- `4c9c34f` — `fix(phase10f): preserve manual buffer edits, fix preview pause and window exit (F-01..F-04, F-06)`
- `a1a02f9` — `test(phase10f): add missing planned QML conflict and exit confirmation tests (F-05)`

**Net Branch Statistics:** 20 files modified/added (+4393 / -32 lines).

---

## 3. Compliance with Settled Decisions (ADR-001: D01–D08)

| Decision | Specification Summary | Implementation Evidence | Verdict |
| :--- | :--- | :--- | :--- |
| **D01: Conflict Persistence Scope** | Session-only transient resolution; zero SQLite draft schema mutations. | Verified. `ConflictSession` lives strictly in presentation memory. Zero database schema tables, columns, or transactions created for merge sessions. | **COMPLIANT** |
| **D02: Merge Engine Foundation** | Pure Python standard-library `diff3` algorithm (`core/markdown/merge.py`) + read-only AST context enrichment. | Verified. Core diff3 is implemented using functional `difflib.SequenceMatcher` projections on line tokens. AST querying in `MarkdownMergeService` is strictly read-only for labeling hunks; zero AST-to-text serialization. | **COMPLIANT** |
| **D03: Concurrent Edit Disposition** | Non-overlapping changes auto-merge cleanly; advances version, retains dirty flag, displays toast. | Verified. Clean auto-merge updates `_source_text`, re-bases `_saved_source_text`, increments `_active_version`, maintains `_is_dirty = True`, and displays the emerald auto-merge banner. | **COMPLIANT** |
| **D04: Interactive In-Buffer Conflict Resolution** | Delimited conflict markers in editor buffer with interactive toolbar; `ConflictSession` is authoritative state owner. | Verified. Tagged collision-safe delimiters (`<<<<<<< [LOCAL:hunk_{i}]`, `=======`, `>>>>>>> [CANONICAL:hunk_{i}]`). `ConflictSession` tracks state; `ConflictResolutionBar.qml` drives navigation and actions. | **COMPLIANT** |
| **D05: Preview Suspended During Conflict** | Rendered preview paused with overlay banner during conflict; raw markers never enter AST parser/renderer. | Verified. When `hasUnresolvedConflict == True`, `setPreviewPaused(True)` halts live preview scheduling, drops in-flight preview tasks, displays `previewPausedOverlay`, and mutes scroll synchronization. | **COMPLIANT** |
| **D06: Second Canonical Advance While Resolving** | Arriving advance ($N+1 \to N+2$) invalidates active session S1, extracts clean candidate without markers, triggers S2. | Verified. `notifyCanonicalDocumentAdvance` extracts clean candidate text via `generate_candidate_markdown()`, invalidates S1, and dispatches S2 against $N+2$ passing 0 conflict markers to diff3. | **COMPLIANT** |
| **D07: Job-Switch Guarding** | Job switching guarded by confirmation dialog at `openReviewWorkspace(jobId)` in `Main.qml`. | Verified. Switching jobs while dirty or conflicting triggers `jobSwitchConfirmModal`. *Cancel* preserves state; *Discard & Switch* clears editor and opens new job. | **COMPLIANT** |
| **D08: Monotonic Session Tokens** | Monotonic generation token prevents race conditions and drops stale async analysis responses. | Verified. `self._merge_session_id` increments monotonically. Out-of-order responses matching older session IDs are discarded immediately in `_on_internal_merge_analyzed`. | **COMPLIANT** |

---

## 4. Status of Remediated Findings (F-01 through F-06)

All six findings identified during the initial independent forensic code review have been remediated and verified:

1. **`F-01` (High) — Preview Unpause on Resolution Completion:**  
   *Remediation:* `interfaces/desktop/app.py` wires `_on_conflict_or_merge_state_changed` to `markdown_editor_controller.hasUnresolvedConflict`. The moment the final conflict hunk is resolved, `setPreviewPaused(False)` is invoked and live preview renders `cleanCandidateText` immediately without waiting for disk commit. Verified in `test_preview_resumes_immediately_when_all_conflicts_resolved`.
2. **`F-02` (High) — Manual In-Buffer Edit Preservation:**  
   *Remediation:* `ConflictSession.sync_from_buffer()` and `apply_resolution_to_buffer()` parse live buffer text, preserve user modifications inside markers in `_current_local_texts`, detect manual marker deletion, and surgically replace only the target hunk span. The editor remains 100% writable (no read-only lock). Verified in `test_manual_edits_preserved_after_hunk_resolution` and `test_in_marker_manual_edit_preserved_on_accept_local`.
3. **`F-03` (Medium) — Window Exit Confirmation on Dirty / Conflicting State:**  
   *Remediation:* `Main.qml` intercepts `onClosing` when `isDirty`, `hasConflict`, or `mergeSessionActive` is true, rejecting close and presenting `windowExitConfirmModal`. *Cancel* preserves workspace; *Discard & Exit* forces clean window termination. Verified in `test_window_exit_confirmation_dialog_cancel_and_discard`.
4. **`F-04` (Medium) — Hunk Navigation Viewport Centering & Caret Focus:**  
   *Remediation:* `_navigate_to_current_hunk_position()` locates the target hunk's tagged marker in `_source_text` and emits `requestNavigateToPosition(pos)`, centering the editor and positioning the cursor. Verified in `test_navigation_emits_request_navigate_to_position`.
5. **`F-05` (Low) — Missing Planned QML & Regression Tests:**  
   *Remediation:* Added `T-MERGE-65` (undo/redo inside markers), `T-MERGE-66` (preview paused overlay visibility), `T-MERGE-67` (window close guard), and regression tests `T-MERGE-68` through `T-MERGE-75`.
6. **`F-06` (Low) — Dead Code & Save Candidate Duplication:**  
   *Remediation:* Consolidated clean-text preparation logic into `_prepare_save_candidate()` shared by both `save()` and `save_sync()`. Cleaned unused imports.

---

## 5. Architectural Invariants & Semantic Distinctions

Phase 10F.5 establishes three critical architectural invariants that govern the desktop review editing lifecycle:

### 5.1 The Three-Way Merge Vector: `BASE + LOCAL DRAFT + CURRENT CANONICAL`

Under Optimistic Concurrency Control (OCC), document state is non-linear during concurrent editing:
- **`BASE` ($vN$):** The immutable document version that was loaded when the user started editing the current buffer. It represents the common ancestor artifact (`output_{job}_v{base_version}.md`).
- **`LOCAL DRAFT`:** The user's live, uncommitted editor buffer. It may contain text additions, formatting changes, and manual edits.
- **`CURRENT CANONICAL` ($vM$, where $M > N$):** The latest immutable document committed to storage by background pipelines or external tool operations (e.g., visual region re-cropping).

The merge engine computes diff projections $(BASE \to LOCAL)$ and $(BASE \to CANONICAL)$ to produce either a clean merged document or isolated conflict hunks.

### 5.2 Authoritative Raw Markdown vs. Read-Only AST Context

- **Authoritative State:** The raw Markdown text is the sole source of truth for both editing and three-way merging. The merge engine operates strictly on raw text lines with exact newline and whitespace preservation.
- **Read-Only AST Context:** The CommonMark AST (`MarkdownDocumentModel` / `MarkdownViewerService`) is queried strictly in a read-only manner to enrich conflict hunks with human-readable semantic labels (e.g., *"Heading 2: Results"* or *"Visual Region: crop_12"*). **The AST is never serialized back into Markdown text**, preventing any AST round-trip syntax corruption.

### 5.3 Token Disambiguation Matrix

To eliminate confusion between the various revision identifiers in the desktop runtime:
- **`merge_session_id` (int):** A monotonic in-memory sequence token incremented by `MarkdownEditorController` every time external document advance triggers merge analysis. It binds asynchronous background tasks to presentation state and cleanly drops stale out-of-order analysis responses.
- **`active_version` (int):** The canonical OCC document watermark currently associated with the editor's base state. Advances to $vM$ upon successful save or clean auto-merge.
- **`draft_revision` (int):** A presentation-level generation counter on `MarkdownEditorController` tracking in-buffer keystrokes and debounced live-preview triggers.
- **`model_generation` (int):** An immutable integer token on `MarkdownDocumentModel` matching the rendered AST block hierarchy to the source text version that generated it, guarding synchronized scroll and cursor tracking against stale geometry.

---

## 6. Verification Results

### 6.1 Full Test Suite Verification

```text
============================= 822 passed in 40.01s =============================
```

- **Total Test Count:** **822 passed**, 0 failed, 0 skipped.
- **Dedicated Merge Test Modules (65 tests total):**
  1. `tests/unit/test_markdown_diff3_merge.py` — **17 passed** (`T-MERGE-01`..`17`)
  2. `tests/unit/test_markdown_merge_service.py` — **7 passed** (`T-MERGE-20`..`26`)
  3. `tests/unit/test_conflict_session.py` — **15 passed** (`T-MERGE-30`..`39`, `test_navigation_next_prev_and_labels`, `T-MERGE-68`..`71`)
  4. `tests/unit/test_markdown_editor_conflict.py` — **13 passed** (`T-MERGE-40`..`47`, `test_resolution_slots_incoming_and_both`, `T-MERGE-72`..`75`)
  5. `tests/unit/test_markdown_preview_pause.py` — **5 passed** (`T-MERGE-50`..`54`)
  6. `tests/unit/test_review_workspace_conflict_qml.py` — **8 passed** (`T-MERGE-60`..`67`)
  
  *(Note on documentation correction: The interim summary previously cited an unverified count of 47 tests. Actual repository inspection confirms 65 dedicated merge tests across these 6 modules, summing to 822 passed across the full test suite).*

### 6.2 Architecture Boundary & Security Invariants

```text
tests/unit/test_architecture_boundaries.py ......................... [PASSED]
tests/unit/test_architecture_events_and_serverless.py ............. [PASSED]
tests/unit/test_architecture_lifecycle.py .......................... [PASSED]
tests/unit/test_architecture_security.py ........................... [PASSED]
tests/unit/test_secret_non_persistence.py .......................... [PASSED]
============================== 22 passed in 3.07s ==============================
```

- **Clean Architecture Purity:** `core/markdown/merge.py` and `application/services/markdown_merge_service.py` contain zero imports of Qt, PySide6, or SQLite.
- **Serverless & Local-First:** Zero HTTP servers, zero listening ports, zero server dependencies.
- **Secret Security:** Zero plaintext credentials or keys persisted, logged, or exposed in DTOs.
- **Hygiene:** `git diff --check` is 100% clean.

---

## 7. Remaining Low-Severity Technical Debt

The three residual observations identified during the final forensic code review are documented as non-blocking technical debt:

1. **`application/dto/merge_dto.py` (Low):** A 10-line backward-compatibility re-export shim created during early task scaffolding. Retained as harmless non-blocking code; can be pruned in a future cleanup milestone.
2. **Unused `uow_factory` Parameter (Low):** `MarkdownMergeService.__init__` accepts `uow_factory: IUnitOfWorkFactory` for interface uniformity with other application services, but does not use it because merge analysis is strictly zero-write.
3. **Repeated Resolution String Search Fallback (Low / Edge Case):** If a user repeatedly clicks resolution buttons for a hunk whose markers were already stripped from the buffer, `ConflictSession.apply_resolution_to_buffer` falls back to `buffer_text.find(prev_res)`. While completely robust for standard forward workflows, character span indexing can be introduced in a future pass if re-resolving resolved hunks becomes an active product feature.

---

## 8. Final Classification

All requirements of Phase 10F.5, decisions D01 through D08 in ADR-001, the implementation plan, and the standing rules in `AGENTS.md` are satisfied.

**Phase 10F.5 is formally:**

# `CLOSED`
