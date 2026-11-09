# TICK-ADE-12: PyMuPDF Structural PDF Inspection Engine & Visual Regression Validator

- **Ticket ID:** `TICK-ADE-12`
- **Milestone:** Milestone D (AI-Assisted PDF Repair)
- **Priority:** P2
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Blocked By:** `TICK-ADE-11`
- **Authoritative ADR:** ADR-009 (Quarto PDF Failure Diagnostics & AI PDF Repair)

---

## 1. Objective

Implement `PyMuPdfValidator` in `infrastructure/pdf/mupdf_validator.py` implementing `IPdfInspectionEngine` to perform structural and visual inspection of compiled PDF candidates beyond basic process exit codes.

---

## 2. User Value

Protects authors from subtle or silent compilation failures, such as documents that compile with exit code 0 but produce blank pages, overflowing text off the page margin, dropped figures, or missing font glyphs.

---

## 3. Scope

- Define abstract port `IPdfInspectionEngine` in `application/ports/pdf_validator_port.py`:
  - `inspect_pdf(pdf_path: Path, source_markdown: str, baseline_pdf_path: Optional[Path] = None) -> PdfInspectionReport`.
- Implement `PyMuPdfValidator` in `infrastructure/pdf/mupdf_validator.py`:
  - **File & Header Integrity:** Verifies `%PDF-` header and successful opening with `fitz.open()`.
  - **Page Bounds:** Checks page dimensions match expected format (e.g. A4: $595 \times 842 \pm 2$ pt) and page count is within reasonable variance of baseline.
  - **Content Preservation:** Extracts plain text from all pages and checks for presence of key text anchors from Markdown.
  - **Blank Page Detection:** Identifies pages with $< 10$ characters of text and zero vector drawings.
  - **Vector Drawing Detection:** For documents containing TikZ/PGF diagrams, verifies that `page.get_drawings()` returns vector primitives.
  - **Visual Diff Thumbnails:** Generates 150 DPI page preview PNGs for side-by-side visual comparison in QML.

---

## 4. Explicit Non-Goals

- No heavy computer vision / OCR processing on every compile (inspection relies on fast PDF object inspection in $O(\text{pages})$).

---

## 5. Codebase References

- `infrastructure/pdf/quarto_adapter.py` (Lines 198–220: existing basic page count and magic header checks).
- `application/ports/pdf_validator_port.py` (New port).

---

## 6. Proposed Files & Components

- Create: `application/ports/pdf_validator_port.py`
- Create: `infrastructure/pdf/mupdf_validator.py`
- Create: `tests/unit/test_pdf_validator.py`

---

## 7. Domain & Architectural Constraints

- **Rule 6.2 Compliance:** `IPdfInspectionEngine` port resides in `application/ports/`, while PyMuPDF imports reside strictly in `infrastructure/pdf/`.

---

## 8. Detailed Implementation Requirements

1. Use PyMuPDF's low-overhead inspection API: `doc.page_count`, `page.rect`, `page.get_text()`, `page.get_drawings()`.
2. Execution must complete in under 500ms for documents under 50 pages.
3. If `fitz.open()` raises a corrupt PDF exception, return `PdfInspectionReport(is_valid=False, failure_reason="Corrupt PDF structure")`.

---

## 9. Acceptance Criteria

- [ ] Corrupt or empty PDF files fail inspection.
- [ ] Orphan blank pages are detected and reported as warnings.
- [ ] Text extraction confirms source text presence.
- [ ] Vector drawings are verified on diagram pages.
- [ ] Page raster previews are generated cleanly as PNG bytes.

---

## 10. Required Tests

- Unit tests in `tests/unit/test_pdf_validator.py` verifying:
  - Detection of normal valid PDF.
  - Detection of empty/corrupt PDF.
  - Warning on orphan blank page.
  - Generation of page preview thumbnails.

---

## 11. Security Requirements

- Prevent decompression bomb attacks when reading untrusted PDF streams; limit maximum page count processed to 500.

---

## 12. Completion Evidence & Follow-up

- Unit test execution evidence passing 100%.
- Completes Milestone D; unblocks Milestone F (`TICK-ADE-15`).
