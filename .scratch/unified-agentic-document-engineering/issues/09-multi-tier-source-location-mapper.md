# TICK-ADE-09: Multi-Tier Generated TeX to Canonical Markdown Source Location Mapper

- **Ticket ID:** `TICK-ADE-09`
- **Title:** Multi-Tier Generated TeX to Canonical Markdown Source Location Mapper
- **Milestone:** Milestone C (Quarto Diagnostic Infrastructure)
- **Priority:** P1
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Scope:** Implement the multi-tier source location mapper in `application/services/pdf_diagnostic_service.py` to map XeLaTeX error lines (pointing to generated `document.tex`) back to the author's original canonical Markdown lines with confidence scoring.
- **Explicit Non-Goals:**
  - No naive line number equality (`tex_line == markdown_line` is strictly forbidden because template headers cause significant line drift).
  - No silent automated patching when mapping confidence is `UNKNOWN` (Finding G).
  - No modifying Markdown source text during mapping.
- **Prerequisites:** `TICK-ADE-08` completed; ADR-009 approved.
- **Dependencies (Blocked By):** `TICK-ADE-08`.
- **Unblocks:** `TICK-ADE-10`.

---

## 1. Existing Source-Code Integration Points

- `interfaces/desktop/controllers/export_controller.py`: Lines 620–662 (existing heuristic token search in `requestPdfRepair`).
- `core/markdown/ast.py`: Header node and raw block extraction for structural section matching.

---

## 2. Proposed Application Interfaces & DTOs

```python
@dataclass(frozen=True)
class SourceMappingResult:
    mapped_line: Optional[int]           # 1-indexed line in canonical Markdown, or None
    mapped_snippet: str                  # Contextual Markdown lines
    confidence: MappingConfidence        # EXACT_TOKEN (1.0), CONTEXTUAL_MATCH (0.7), SECTION_ANCHOR (0.5), UNKNOWN (0.0)
    matched_token: Optional[str] = None
```

---

## 3. Detailed Implementation Requirements

1. **Multi-Tier Resolution Logic (Finding G):**
   - **Tier 1 (`EXACT_TOKEN`, 1.0):** Extracts distinctive macro tokens from failing TeX lines (e.g. `\includegraphics{crop_abc.png}`, `\begin{tikzpicture}`, `\label{...}`, distinctive variable names). Searches active Markdown for a unique exact match.
   - **Tier 2 (`CONTEXTUAL_MATCH`, 0.7):** Identifies surrounding phrase tokens, normalizing common Pandoc transformations (e.g. `\st{sample}` $\to$ `~~sample~~`, mathematical delimiters, escaped characters). Searches Markdown for matching phrase.
   - **Tier 3 (`SECTION_ANCHOR`, 0.5):** Scans backward in `document.tex` from error line to nearest `\section{...}` or `\subsection{...}`. Matches section title against Markdown `# ...` headers. Maps to header line.
   - **Tier 4 (`UNKNOWN`, 0.0):** If error is inside preamble boilerplate or unmapped macros, sets `mapped_line = None` with `UNKNOWN` confidence.
2. **Ambiguity Gating & Patch Authorization Invariant (Finding C):**
   - **`EXACT_TOKEN` (1.0):** Authorizes targeted document patch generation.
   - **`CONTEXTUAL_MATCH` (0.7):** Authorizes document patch generation after verifying surrounding text context.
   - **`SECTION_ANCHOR` (0.5):** Insufficient for automatic exact Patch generation; surfaces section coordinate for human inspection only.
   - **`UNKNOWN` (0.0):** If confidence is `UNKNOWN`, the system displays the failing TeX snippet directly to the user; the Agent is strictly forbidden from applying automated replacements or generating source patches.

---

## 4. Acceptance Criteria

- [ ] Distinctive TikZ diagram environments map to exact Markdown line with `EXACT_TOKEN`.
- [ ] Pandoc strikeout transformations (`\st{...}` $\to$ `~~...~~`) map accurately via `CONTEXTUAL_MATCH`.
- [ ] Failing formulas inside a section without unique tokens map to the section header with `SECTION_ANCHOR`.
- [ ] Boilerplate preamble errors return `mapped_line = None` with `UNKNOWN` confidence.
- [ ] Zero naive line number assumptions.

---

## 5. Test Specifications

### 5.1 Unit Tests
- `tests/unit/test_pdf_source_mapper.py`:
  - `test_exact_token_mapping_for_images_and_tikz()`: Confirms image crop token matches.
  - `test_pandoc_strikethrough_contextual_mapping()`: Confirms `\st{...}` maps back to `~~...~~`.
  - `test_section_header_anchor_mapping()`: Confirms fallback to nearest heading.
  - `test_preamble_boilerplate_maps_to_unknown()`: Confirms `mapped_line is None`.
  - `test_persian_rtl_heading_mapping()`: Confirms clean matching with Persian characters.

### 5.2 Negative & Security Tests
- `test_prevent_regex_catastrophic_backtracking()`: Verifies bounded execution time on large documents ($< 50$ms).

### 5.3 Concurrency & Persistence Considerations
- Pure text analysis in application layer; thread-safe and stateless.

---

## 6. Error and Recovery Behavior

- Unmatched tokens fall back gracefully down the tier hierarchy without throwing uncaught exceptions.

---

## 7. Observability & Audit Requirements

- Returns confidence score and matched token in `SourceMappingResult`.

---

## 8. Documentation Updates

- Document 4-tier mapping strategy in ADR-009.

---

## 9. Required Project Skills

- `diagnosing-bugs`: Analyzing Pandoc AST transformations.
- `tdd`: Test-driven source mapper implementation.

---

## 10. Verification Commands

```bash
pytest tests/unit/test_pdf_source_mapper.py -v
```

---

## 11. Definition of Done

- All unit tests pass with 100% coverage.
- Multi-tier source mapping verified across Latin and Persian documents.
