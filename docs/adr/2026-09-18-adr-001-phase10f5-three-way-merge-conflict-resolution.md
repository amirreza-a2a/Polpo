# ADR-001: Phase 10F.5 Three-Way Merge and Visual Conflict Diff Resolution

- **Status:** Accepted
- **Date:** 2026-09-18
- **Phase:** 10F.5 — Three-Way Merge / Visual Conflict Diff Resolution
- **Scope:** Desktop Review Workspace (`MarkdownEditorPane`, `MarkdownView`, `ReviewWorkspaceView`)
- **Authoritative Baseline:** Commit `a16bfb4` (Phase 10F.4 formal closure)

---

## 1. Context

PolpoT is an embedded, desktop-first, local-first document processing application. The desktop Review Workspace provides a synchronized dual-pane interface containing a PDF Document Viewer on the left and a Segmented Markdown Workspace on the right (hosting the native Markdown source editor and virtualized rendered preview).

The editing and viewing lifecycle follows strict Clean Architecture and Optimistic Concurrency Control (OCC) invariants:
1. **Immutable Document Artifacts:** Every committed canonical Markdown document is stored immutably on disk in artifact storage as `output_{job_id}_v{version}.md`. Historical document artifacts are never overwritten or deleted during editing.
2. **OCC Watermark Allocation:** Canonical versions advance strictly via `MarkdownEditorService.commit_source_text()`, which enforces atomic SQLite watermark allocation (`jobs.output_artifact_version_watermark`) and pointer updates (`jobs.output_path`).
3. **Live Dual-Pane Preview (Phase 10F.3):** Live typing debounces transient preview projections into `MarkdownDocumentModel` without writing to SQLite, mutating canonical version watermarks, or writing disk artifacts. Stale in-flight renders are discarded via Option B latest revision matching (`_draft_revision`).
4. **Continuous Viewport Tracking (Phase 10F.4):** Viewport scrolling is continuously synchronized between editor and preview using normalized scroll progress ($0.0 \dots 1.0$), 16ms leading-edge throttling, and directional origin locks (`_sync_origin`).

### The External Advance Conflict Problem
While a user is actively editing a document (creating a dirty local draft based on version $N$), external events can advance the canonical document to version $N+1$ or higher:
- **Visual Region Re-crops:** The user adjusts a visual region bounding box in the PDF viewer; `ApplyReviewService` re-crops the image, generates `output_{job_id}_v{N+1}.md` with the updated image token, and commits it to SQLite.
- **Background Pipelines:** Asynchronous typography refinement (Pipeline 2) or secondary AI processing commits a new canonical output version to SQLite.

Currently, when `MarkdownEditorController` is notified of an external advance while `isDirty == True`:
- The controller sets `_has_conflict = True` and records an error message.
- Saving is completely blocked (`MarkdownEditorController.save()` returns early; Save button is disabled in QML; `commit_source_text()` rejects stale base versions via `StaleDocumentVersionError`).
- The UI presents a conflict warning banner with only a single action: **"Reload Latest"**.
- Clicking "Reload Latest" or "Discard" immediately and unconditionally overwrites the editor buffer, permanently destroying the user's uncommitted work.

---

## 2. Problem Statement

How should PolpoT resolve conflicting external canonical document updates against unsaved local editor drafts without destroying user work, violating OCC invariants, corrupting Markdown document formatting, or introducing server/network dependencies?

---

## 3. Settled Architectural & Product Decisions

### D01 — Conflict Persistence Scope: Session-Only
- **Decision:** Conflict resolution is strictly **session-only**. Unresolved local drafts and active merge state reside exclusively in memory during the active application session.
- **Rationale:**
  - Standard desktop text editors (e.g., VS Code, Sublime Text without an external auto-save database) treat uncommitted editor buffers as ephemeral session state.
  - Avoids premature SQLite schema migrations (`004_editor_drafts.sql`), orphan draft garbage collection, and table lifecycle management.
  - Durable draft persistence across application restarts is explicitly deferred to a future milestone.
  - If the user attempts to close the application while uncommitted conflict state exists, the desktop UI intercepts the window close event with a confirmation prompt.

### D02 — Merge Engine Foundation: Hybrid Architecture
- **Decision:** A **format-preserving line-based three-way merge (`diff3`) over raw Markdown text**, enriched with **Markdown AST/source-line interval context** for conflict labeling and UI navigation.
- **Critical Technical Qualifications:**
  1. **Not Off-the-Shelf:** Python standard library `difflib.SequenceMatcher` is strictly a two-sequence matching primitive, **not** a three-way merge algorithm. The core merge engine (`core/markdown/merge.py`) must implement a genuine, deterministic three-way merge algorithm (`diff3`) constructed from two-way LCS/matching operations against the common ancestor.
  2. **Zero Lossy AST Serialization:** The system must **never** deconstruct Markdown into an AST and serialize it back to Markdown text. AST serializers destroy user formatting, collapse intentional whitespace, strip comments, and corrupt TeX math formulas. Raw Markdown text remains the authoritative representation.
  3. **AST Contextual Enrichment:** The CommonMark AST (`MarkdownDocumentModel` source-line intervals `[sourceStartLine, sourceEndLine]`) is queried in a read-only manner to tag conflict hunks with human-readable semantic block headers (e.g., *"Conflict in Heading 2: Introduction"*, *"Conflict in Visual Region: crop_10_r1"*).

### D03 — Non-Overlapping Auto-Merge: Automatic with Informational Notification
- **Decision:** When the three-way merge algorithm establishes that local edits and external canonical advances occupy **completely non-overlapping line intervals**, the changes are **automatically merged into the editor buffer**, the base version is advanced to $N+1$, and an **informational notification banner** is displayed.
- **Rationale:**
  - In ~90% of desktop workflows, external advances are single-line visual region token updates (`![[crop_...|region_id=...]]`) caused by re-cropping on one page, while local edits are paragraph edits on another page.
  - Requiring a manual confirmation modal for non-overlapping edits introduces severe workflow friction without safety benefits.
  - Displaying a non-intrusive notification (e.g., *"External image changes merged seamlessly"*) guarantees full user transparency without interrupting the typing flow.
  - Overlapping changes are never silently merged.

### D04 — Conflict Resolution UX: In-Buffer Resolution with Interactive Toolbar
- **Decision:** True overlapping conflicts are resolved **directly inside the native Markdown editor (`MarkdownEditorPane`)** using an **in-buffer conflict representation** paired with an **interactive resolution toolbar**.
- **Design Specifications & Invariants:**
  1. **Transient In-Buffer Representation:** Conflicting hunks are bounded by explicit markers:
     ```markdown
     <<<<<<< Local Draft
     User's uncommitted modifications
     =======
     Incoming canonical changes (vN+1)
     >>>>>>> Incoming Canonical
     ```
  2. **Interactive Action Toolbar:** An inline toolbar positioned directly above the editor provides quick resolution actions:
     - `[Accept Local]` — Keeps local variant and removes incoming marker block.
     - `[Accept Incoming]` — Replaces with canonical variant and removes local marker block.
     - `[Accept Both]` — Keeps local followed by canonical.
     - `[Next Conflict]` / `[Previous Conflict]` — Navigates cursor to next/previous hunk.
  3. **Save Gating Invariant:** The controller must validate the complete absence of conflict markers prior to allowing save. Attempting to save while markers exist is strictly blocked.
  4. **Undo/Redo & Native TextArea Editing:** The user retains full freedom to manually edit text inside and around conflict markers. Standard native undo/redo remains functional.
  5. **Non-Persistence Invariant:** Conflict markers must **never** be persisted to canonical SQLite storage or written to canonical disk artifacts.

### D05 — Visual Preview During Conflict: Suspended with Explicit Overlay
- **Decision:** While unresolved conflict markers exist in the editor buffer, **normal live preview rendering is suspended**. The preview pane retains the **last valid rendered document** and displays an explicit **conflict resolution overlay banner**:
  ```text
  ⚠️ Preview paused during conflict resolution
  ```
- **Rationale:**
  - CommonMark parsers do not understand conflict markers; feeding raw `<<<<<<<` and `>>>>>>>` tokens into `markdown-it-py` produces broken HTML, malformed blockquotes, and visual noise.
  - Preserving the last valid render avoids jarring flashes or blank views while the user is actively resolving conflicts.
  - Once all conflict markers are resolved, live preview rendering automatically resumes.

### D06 — Second External Advance: Immediate Invalidation & Re-Merge
- **Decision:** If canonical advances again ($N+1 \to N+2$) while the user is actively resolving an $N$ vs $N+1$ conflict, the active merge session is **immediately invalidated**. The system preserves the user's **current in-progress resolution** as the new local candidate input and automatically initiates a fresh three-way merge against $N+2$.
- **Rationale:**
  - Guarantees that the user never resolves against stale intermediate state.
  - Prevents OCC commit failures at save time (`commit_source_text` requires `base_version == current_snapshot.active_version`).
  - Preserves manual resolution work completed so far without risking silent data corruption.

### D07 — Job Switching During Conflict: Blocked Behind Confirmation Dialog
- **Decision:** Attempting to switch jobs while an uncommitted draft or active conflict exists is **blocked behind an explicit modal confirmation dialog**:
  ```text
  "You have unsaved changes and unresolved conflicts in Job X.
   Switching jobs will discard your uncommitted work.
   Do you want to discard your changes or cancel?"
  ```
- **Rationale:**
  - Prevents accidental data loss when clicking another item in the job list.
  - Avoids complex multi-job draft caching in Phase 10F.5.

### D08 — Merge Session Identity: Dedicated Transient `merge_session_id`
- **Decision:** Introduce an explicit, monotonic **`merge_session_id: int`** counter in `MarkdownEditorController`.
- **Rationale:**
  - Concurrently running background diff calculations and document loading tasks must carry `merge_session_id`.
  - Discarding stale asynchronous callbacks becomes a trivial equality check (`task_session_id == self._merge_session_id`).
  - Keeps merge lifecycle cleanly isolated from other system tokens.

---

## 4. Prominent Architectural Invariants

### 4.1 The Three Independent Merge Inputs
True three-way merge relies strictly on three distinct, recoverable inputs:
```text
BASE                    <-- Stored immutably on disk: output_{job}_v{base_version}.md
  +                         (and cached in memory: _saved_source_text)
LOCAL DRAFT             <-- Retained in memory: MarkdownEditorController._source_text
  +
CURRENT CANONICAL       <-- Stored immutably on disk: output_{job}_v{canonical_version}.md
                            (referenced in SQLite jobs.output_path)
```

### 4.2 Authoritative vs. Informational Separation
```text
Raw Markdown Text         <-- Authoritative merge representation (format-preserving)
       ≠
Semantic Markdown AST     <-- Presentation, navigation, and contextual labeling only
```

### 4.3 Distinct Concurrency Tokens
The system strictly distinguishes five independent identity tokens:
```text
Merge Session Identity    (merge_session_id: monotonic per conflict analysis/resolution session)
       ≠
Canonical Markdown Version (active_version: SQLite job.output_artifact_version_watermark)
       ≠
Draft Revision            (_draft_revision: monotonic per local keystroke in editor)
       ≠
Preview Model Generation  (_model_generation: monotonic per MarkdownDocumentModel update)
       ≠
VisualRegion Artifact Ver (active_artifact_version: crop_{job}_{region}_v{ver}.jpg)
```

---

## 5. Consequences

### Positive
- **Zero Data Loss:** External visual region re-crops no longer force users to wipe their uncommitted drafts.
- **Friction-Free Non-Overlapping Merges:** 90% of external advances merge automatically without interrupting the user.
- **Format Preservation:** Because merge runs on raw text lines, custom indentation, HTML comments, tables, and TeX equations are 100% preserved.
- **Strict OCC Compliance:** Canonical saves advance versions cleanly via `MarkdownEditorService.commit_source_text(base_version=canonical_ver)`, ensuring zero schema regressions and zero database locks.
- **Minimal Complexity:** Avoids database schema migrations, remote network daemons, and external git dependencies.

### Negative / Trade-Offs
- **Session-Only Limitation:** If the desktop application crashes or the system loses power during an active conflict, uncommitted draft edits are lost.
- **In-Buffer Conflict Marker Discipline:** The editor controller must rigorously police the buffer to guarantee that raw conflict markers cannot be saved as valid Markdown.

---

## 6. Deferred Decisions & Future Work

The following items are explicitly **excluded from Phase 10F.5** and reserved for future phases:
1. **Durable Draft Persistence:** Stashing uncommitted drafts in SQLite (`004_editor_drafts.sql`) or dedicated `.draft.md` disk files across application restarts.
2. **Multi-Job Draft Cache:** Preserving independent in-memory dirty draft states across multiple background jobs simultaneously.
3. **Multi-File Document Merge:** Three-way merge across multi-file collections (PolpoT operates on single canonical files per job).
4. **Git CLI / GitPython Integration:** Utilizing git repositories, worktrees, or `git merge-file` commands.
5. **AST-to-Markdown Serialization:** Parsing AST back into generated text.
6. **Background Server Daemon:** Introducing FastAPI, WebSockets, or HTTP loopback daemons.
7. **Heuristic Conflict Guessing:** Silently choosing one conflicting text branch over another without explicit user interaction.

---

## 7. Rejected Alternatives

| Alternative | Reason for Rejection |
| :--- | :--- |
| **Pure AST Semantic Tree Merge** | Markdown AST de-parsing/re-serialization is destructive: it strips comments, alters whitespace, rewraps lines, and corrupts complex inline TeX math syntax. |
| **External Git CLI (`git merge-file`)** | Violates Rule 2 & 19 of `AGENTS.md` (desktop-first, local-first, minimal dependencies). External processes create packaging, cross-platform, and PATH risks on Windows/macOS. |
| **Three-Column Split View (`Base \| Local \| Remote`)** | Horizontally unviable on standard desktop screens when sharing space with the PDF viewer. |
| **Raw Conflict Markers in Live Preview** | CommonMark parsers misinterpret `<<<<<<<` markers, producing chaotic layout flashes and broken preview rendering. |
| **Silent Overwrite on Region Re-crop** | Discarding local edits when a user adjusts an image region was the primary architectural deficiency of Phase 10F.3/10F.4. |

---

## 8. Dependency on Existing OCC & Persistence Architecture

Phase 10F.5 directly leverages and preserves the Phase 10F.1 OCC persistence pipeline:
1. When a conflict between base version $N$ and canonical version $N+1$ is resolved, the resulting text represents a logical evolution of $N+1$.
2. The resolved text is committed by calling:
   ```python
   editor_service.commit_source_text(
       job_id=job_id,
       raw_text=resolved_text,
       base_version=current_canonical_version,  # e.g., N+1
   )
   ```
3. `MarkdownEditorService` validates that SQLite `job.output_path` matches version $N+1$, allocates watermark $N+2$, writes `output_{job_id}_v{N+2}.md`, verifies disk existence, and atomically points SQLite to the new artifact.
4. Zero modifications are required in `core/` persistence entities or SQLite schemas.

---

## 9. Relationship to Phases 10F.3 and 10F.4

- **Phase 10F.3 Coexistence:** Live preview debouncing (`scheduleLivePreview`), Option B latest revision tracking (`_draft_revision`), and structural document reconciliation (`_sync_document_structure`) remain intact. Preview simply pauses when conflict markers are present.
- **Phase 10F.4 Coexistence:** Continuous normalized scroll tracking ($0.0 \dots 1.0$) and directional locking continue to function normally during clean editing and resume immediately once conflicts are resolved.

---

## 10. Open Technical Validation Requirements for Architecture Design

Before code implementation begins, the architecture design pass must explicitly validate and verify the following algorithmic and interaction edge cases:

1. **Deterministic `diff3` Correctness:** Proving that the custom Python three-way merge correctly identifies clean non-overlapping insertions, deletions, replacements, and conflicting hunks.
2. **Overlapping Insertion Handling:** Behavior when both Local and Remote insert differing text at the exact same line offset.
3. **Adjacent Edit Boundaries:** Behavior when Local edits line 10 and Remote edits line 11 (determining whether boundary heuristics flag a conflict or allow clean merge).
4. **Identical Concurrent Edits:** Verifying that identical edits made in both Local and Remote merge cleanly without conflict.
5. **Delete vs. Modify Collisions:** Behavior when Local deletes a paragraph that Remote modified (or vice versa).
6. **Repeated Lines & Ambiguous Sequences:** Ensuring `diff3` does not misalign matches when documents contain repeated blank lines, markdown separators (`---`), or identical list bullets.
7. **Whitespace-Only Edits:** Deterministic handling of trailing whitespace, Windows (`\r\n`) vs. Unix (`\n`) newlines, and indentation changes.
8. **Markdown Region-Token Edits:** Verifying that `![[crop_...|region_id=...]]` token additions, updates, and deletions cleanly map to visual regions without corrupting token syntax.
9. **EOF & Trailing Newline Invariants:** Preventing EOF boundary errors when merge hunks occur at the final line of a document.
