# TICK-ADE-07: Native Editor Buffer Patch Adoption, Undo/Redo Integrity & OCC Gating

- **Ticket ID:** `TICK-ADE-07`
- **Milestone:** Milestone B (Working Copy & Patch Architecture)
- **Priority:** P1
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Blocked By:** `TICK-ADE-06`
- **Authoritative ADR:** ADR-008 (Agent Working Copy, Isolated Patch Lifecycle & OCC Safety)

---

## 1. Objective

Integrate verified `PatchSet` adoption into `interfaces/desktop/controllers/markdown_editor_controller.py`, executing multi-region replacements via atomic `QTextCursor` edit blocks, preserving the Qt undo/redo stack (`Ctrl+Z`), marking the editor buffer dirty, and gating canonical persistence under Optimistic Concurrency Control (OCC).

---

## 2. User Value

Authors retain complete manual control over AI changes. Applying an approved proposal instantly reflects in the editor with full syntax highlighting, allows standard `Ctrl+Z` undo to revert the entire proposal in one keystroke, and guarantees that saving respects OCC without silent overwrites.

---

## 3. Scope

- Extend `MarkdownEditorController` in `interfaces/desktop/controllers/markdown_editor_controller.py`:
  - Add slot `@Slot(str, result=bool)` `applyPatchSet(patch_set_json: str) -> bool`.
  - Validate that active document job matches patch set's `job_id`.
  - Validate that buffer has no active merge conflicts (`_has_conflict == False`).
  - Use `QTextCursor.beginEditBlock()` and `QTextCursor.endEditBlock()` to execute all line replacements within a single undoable transaction on `_text_document`.
  - Update `_source_text`, set `_is_dirty = True`, emit `dirtyChanged`, `sourceTextChanged`.
  - Leave `_active_version` watermark unchanged (OCC invariant).
- Add save guard:
  - If user triggers `save()` after patch application, standard OCC calls `DocumentPublicationService.commit_source_text(job_id, base_version, text)`. If a background update advanced canonical version, intercept with standard three-way merge conflict session.

---

## 4. Explicit Non-Goals

- No direct SQL execution or bypassing `MarkdownEditorService` / `DocumentPublicationService`.
- No automatic silent save on patch adoption (user must explicitly save, or click "Apply and Save" with clear confirmation).

---

## 5. Codebase References

- `interfaces/desktop/controllers/markdown_editor_controller.py` (Lines 1670–1760: existing `applyRepair` fragment replacement pattern).
- `application/services/document_publication_service.py` (4-phase commit protocol).
- `docs/adr/2026-11-07-adr-006-ai-assisted-latex-repair.md` (ADR-006 D06: Atomic editor replacement invariant).

---

## 6. Proposed Files & Components

- Modify: `interfaces/desktop/controllers/markdown_editor_controller.py`
- Create: `tests/integration/test_editor_patch_adoption.py`

---

## 7. Domain & Architectural Constraints

- **Rule 5 Compliance:** Presentation controller acts strictly as an adapter, binding Qt cursor operations to the verified `DocumentPatchService` output.
- **Rule 8 & 9 Compliance:** Database version advances strictly through `DocumentPublicationService` under `BEGIN IMMEDIATE`.

---

## 8. Detailed Implementation Requirements

1. If `_is_dirty == True` when the user clicks "Apply Patch Set", verify if the user's uncommitted edits conflict with patch lines:
   - If clean (no overlapping lines), apply patch set and retain dirty state.
   - If conflicting, raise `EditorDirtyConflictError` and prompt the user to save or revert their edits first.
2. The entire replacement sequence must execute inside a single `QTextCursor.beginEditBlock()` / `endEditBlock()` pair. Pressing `Ctrl+Z` in the UI must undo all hunks in the patch set simultaneously.
3. Position tracking: If the cursor is currently placed after the patched regions, adjust cursor position cleanly without jumping or throwing out-of-range exceptions.

---

## 9. Acceptance Criteria

- [ ] Applying an approved patch set modifies the editor text accurately.
- [ ] Buffer dirty status becomes `True` (`isDirty == true`).
- [ ] Active canonical version watermark in SQLite remains unchanged upon adoption.
- [ ] Pressing `Ctrl+Z` (Undo) immediately reverts the entire applied patch set.
- [ ] Applying a patch set while conflicts are active is strictly rejected.
- [ ] Saving the modified buffer increments the canonical version monotonically ($N \to N+1$).

---

## 10. Required Tests

- Integration tests in `tests/integration/test_editor_patch_adoption.py` verifying:
  - Atomic cursor edit block execution with PySide6 headless `QTextDocument`.
  - `QUndoStack` undo and redo behavior reverting the applied patch.
  - OCC save rejection if base version was externally modified.

---

## 11. Security Requirements

- Ensure text inserted via `QTextCursor` does not execute scripts or trigger unhandled QTextDocument HTML formatting tags.

---

## 12. Completion Evidence & Follow-up

- Integration test suite passing 100%.
- Completes Milestone B; unblocks Milestone F (`TICK-ADE-15`).
