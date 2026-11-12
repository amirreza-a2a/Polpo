# TICK-ADE-06: Raw Line-Based Document Patch Service, Contextual Anchors & Collision Disambiguation

- **Ticket ID:** `TICK-ADE-06`
- **Title:** Raw Line-Based Document Patch Service, Contextual Anchors & Collision Disambiguation
- **Milestone:** Milestone B (Working Copy & Patch Architecture)
- **Priority:** P1
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Scope:** Implement `DocumentPatch`, `PatchAnchor`, `PatchSet`, and `DocumentPatchService` in `core/entities/document_patch.py` and `application/services/document_patch_service.py`, enabling line-based text transformations, contextual anchor verification, duplicate fragment disambiguation, and unified diff generation without lossy AST serialization.
- **Explicit Non-Goals:**
  - No AST-to-text re-serialization (CommonMark AST is strictly read-only; text manipulation operates on raw string lines).
  - No relying on line numbers alone without anchor verification (Finding C).
  - No modification of canonical files on disk.
- **Prerequisites:** `TICK-ADE-05` completed; ADR-008 approved.
- **Dependencies (Blocked By):** `TICK-ADE-05`.
- **Unblocks:** `TICK-ADE-07`, `TICK-ADE-11`.

---

## 1. Existing Source-Code Integration Points

- `core/markdown/merge.py`: Existing diff3 and line-matching algorithms.
- `application/dto/latex_repair_dto.py`: Existing fragment replacement contract.
- `application/services/latex_repair_service.py`: Disambiguation precedent for formula replacements.

---

## 2. Proposed Domain Interfaces & DTOs

```python
class PatchScope(Enum):
    SOURCE_LINE_RANGE = "SOURCE_LINE_RANGE"
    RAW_BLOCK_TOKEN = "RAW_BLOCK_TOKEN"
    METADATA_FIELD = "METADATA_FIELD"
    STYLE_PROFILE = "STYLE_PROFILE"

@dataclass(frozen=True)
class PatchAnchor:
    context_before: str                  # 2 lines immediately preceding target
    target_lines: str                    # Expected verbatim target lines
    context_after: str                   # 2 lines immediately following target
    target_sha256: str                   # SHA-256 of expected target_lines

@dataclass(frozen=True)
class DocumentPatch:
    patch_id: str
    scope: PatchScope
    target_start_line: int              # 1-indexed start line in snapshot text
    target_end_line: int                # 1-indexed end line in snapshot text
    anchor: PatchAnchor                 # Contextual verification anchor
    original_text: str                  # Expected original text fragment
    replacement_text: str               # Proposed replacement text
    change_summary: str                 # Human-readable rationale (English)
    preserved_invariants: Tuple[str, ...]

@dataclass(frozen=True)
class PatchSet:
    set_id: str
    working_copy_id: str
    patches: List[DocumentPatch]
    created_at: float
```

---

## 3. Detailed Implementation Requirements

1. **Contextual Anchor Verification (Finding C):**
   - Applying a patch verifies that lines at `[target_start_line, target_end_line]` match `anchor.target_lines` and `anchor.target_sha256`.
   - If line coordinates drifted due to prior human typing, uses `context_before` and `context_after` to re-anchor the target location.
   - If identical fragments occur in multiple places, the anchor bounding lines disambiguate the target. If ambiguous, raises `AmbiguousPatchAnchorError`.
2. **Bottom-Up Application:**
   - Multi-hunk patch sets sort patches descending by `target_start_line` before applying to preserve earlier line indices.
3. **Overlapping Patch Detection:**
   - Detects if any two patches within a `PatchSet` share overlapping line intervals. If detected, rejects set with `OverlappingPatchError`.
4. **Unified Diff Generation:**
   - Uses standard `difflib.unified_diff` to emit standard Git-compatible diffs for presentation in UI.

---

## 4. Acceptance Criteria

- [ ] Line-based replacements apply cleanly and preserve surrounding document whitespace and line endings (`\n` vs `\r\n`).
- [ ] Contextual anchors disambiguate identical formulas appearing at multiple locations.
- [ ] Multi-patch sets apply bottom-up without line offset corruption.
- [ ] Overlapping patches within a single set are detected and rejected.
- [ ] Original text mismatch raises `PatchTargetMismatchError` without modifying source text.
- [ ] `generate_unified_diff()` emits clean unified diff text.

---

## 5. Test Specifications

### 5.1 Unit Tests
- `tests/unit/test_document_patch_service.py`:
  - `test_single_and_multi_line_patch_application()`: Basic replacements.
  - `test_contextual_anchor_disambiguation()`: Verifies correct hunk selected among identical tokens.
  - `test_bottom_up_patch_set_application()`: Multi-hunk distant replacements.
  - `test_detect_and_reject_overlapping_patches()`: Rejection of colliding intervals.
  - `test_line_ending_preservation()`: Confirms CRLF and LF preservation.

### 5.2 Negative & Security Tests
- `test_reject_control_characters_in_replacement()`: Ensures replacements containing null bytes (`\0`) are rejected.

### 5.3 Concurrency & Persistence Considerations
- Pure text manipulation in memory; zero disk writes.

---

## 6. Error and Recovery Behavior

- Verification failures abort patch application completely, returning `(original_text, False, error_reason)` cleanly.

---

## 7. Observability & Audit Requirements

- Returns detailed failure reasons in `PatchApplicationResult`.

---

## 8. Documentation Updates

- Document stable anchor model in ADR-008.

---

## 9. Required Project Skills

- `codebase-design`: Clean patch data structures.
- `tdd`: Test-driven patch engine implementation.

---

## 10. Verification Commands

```bash
pytest tests/unit/test_document_patch_service.py -v
python3 -c "import ast; tree = ast.parse(open('application/services/document_patch_service.py').read()); assert not any('PySide6' in ast.dump(n) or 'sqlite3' in ast.dump(n) for n in ast.walk(tree))"
```

---

## 11. Definition of Done

- All unit tests pass with 100% coverage.
- Anchor verification and diff generator fully verified.
