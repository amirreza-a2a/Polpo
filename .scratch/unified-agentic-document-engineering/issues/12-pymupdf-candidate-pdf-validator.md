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

1. **Structural PDF Inspection (Finding H):**
   - Header & Integrity: Verifies `%PDF-` header and successful opening with `fitz.open(request.pdf_path)`. If corrupted, records `HARD_FAILURE`.
   - Page Dimensions: Verifies page dimensions match expected format (e.g. A4: $595 \times 842 \pm 2$ pt). Flags deviations as `WARNING`.
   - Text Content Extraction: Extracts text via `page.get_text()` and checks for presence of key text anchors from `request.source_markdown`.
   - Blank Page Detection: Flags pages with $< 10$ characters of text and zero vector drawings as `WARNING` (blank page indicator).
   - Vector Graphics Detection: Checks `len(page.get_drawings()) > 0` on diagram pages; flags missing drawings as `WARNING`.
2. **Optional Visual Preview Generation:**
   - If `request.generate_raster_previews` is `True`, renders 150 DPI page preview PNGs for sampled pages (first 3 pages + last page + any page with an anomaly).
   - Enforces 50MB image memory buffer cap and 5.0-second execution timeout.
3. **Realistic Performance Target:**
   - Structural checks complete in $< 1.5$ seconds for documents under 50 pages on standard desktop hardware.

---

## 4. Acceptance Criteria

- [ ] Corrupt or empty PDF files fail inspection with `HARD_FAILURE` anomaly.
- [ ] Orphan blank pages are detected and reported as `WARNING` without hard rejection.
- [ ] Text extraction confirms source text presence across compiled pages.
- [ ] Vector drawings are verified on diagram pages.
- [ ] Page raster previews are generated cleanly as PNG byte buffers when requested.
- [ ] Implements `IPdfInspectionEngine` completely with zero Qt imports.

---

## 5. Test Specifications

### 5.1 Unit Tests
- `tests/unit/test_pdf_validator.py`:
  - `test_valid_pdf_structural_inspection()`: Confirms clean report on valid sample PDF.
  - `test_corrupt_pdf_hard_failure()`: Confirms `HARD_FAILURE` on truncated/corrupted PDF.
  - `test_orphan_blank_page_detection()`: Confirms warning on blank trailing page.
  - `test_vector_drawing_detection()`: Confirms drawings detected on TikZ test PDF.
  - `test_raster_preview_generation()`: Confirms valid PNG bytes generated for sampled pages.

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
