# TICK-ADE-06: Raw Line-Based Document Patch Service & Collision Disambiguation

- **Ticket ID:** `TICK-ADE-06`
- **Milestone:** Milestone B (Working Copy & Patch Architecture)
- **Priority:** P1
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Blocked By:** `TICK-ADE-05`
- **Authoritative ADR:** ADR-008 (Agent Working Copy, Isolated Patch Lifecycle & OCC Safety)

---

## 1. Objective

Implement `DocumentPatch`, `PatchSet`, and `DocumentPatchService` in `core/entities/document_patch.py` and `application/services/document_patch_service.py`, enabling line-based text transformations, duplicate fragment disambiguation, and unified diff generation without lossy AST serialization.

---

## 2. User Value

Authors receive clear, granular side-by-side or unified diffs of proposed changes. Even if a formula or diagram appears multiple times in a document, line coordinates guarantee that the exact intended location is modified without ambiguous replacements.

---

## 3. Scope

- Define domain entities in `core/entities/document_patch.py`:
  - `PatchScope` enum (`SOURCE_LINE_RANGE`, `RAW_BLOCK_TOKEN`, `METADATA_FIELD`, `STYLE_PROFILE`).
  - `DocumentPatch` value object: `patch_id: str`, `scope: PatchScope`, `target_start_line: int`, `target_end_line: int`, `original_text: str`, `replacement_text: str`, `change_summary: str`, `preserved_invariants: Tuple[str, ...]`.
  - `PatchSet` aggregate: `set_id: str`, `working_copy_id: str`, `patches: List[DocumentPatch]`, `created_at: float`.
- Implement `DocumentPatchService` in `application/services/document_patch_service.py`:
  - `apply_patch(base_text: str, patch: DocumentPatch) -> Tuple[str, bool, Optional[str]]`: Replaces lines `[target_start_line, target_end_line]`, verifying that existing lines match `patch.original_text`.
  - `apply_patch_set(base_text: str, patch_set: PatchSet) -> Tuple[str, bool, Optional[str]]`: Applies multiple non-overlapping patches in reverse line order (bottom-up) to preserve line indices.
  - `generate_unified_diff(original_text: str, modified_text: str) -> str`: Produces clean unified diff string using Python's standard `difflib.unified_diff`.
  - `detect_overlaps(patches: Sequence[DocumentPatch]) -> bool`: Verifies that no two patches share overlapping line intervals.

---

## 4. Explicit Non-Goals

- No AST-to-text re-serialization (CommonMark AST is strictly read-only; text manipulation operates on raw string lines).
- No modification of files on disk.

---

## 5. Codebase References

- `core/markdown/merge.py` (Existing diff3 and line-matching algorithms).
- `application/dto/latex_repair_dto.py` (Existing fragment replacement contract).
- `application/services/latex_repair_service.py` (Disambiguation precedent).

---

## 6. Proposed Files & Components

- Create: `core/entities/document_patch.py`
- Create: `application/services/document_patch_service.py`
- Create: `tests/unit/test_document_patch_service.py`

---

## 7. Domain & Architectural Constraints

- **Format Preservation Invariant ([CONTEXT.md](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/CONTEXT.md) Section 3.4):** Plain text is the canonical source of truth. Patches must preserve original newline styles (`\n` vs `\r\n`) and trailing whitespace outside the targeted replacement range.

---

## 8. Detailed Implementation Requirements

1. When applying multiple patches in a `PatchSet`, sort patches descending by `target_start_line` before applying. This prevents line additions or deletions in earlier patches from invalidating line numbers of later patches.
2. If `patch.original_text` does not match the actual text at `[target_start_line, target_end_line]`, reject the patch with `PatchTargetMismatchError`.
3. If two patches have overlapping line spans (e.g. `10-15` and `12-18`), reject the set with `OverlappingPatchError`.

---

## 9. Acceptance Criteria

- [ ] Line-based replacements apply cleanly and preserve surrounding document whitespace.
- [ ] Bottom-up application of multi-hunk patch sets modifies all target regions accurately.
- [ ] Overlapping patches within a single set are detected and rejected.
- [ ] Original text mismatch raises `PatchTargetMismatchError` without modifying the text.
- [ ] `generate_unified_diff()` emits standard Git-compatible unified diff syntax.

---

## 10. Required Tests

- Unit tests in `tests/unit/test_document_patch_service.py` verifying:
  - Single line replacement, multi-line replacement, insertions, and deletions.
  - Multi-patch sets applied bottom-up across distant document lines.
  - Detection and rejection of overlapping patch intervals.
  - Rejection when original text has changed.
  - Preservation of CRLF and LF line endings.

---

## 11. Security Requirements

- Patch replacements must reject null bytes (`\0`) and control characters that corrupt text editors.

---

## 12. Completion Evidence & Follow-up

- Unit test execution evidence passing 100%.
- Unblocks: `TICK-ADE-07`, `TICK-ADE-11`.
