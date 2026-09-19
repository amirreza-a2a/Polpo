# Phase 10F.4 Closure Report & Milestone Retrospective

## Metadata

- **Phase:** 10F.4 — Source ↔ Preview Synchronized Cursor & Scroll Tracking
- **Repository:** `~/DevelopPOlpo/PolpoT`
- **Branch:** `master`
- **Baseline Commit:** `6514f76` (`docs(phase10f): add live dual-pane preview plan`)
- **Final Implementation Commit:** `457072d` (`feat(phase10f): remediate continuous scroll synchronization and viewport stability`)
- **Closure Date:** 2026-09-18
- **Status:** **PHASE 10F.4 CLOSED**

---

## 1. Executive Summary

Phase 10F.4 establishes bidirectional, smooth, and feedback-loop-immune cursor and scroll synchronization between the native Markdown source editor and the rendered Markdown preview in the desktop Review Workspace.

Building upon Phase 10F.3's live dual-pane preview architecture, Phase 10F.4 captures 1-indexed source line intervals from CommonMark AST tokens, maps line numbers to AST block nodes in presentation models, and orchestrates viewport and cursor synchronization through a dedicated presentation coordinator (`ReviewWorkspaceSyncCoordinator`).

Following an independent forensic code review that identified discrete block jumping in the initial implementation, a comprehensive continuous-scroll remediation was implemented. Viewport scrolling was transformed from discrete block index jumps to continuous normalized scroll progress (`0.0` to `1.0`) with leading-edge 16ms throttling, directional sync locks, and `ListView.Beginning` tall-block alignment. All 757 automated tests pass, and manual desktop GUI testing confirms responsive, smooth, modern IDE-style synchronization.

---

## 2. Phase 10F.4 Commit Chain

The complete Phase 10F.4 commit sequence on `master`:

1. `49749f7` — `docs(phase10f): add Phase 10F.4 source preview synchronization plan`
   Comprehensive architectural specification, data structures, and execution roadmap for synchronized cursor and scroll tracking.
2. `2d0f183` — `feat(phase10f): add AST line intervals, model mapping, and ReviewWorkspaceSyncCoordinator`
   AST interval capture in `MarkdownDocument` / `MarkdownDocumentDTO`, `MarkdownDocumentModel` line-to-node lookup, and `ReviewWorkspaceSyncCoordinator` foundation with directional lock.
3. `85a43ba` — `feat(phase10f): wire synchronized cursor and scroll tracking in dual-pane QML views`
   Dual-pane QML event binding across `MarkdownEditorPane`, `MarkdownView`, and `ReviewWorkspaceView`, including scroll debouncing and node click navigation.
4. `457072d` — `feat(phase10f): remediate continuous scroll synchronization and viewport stability`
   Remediation to continuous normalized progress (`0.0` to `1.0`), leading-edge 16ms throttled coordination, cursor/selection preservation during preview scrolling, `ListView.Beginning` tall-block positioning, and live typing viewport stability.

---

## 3. Architecture Delivered

### 3.1 AST Line Intervals & Presentation Mapping
- **Source Line Intervals:** CommonMark parser (`markdown-it-py`) token maps (`tok.map`) are captured as 1-indexed `[start_line, end_line]` intervals in core `MarkdownDocument` AST nodes and propagated through `MarkdownDocumentDTO`.
- **Fast Model Lookup:** `MarkdownDocumentModel` provides O(N) linear scanning (or binary search across monotonic intervals) via `nodeIndexAtLine(line)` and `lineAtNodeIndex(index)`.
- **Model Generation Tracking:** `model_generation: int` counter increments on document updates, ensuring asynchronous mapping requests match the current document layout.

### 3.2 Continuous Viewport Synchronization & 16ms Throttling
- **Progress-Based Synchronization:** Normalized scroll progress ($progress = \frac{contentY}{contentHeight - height}$) is reported continuously during wheel, flick, and scrollbar drag gestures.
- **Leading-Edge 16ms Throttle:** `ReviewWorkspaceSyncCoordinator` throttles high-frequency QML scroll events with leading-edge dispatch, ensuring 60fps responsiveness without event flooding.
- **Directional Lock (`SyncOrigin`):** An explicit origin state (`SOURCE_USER` vs. `PREVIEW_USER`) prevents echo and recursive ping-pong scroll events.

### 3.3 Semantic Navigation & Selection Preservation
- **Cursor vs. Viewport Decoupling:** Preview user scrolling adjusts the editor viewport (`scrollViewportToLine` / `contentY`) without moving the editor text cursor or clearing active text selections.
- **Preview Click Navigation:** Clicking a preview block (`nodeClicked`) explicitly positions the text cursor at the block's first line and brings it smoothly into view.
- **Tall Block Positioning:** Viewport alignment positions tall blocks at `ListView.Beginning` rather than centering, preventing the top of multi-page tables or long code blocks from being pushed offscreen.
- **Live Draft Typing Stability:** Active draft updates (`apply_transient_preview`) do not jerk, bounce, or recenter the preview pane while the user is actively typing in the editor.

### 3.4 Clean Architecture & Persistence Invariants
- **Zero Database Writes:** Cursor and scroll tracking are strictly ephemeral presentation concerns. Zero SQLite transactions, zero schema changes, and zero disk writes occur during synchronization.
- **OCC Integrity Preserved:** Synchronization logic never modifies canonical Markdown versions (`jobs.active_markdown_version`) or visual region versions.
- **Strict Clean Boundaries:** `core/` and `application/` remain 100% Qt-free. All UI coordination lives in `interfaces/desktop/`.

---

## 4. Verification & Quality Evidence

### 4.1 Automated Test Verification
- **Full Suite Status:** 757 / 757 tests passed (0 failures, 0 errors) in 40.92s.
- **Baseline Comparison:** 724 tests at baseline `6514f76` (+33 new tests across Phase 10F.3 & 10F.4).
- **Key Test Suites:**
  - `tests/unit/test_markdown_ast_line_intervals.py`: Token mapping, intervals, edge cases.
  - `tests/unit/test_review_workspace_sync_coordinator.py`: Lock suppression, progress translation, tall block positioning, caret preservation.
  - `tests/unit/test_review_workspace_editor_integration.py`: QML dual-pane SplitView layout, tab switching, continuous progress signals.
  - `tests/unit/test_markdown_live_preview.py`: OCC version safety, debounced live preview, typing stability.

### 4.2 Manual Desktop GUI Verification
- Continuous bidirectional scrolling verified across short, medium, and tall multi-page documents.
- Split-screen dragging and simultaneous scrolling show zero oscillation or lockup.
- Clicking preview blocks accurately jumps the editor cursor to the exact line.
- Live typing in the editor updates the preview without jumping or losing viewport context.

### 4.3 Code Quality & Diff Hygiene
- `git diff --check`: Clean (0 whitespace or formatting errors).
- All comments, docstrings, and developer documentation written in English per `AGENTS.md`.

---

## 5. Independent Forensic Code Review Findings

The independent forensic code review classified the remediated implementation as **`READY FOR CLOSURE`**. The review identified three non-blocking observations to record for ongoing maintenance:

1. **`M-01` (Medium — Retained Legacy Hook):**
   - *Description:* `MarkdownViewerController.reportUserScrolled()` and `userScrolledNode` hook are retained solely for backward compatibility with existing unit tests. Production QML continuous scrolling uses `reportPreviewScrollProgress()`.
   - *Recommendation:* Deprecate and remove `reportUserScrolled()` in a future maintenance pass when legacy test references are updated.
2. **`L-01` (Low — Comment Formatting Clutter):**
   - *Description:* `interfaces/desktop/coordinators/review_workspace_sync_coordinator.py` contains decorative separator banners (`# -------------------------------------------------------------------------`), which strictly conflict with `AGENTS.md` Rule 20.3.
   - *Recommendation:* Replace with standard clean function/class docstrings in the next refactoring cycle.
3. **`L-02` (Low — Viewport Reset Timing):**
   - *Description:* Immediate reset of `isProgrammaticScrolling = false` in `MarkdownView.qml` within `onRequestScrollPreviewToProgress` relies heavily on the coordinator's directional lock (`SyncOrigin.SOURCE_USER`) to suppress echoes.
   - *Recommendation:* Add a small deferred reset timer (e.g., 50ms) if future asynchronous layout engines exhibit delayed scroll callbacks.

---

## 6. Retrospective

### 6.1 What Worked Well
- **Token Map Extraction:** Leveraging `markdown-it-py`'s native token mapping provided accurate, robust source line intervals without requiring custom Markdown parsers or regex hacks.
- **Dedicated Presentation Coordinator:** Isolating synchronization logic into `ReviewWorkspaceSyncCoordinator` prevented clutter in `MarkdownEditorController` and `MarkdownViewerController`, upholding Clean Architecture separation.
- **TDD Rigor:** High-coverage unit tests for coordinator states and directional locks caught race conditions before they reached QML runtime.

### 6.2 What Required Remediation & Architectural Lesson
- **The Core Issue:** The initial implementation conflated discrete semantic block identity (`node_index`) with continuous visual viewport position (`contentY`).
- **The Symptom:** Scrolling one pane caused the opposite pane to "stair-step" or snap abruptly to discrete block boundaries rather than tracking smoothly.
- **The Architectural Lesson:**
  $$\text{Semantic Navigation Identity (Discrete AST Block)} \neq \text{Continuous Visual Viewport Position (Normalized Progress)}$$
  Semantic block indexing is ideal for discrete actions (such as clicking a heading or jumping to a line), whereas viewport synchronization requires continuous geometric interpolation ($0.0 \dots 1.0$) with rate limiting. Treating them as separate concerns resolved the UX friction completely.

---

## 7. Next Steps

Phase 10F.4 is complete. The desktop Review Workspace now features:
- Live dual-pane synchronized preview (Phase 10F.3)
- Continuous bidirectional scroll tracking and click navigation (Phase 10F.4)

The next planned milestone is:
- **Phase 10F.5:** Three-Way Merge / Visual Conflict Diff Resolution (incorporating visual diffing when external advances conflict with unsaved drafts).
