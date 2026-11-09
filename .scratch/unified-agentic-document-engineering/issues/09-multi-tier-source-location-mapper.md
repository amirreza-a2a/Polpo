# TICK-ADE-09: Multi-Tier Generated TeX to Canonical Markdown Source Location Mapper

- **Ticket ID:** `TICK-ADE-09`
- **Milestone:** Milestone C (Quarto Diagnostic Infrastructure)
- **Priority:** P1
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Blocked By:** `TICK-ADE-08`
- **Authoritative ADR:** ADR-009 (Quarto PDF Failure Diagnostics & AI PDF Repair)

---

## 1. Objective

Implement the multi-tier source location mapper in `application/services/pdf_diagnostic_service.py` to map XeLaTeX error lines (which point to generated `document.tex`) back to the author's original canonical Markdown lines with confidence scoring.

---

## 2. User Value

Prevents jumping to incorrect editor lines when a PDF export fails. Clicking "Go to Error" navigates precisely to the offending formula, image, or section, or clearly informs the user that the failure is in generated template code.

---

## 3. Scope

- Implement `PdfDiagnosticService.map_tex_line_to_source()` in `application/services/pdf_diagnostic_service.py`:
  - Input: `tex_line_number: int`, `document_tex: str`, `canonical_markdown: str`.
  - Output: `SourceMappingResult(mapped_line: Optional[int], mapped_snippet: str, confidence: MappingConfidence)`.
- Multi-tier resolution logic:
  - **Tier 1 (Exact Unique Token Search):** Scans the failing TeX lines for distinctive macro tokens (e.g. `\includegraphics{crop_abc.png}`, `\begin{tikzpicture}`, `\label{...}`, specific math variables). If found uniquely in Markdown, returns line with `EXACT_TOKEN` (confidence 1.0).
  - **Tier 2 (Structural Section Anchor):** Scans backward in `document.tex` to find the nearest `\section{...}` or `\subsection{...}`. Matches the section title against Markdown `# ...` headers. Returns line with `SECTION_ANCHOR` (confidence 0.6).
  - **Tier 3 (Explicit Unmapped Fallback):** If no match is found, returns `mapped_line = None` and `confidence = UNKNOWN` (confidence 0.0), alerting the caller that the error exists in generated TeX boilerplate.

---

## 4. Explicit Non-Goals

- No naive line number equality (`tex_line == markdown_line` is strictly forbidden because template headers cause significant line drift).
- No modifying Markdown source text.

---

## 5. Codebase References

- `interfaces/desktop/controllers/export_controller.py` (Lines 620–662: existing heuristic token search in `requestPdfRepair`).
- `core/markdown/ast.py` (Header node extraction).

---

## 6. Proposed Files & Components

- Create/Modify: `application/services/pdf_diagnostic_service.py`
- Create: `tests/unit/test_pdf_source_mapper.py`

---

## 7. Domain & Architectural Constraints

- **Rule 6.2 Compliance:** `application/services/pdf_diagnostic_service.py` must have zero imports of PySide6 or Qt.

---

## 8. Detailed Implementation Requirements

1. Token extraction must ignore generic TeX commands: `\documentclass`, `\begin`, `\end`, `\usepackage`, `\par`, `\makeatletter`, `\makeatother`.
2. Section matching must strip LaTeX formatting macros (e.g. `\textbf{...}`, `\foreignlanguage{...}`) before matching Markdown heading text.
3. Whitespace normalization: Strip leading/trailing spaces and collapse multiple spaces when comparing strings.

---

## 9. Acceptance Criteria

- [ ] Distinctive TikZ diagram environments map to exact Markdown line with `EXACT_TOKEN`.
- [ ] Failing formulas inside a section without unique tokens map to the section header with `SECTION_ANCHOR`.
- [ ] Boilerplate preamble errors return `mapped_line = None` with `UNKNOWN` confidence.
- [ ] Zero naive line number assumptions.

---

## 10. Required Tests

- Unit tests in `tests/unit/test_pdf_source_mapper.py` verifying:
  - Exact token mapping for images, raw LaTeX blocks, and formulas.
  - Section header mapping when tokens are generic.
  - Correct fallback to `UNKNOWN` for preamble errors.
  - Clean handling of Persian RTL text inside section titles.

---

## 11. Security Requirements

- Prevent regex catastrophic backtracking (ReDoS) when searching for tokens in large documents. Use compiled regexes with bounded quantifiers.

---

## 12. Completion Evidence & Follow-up

- Unit test execution evidence passing 100%.
- Unblocks: `TICK-ADE-10`.
