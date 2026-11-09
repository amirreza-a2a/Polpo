# TICK-ADE-10: Deterministic First-Stage Error Classifier & Scope Separator

- **Ticket ID:** `TICK-ADE-10`
- **Milestone:** Milestone C (Quarto Diagnostic Infrastructure)
- **Priority:** P1
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Blocked By:** `TICK-ADE-08`, `TICK-ADE-09`
- **Authoritative ADR:** ADR-009 (Quarto PDF Failure Diagnostics & AI PDF Repair)

---

## 1. Objective

Implement the deterministic first-stage error classifier in `application/services/pdf_diagnostic_service.py` to categorize compilation failures into discrete scopes (`SOURCE_SYNTAX`, `PACKAGE_MISSING`, `FONT_MISSING`, `TEMPLATE_CONFLICT`, `PANDOC_TRANSFORM`, `LAYOUT_OVERFLOW`, `PRODUCT_DEFECT`) and enforce D-USER-03 scope separation.

---

## 2. User Value

Prevents the AI assistant from proposing improper fixes — such as deleting strikethrough from an author's text when a package conflict occurs (the `soul` RTL issue), or attempting to edit installed read-only product templates.

---

## 3. Scope

- Implement `PdfDiagnosticService.classify_error(diagnostic: DiagnosticBundle) -> ErrorClassificationResult`:
  - Rules for `PACKAGE_MISSING`: Matches `! LaTeX Error: File '...sty' not found` or `missing_packages` populated by sanitizer.
  - Rules for `FONT_MISSING`: Matches `The font "..." cannot be found` or `fontspec error`.
  - Rules for `TEMPLATE_CONFLICT`: Matches known XeLaTeX/package interactions (e.g. `Package soul Error: Reconstruction failed.` in RTL documents, `microtype` option collisions, `xcolor` clash).
  - Rules for `SOURCE_SYNTAX`: Matches `! Undefined control sequence`, `! Missing $ inserted`, `! Extra }`, or syntax errors within document blocks.
  - Rules for `PANDOC_TRANSFORM`: Matches `pandoc: ...` errors, YAML front-matter parse errors, or table syntax failures.
  - Rules for `PRODUCT_DEFECT`: Matches errors occurring within `polpo_preamble.tex` lines (e.g. `\polpoOrigIncludegraphics` definitions) where author Markdown is standard.
- Enforce repair scope recommendation:
  - Returns `recommended_scope`: `DOCUMENT_MARKUP`, `YAML_METADATA`, `PUBLICATION_STYLE`, or `PRODUCT_DEFECT_REPORT`.

---

## 4. Explicit Non-Goals

- No AI model-based classification for the first stage (classification must be deterministic, reproducible, and fast).
- No mutating global product templates during user document tasks.

---

## 5. Codebase References

- `infrastructure/pdf/tex_log_sanitizer.py` (Pattern matching for packages and fonts).
- `resources/templates/polpo_preamble.tex` (Known preamble definitions).
- ADR-009 Section 7.4 (Analysis of the `soul` strikethrough failure).

---

## 6. Proposed Files & Components

- Modify: `application/services/pdf_diagnostic_service.py`
- Create: `tests/unit/test_error_classifier.py`

---

## 7. Domain & Architectural Constraints

- **D-USER-03 Compliance:** The classifier must strictly isolate product-level issues from document repairs.
- **Rule 6.2 Compliance:** Application services contain zero Qt imports or subprocess spawns.

---

## 8. Detailed Implementation Requirements

1. Classifier must explicitly test for the mandatory failure case:
   `Package soul Error: Reconstruction failed.` with Persian language enabled. Must classify as `ErrorCategory.TEMPLATE_CONFLICT` with `recommended_scope = PUBLICATION_STYLE` (overriding strikeout with `ulem`) and flag a `PRODUCT_DEFECT_REPORT`.
2. Classifier must run in under 5 milliseconds on log texts up to 100KB using pre-compiled regex patterns.

---

## 9. Acceptance Criteria

- [ ] Missing `.sty` errors classified as `PACKAGE_MISSING` and routed to package recovery.
- [ ] Missing font errors classified as `FONT_MISSING`.
- [ ] `Package soul Error: Reconstruction failed.` classified as `TEMPLATE_CONFLICT`.
- [ ] Preamble macro definitions errors classified as `PRODUCT_DEFECT`.
- [ ] Output provides unambiguous `recommended_scope` preventing destructive edits to author Markdown.

---

## 10. Required Tests

- Unit tests in `tests/unit/test_error_classifier.py` verifying:
  - Correct categorization of 15+ real-world XeLaTeX and Quarto compiler error logs.
  - Mandatory verification of the `soul` reconstruction error under Persian RTL.
  - Verification of missing package and font classifications.

---

## 11. Security Requirements

- Disallow regexes with unbounded repetitions on untrusted compiler logs to prevent ReDoS.

---

## 12. Completion Evidence & Follow-up

- Unit test execution evidence passing 100%.
- Completes Milestone C; unblocks Milestone D (`TICK-ADE-11`).
