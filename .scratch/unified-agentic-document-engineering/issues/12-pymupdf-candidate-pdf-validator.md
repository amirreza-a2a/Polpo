# TICK-ADE-12: PyMuPDF Structural PDF Inspection Engine & Visual Regression Validator

- **Ticket ID:** `TICK-ADE-12`
- **Title:** PyMuPDF Structural PDF Inspection Engine & Visual Regression Validator
- **Milestone:** Milestone D (AI-Assisted PDF Repair)
- **Priority:** P2
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Scope:** Implement `PyMuPdfValidator` in `infrastructure/pdf/mupdf_validator.py` implementing the `IPdfInspectionEngine` port (defined in `TICK-ADE-10`), providing fast structural document inspection, anomaly classification, and optional page preview rasterization.
- **Explicit Non-Goals:**
  - No OCR or heavy visual machine learning models on every compilation.
  - No universal 500ms guarantee for arbitrary PDF sizes and hardware (Finding H).
  - No defining the `IPdfInspectionEngine` port here (the abstract port is owned by `TICK-ADE-10`, Finding B).
- **Prerequisites:** `TICK-ADE-10` completed; ADR-009 approved.
- **Dependencies (Blocked By):** `TICK-ADE-10`.
- **Unblocks:** `TICK-ADE-15`.

---

## 1. Existing Source-Code Integration Points

- `application/ports/pdf_validator_port.py`: Abstract `IPdfInspectionEngine` port and inspection DTOs.
- `infrastructure/pdf/quarto_adapter.py`: Lines 198–220 (existing basic page count and magic header checks).

---

## 2. Proposed Component Implementation

```python
# infrastructure/pdf/mupdf_validator.py

class PyMuPdfValidator(IPdfInspectionEngine):
    """Concrete PyMuPDF adapter implementing structural and visual PDF inspection."""

    def inspect_pdf(self, request: PdfInspectionRequest) -> PdfInspectionResult:
        ...
```

---

## 3. Detailed Implementation Requirements

1. **Structural PDF Inspection (Finding E, Finding F, Finding H):**
   - Header & Integrity: Verifies `%PDF-` header and successful opening with `fitz.open(request.pdf_path)`. If corrupted, records `HARD_FAILURE` and sets `is_valid = False`.
   - Page Count Bound: Enforces `max_pages = 500` bound against decompression bombs.
   - Page Dimensions: Verifies page dimensions match expected format (e.g. A4: $595 \times 842 \pm 2$ pt). Flags deviations as `WARNING`.
   - Text Content Extraction: Extracts text via `page.get_text()` and checks for presence of bounded `request.expected_text_anchors: Tuple[str, ...]` (max 10 anchors, max 128 chars each, max 2 KB total; does NOT require full source markdown).
   - Blank Page Detection: Flags pages with $< 10$ characters of text and zero vector drawings as `WARNING` (blank page indicator).
   - Vector Graphics Detection: `require_vector_drawings = False` by default. Text-only documents are structurally valid with zero drawings. Verifies `len(page.get_drawings()) > 0` ONLY on pages specified in `request.policy.expected_diagram_pages: Tuple[int, ...]`; flags missing drawings on those pages as `WARNING`, not automatic hard rejection.
   - Structural vs Semantic Scope: Explicitly documents that `is_valid = True` proves structural well-formedness and readability only; it does NOT imply visual beauty, mathematical correctness, Persian RTL text correctness, or semantic fidelity.
2. **Optional Visual Preview Generation:**
   - If `request.generate_raster_previews` is `True`, renders 150 DPI page preview PNGs for sampled pages (first 3 pages + last page + any page with an anomaly; max 5 pages total).
   - Enforces 50MB image memory buffer cap and 5.0-second execution timeout with cooperative cancellation checks.
   - Generated buffers returned as immutable `raster_previews: Tuple[bytes, ...]`.
3. **Realistic Performance Target:**
   - Structural checks complete in $< 1.5$ seconds for documents under 50 pages on reference hardware (Ubuntu 24.04, Core i7/Ryzen 7). This benchmark is a quality objective, not an absolute real-time guarantee.

---

## 4. Acceptance Criteria

- [ ] Corrupt or empty PDF files fail inspection with `HARD_FAILURE` anomaly and `is_valid = False`.
- [ ] Text-only PDF documents pass inspection cleanly without requiring vector drawings (`require_vector_drawings = False`).
- [ ] Vector drawings are verified exclusively on pages listed in `expected_diagram_pages`.
- [ ] Orphan blank pages are detected and reported as `WARNING` without causing automatic hard rejection.
- [ ] Text extraction verifies presence of `expected_text_anchors` without requiring full markdown text.
- [ ] Page raster previews are generated cleanly as PNG byte buffers for at most 5 sampled pages when requested.
- [ ] Structural passing is explicitly documented as separate from visual/semantic correctness.
- [ ] Implements `IPdfInspectionEngine` completely with zero Qt imports.

---

## 5. Test Specifications

### 5.1 Unit Tests
- `tests/unit/test_pdf_validator.py`:
  - `test_valid_pdf_structural_inspection()`: Confirms clean report on valid sample PDF.
  - `test_text_only_pdf_passes_without_drawings()`: Asserts text-only PDF passes with zero vector drawings.
  - `test_expected_diagram_pages_drawing_verification()`: Asserts drawings are checked only on expected diagram pages.
  - `test_corrupt_pdf_hard_failure()`: Confirms `HARD_FAILURE` on truncated/corrupted PDF.
  - `test_orphan_blank_page_detection()`: Confirms warning on blank trailing page without hard rejection.
  - `test_text_anchor_extraction_verification()`: Confirms presence of expected text anchors.
  - `test_raster_preview_generation_sampled_pages()`: Confirms valid PNG bytes generated for at most 5 sampled pages.

### 5.2 Negative & Security Tests
- `test_decompression_bomb_mitigation()`: Limits maximum processed page count to 500 pages.

### 5.3 Concurrency & Persistence Considerations
- PyMuPDF document instances are opened locally, inspected, and closed promptly in a `with fitz.open(...)` context.

---

## 6. Error and Recovery Behavior

- Corrupted PDF streams or PyMuPDF C-level exceptions are caught and mapped cleanly to `PdfInspectionResult(is_valid=False)`.

---

## 7. Observability & Audit Requirements

- Returns detailed list of `PdfVisualAnomaly` with severity, page number, and explanation.

---

## 8. Documentation Updates

- Document structural vs visual inspection in ADR-009.

---

## 9. Required Project Skills

- `codebase-design`: Clean adapter implementation of application port.
- `tdd`: Test-driven validator implementation with PDF test fixtures.

---

## 10. Verification Commands

```bash
pytest tests/unit/test_pdf_validator.py -v
python3 -c "import ast; tree = ast.parse(open('infrastructure/pdf/mupdf_validator.py').read()); assert not any('PySide6' in ast.dump(n) for n in ast.walk(tree))"
```

---

## 11. Definition of Done

- All unit tests pass with 100% coverage.
- Structural inspection and visual preview generation verified on real PDF fixtures.
