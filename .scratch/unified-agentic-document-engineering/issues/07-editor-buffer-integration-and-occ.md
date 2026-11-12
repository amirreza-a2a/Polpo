# TICK-ADE-07: Native Editor Buffer Patch Adoption, Three-Way Conflict Handling & OCC Gating

- **Ticket ID:** `TICK-ADE-07`
- **Title:** Native Editor Buffer Patch Adoption, Three-Way Conflict Handling & OCC Gating
- **Milestone:** Milestone B (Working Copy & Patch Architecture)
- **Priority:** P1
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Scope:** Integrate verified `PatchSet` adoption into `interfaces/desktop/controllers/markdown_editor_controller.py`, executing three-way conflict checks, safe disjoint rebases, multi-region replacements via atomic `QTextCursor` edit blocks, preserving the Qt undo/redo stack (`Ctrl+Z`), marking the editor buffer dirty, and gating canonical persistence under Optimistic Concurrency Control (OCC).
- **Explicit Non-Goals:**
  - No direct SQL execution or bypassing `DocumentPublicationService`.
  - No automatic silent save on patch adoption (user must explicitly save, or click "Apply and Save" with clear confirmation).
  - No overwriting concurrent human edits without explicit review (Finding C).
- **Prerequisites:** `TICK-ADE-06` completed; ADR-008 approved.
- **Dependencies (Blocked By):** `TICK-ADE-06`.
- **Unblocks:** `TICK-ADE-15`.

---

## 1. Existing Source-Code Integration Points

- `interfaces/desktop/controllers/markdown_editor_controller.py`: Primary desktop editor presentation controller managing the in-memory `_text_document`, dirty state, and undo stack.
- `application/services/document_publication_service.py`: 4-phase atomic OCC publication protocol.
- `core/markdown/merge.py`: Three-way merge engine for conflict hunk resolution.

---

## 2. Proposed Application & Presentation DTOs

```python
@dataclass(frozen=True)
class ApplyPatchCommand:
    task_id: str
    patch_set: PatchSet
    snapshot: TaskInputSnapshot

@dataclass(frozen=True)
class PatchAdoptionResult:
    success: bool
    applied_hunks: int
    is_dirty: bool
    requires_conflict_resolution: bool = False
    conflict_message: Optional[str] = None
```

---

## 3. Detailed Implementation Requirements

1. **Three-Way Conflict Preconditions (Finding C):**
   - When the user clicks "Apply Changes", `MarkdownEditorController.apply_patch_set()` evaluates:
     1. *Canonical Base Check:* Verifies that SQLite's canonical version has not advanced beyond `snapshot.base_canonical_version`. If advanced, raises `StaleDocumentVersionError` (triggers standard merge session).
     2. *Editor Buffer Check:* Compares active editor text against `snapshot.editor_draft_hash`.
        - If matching: applies cleanly.
        - If dirty/modified: compares modified regions against patch anchors.
        - If modified regions are disjoint: safely rebases patch offsets using anchor targets and context lines.
        - If modified regions overlap: aborts automatic apply, raises `EditorConflictError`, and opens the interactive conflict view.
2. **Atomic `QTextCursor` Edit Block:**
   - Wraps all hunk replacements in `QTextCursor.beginEditBlock()` and `QTextCursor.endEditBlock()`.
   - The entire multi-region patch set forms a single undo action. Authors can press `Ctrl+Z` to revert the entire agent proposal cleanly.
3. **OCC Persistence Gating:**
   - Applying patches sets `_is_dirty = True`, but does NOT commit to disk or advance version watermarks.
   - User triggers explicit Save (`Ctrl+S`), which executes `DocumentPublicationService.commit_source_text()` under OCC.

---

## 4. Acceptance Criteria

- [ ] Applying an approved patch set modifies the editor text accurately.
- [ ] Buffer dirty status becomes `True` (`isDirty == true`).
- [ ] Active canonical version watermark in SQLite remains unchanged upon adoption.
- [ ] Pressing `Ctrl+Z` (Undo) immediately reverts the entire applied patch set in one step.
- [ ] Concurrently modified disjoint lines in the editor are rebased and applied cleanly.
- [ ] Concurrently modified overlapping lines trigger `EditorConflictError` and block silent overwrites.
- [ ] Saving the modified buffer increments the canonical version monotonically ($N \to N+1$).

---

## 5. Test Specifications

### 5.1 Integration Tests
- `tests/integration/test_editor_patch_adoption.py`:
  - `test_atomic_edit_block_undo_redo()`: Verifies `QTextDocument` undo/redo reverts multi-hunk replacements.
  - `test_disjoint_editor_rebase()`: Confirms disjoint human typing allows safe patch application.
  - `test_overlapping_conflict_rejection()`: Confirms overlapping human typing is intercepted.
  - `test_occ_gated_save_after_adoption()`: Confirms saving advances canonical version monotonically.

### 5.2 Negative & Security Tests
- `test_reject_patch_when_merge_conflict_active()`: Ensures patches cannot be applied if editor has active conflict markers (`<<<<<<<`).

### 5.3 Concurrency & Persistence Considerations
- Interacts strictly with Qt GUI thread; persistence handled by background UoW.

---

## 6. Error and Recovery Behavior

- Conflicts abort buffer modification and display interactive hunk resolution toolbar without data loss.

---

## 7. Observability & Audit Requirements

- Emits Qt signal `patchSetApplied(int hunkCount)` for UI notification.

---

## 8. Documentation Updates

- Document three-way conflict flow in ADR-008.

---

## 9. Required Project Skills

- `codebase-design`: Clean presentation controller boundaries.
- `tdd`: Test-driven editor adoption harness with headless `QTextDocument`.

---

## 10. Verification Commands

```bash
pytest tests/integration/test_editor_patch_adoption.py -v
```

---

## 11. Definition of Done

- All integration tests pass 100%.
- Undo/redo integrity and three-way conflict safety verified.
