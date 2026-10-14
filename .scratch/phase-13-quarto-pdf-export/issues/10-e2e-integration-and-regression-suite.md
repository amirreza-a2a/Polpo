# 10: End-to-End Integration, Cross-Platform Verification & P08 Regression Proof

**What to build:** Implement comprehensive end-to-end integration tests in `tests/integration/test_quarto_pdf_export_e2e.py` and regression verifications covering all 10 V3.8 design scenarios. Validates cross-platform execution on Linux and Windows, intermediate `.tex` character-offset ordering (F-02), typed schema enforcement with `papersize` (F-01), bilingual Persian RTL typesetting with TikZ English isolation (ADR-003 D02), visual region image embedding, cancellation under load, and CI conditional gating.

**Blocked by:** 09 (Desktop Presentation Integration, Setup Assistant & Recovery Dialog).

**Status:** ready-for-agent

## Scope

- Implement `tests/integration/test_quarto_pdf_export_e2e.py`:
  - **CI Readiness Gate:** Adds `@pytest.mark.skipif(not is_quarto_installed(), reason="Quarto not installed")` to ensure test suite runs cleanly on CI runners without requiring local TeX. Mocks and unit tests provide 100% CI coverage when runtimes are absent.
  - **Scenario 1 (Include Placement Isolation & F-02 Verification):** Compiles document with root headers, format headers, inline headers, before-body text, and after-body text with `keep-tex: true`. Inspects intermediate `.tex` character offsets and asserts:
    `offset(polpo_preamble) < offset(root_header) < offset(format_header) < offset(inline_header) < offset(\begin{document}) < offset(before_body) < offset(body) < offset(after_body) < offset(\end{document})`.
  - **Scenario 2 (Document Class Precedence):** Passes root `report` and format `scrartcl`. Asserts generated `.tex` declares `\documentclass{scrartcl}`.
  - **Scenario 3 (Custom Format Extension Rejection):** Tests `format: acm-pdf`, `format: [pdf, acm-pdf]`, and `format: {acm-pdf: {}}`. Asserts all raise `UnsupportedMetadataKeyError` before launching subprocesses.
  - **Scenario 4 (Multi-Format Pruning):** Tests `format: {html: {theme: cosmo}, pdf: {toc: true}}`. Asserts canonical Markdown is unmodified, staged file prunes `html`, and Quarto compiles exit code 0.
  - **Scenario 5 (Typed Schema & F-01 Verification):** Tests `papersize: 123` and `papersize: ["a4"]` at root and format scopes. Asserts `MalformedMetadataError`. Valid string `papersize: "a4"` compiles cleanly.
  - **Scenario 6 (Lua Filter Attack Containment):** Tests front matter declaring `filters: ["exploit.lua"]`. Asserts `UntrustedDocumentExtensionError`. Zero subprocess spawn; exploit payload never executes.
  - **Scenario 7 (External Include File Rejection):** Tests `include-before-body: "evil.tex"` and `{"file": "evil.tex"}`. Asserts `UntrustedDocumentExtensionError`.
  - **Scenario 8 (Bilingual Persian RTL & TikZ Isolation — ADR-003 D02):** Tests document containing Persian RTL text, mathematical equations, and a TikZ schematic. Asserts XeLaTeX compiles cleanly exit code 0, producing valid `%PDF-` artifact with English-isolated TikZ labels.
  - **Scenario 9 (Visual Region Crop Embedding):** Ingests sample PDF, extracts visual region `crop_{region_id}.png`, generates Markdown reference, and exports PDF. Asserts crop is staged, embedded in PDF, and staging workspace is unlinked.
  - **Scenario 10 (Cooperative Cancellation & Timeout):** Triggers cancellation mid-compile. Asserts subprocess tree killed immediately, no orphan processes remain, and staging directory is purged.
- **Cross-Platform Verification:**
  - Validates path separators, Windows CRLF vs LF line endings in staged `.qmd`, and `CREATE_NO_WINDOW` execution on Windows.

## Non-Goals

- Implementing interactive live previewing of TikZ in the editor pane (prohibited by ADR-003 D03).
- Modifying canonical Markdown files on disk.

## Files Likely Impacted

- Create: `tests/integration/test_quarto_pdf_export_e2e.py`
- Create: `tests/fixtures/sample_bilingual_doc.qmd`
- Create: `tests/fixtures/sample_crop.png`

## Public Interfaces / Contracts

```python
def is_quarto_installed() -> bool:
    """Helper for conditionally skipping real compilation on CI runners lacking Quarto."""
    ...


@pytest.mark.integration
class TestQuartoPdfExportE2E:
    def test_e2e_include_ordering_in_intermediate_tex(self): ...
    def test_e2e_bilingual_persian_and_tikz_compilation(self): ...
    def test_e2e_custom_format_rejection(self): ...
    def test_e2e_papersize_validation(self): ...
    def test_e2e_cancellation_under_load(self): ...
```

## Architectural Invariants

- **Zero Canonical Mutation:** Author Markdown files must remain byte-identical before and after export operations.
- **Complete Cleanup:** Temporary staging workspaces must be completely unlinked after tests complete.
- **Cross-Platform Consistency:** Tests must pass identically under Linux and Windows environments.

## Acceptance Criteria

- [ ] All 10 end-to-end and regression test scenarios implemented and passing.
- [ ] **F-01 Verified:** `papersize` type enforcement covered by unit and E2E assertions.
- [ ] **F-02 Verified:** Intermediate `.tex` character-offset assertions prove exact ordering of `polpo_preamble.tex`, author headers, and body elements.
- [ ] **F-03 Verified:** Subprocess execution verified with `-no-shell-escape`; non-sandboxed filesystem reads documented.
- [ ] Bilingual Persian RTL document with TikZ compiles to a valid multi-page PDF document.
- [ ] Visual region image crops are embedded properly into the generated PDF artifact.
- [ ] Subprocess cancellation and timeouts kill child processes and purge staging workspaces without leaking files.
- [ ] CI runner without Quarto cleanly skips live compilation tests while running all mock integration tests.

## Verification / Tests Required

- Run `pytest tests/integration/test_quarto_pdf_export_e2e.py -v`.
- Run on Linux reference environment (Ubuntu 24.04).
- Run on Windows reference environment (Windows 11 / Server 2025).

## Commit Boundary

Single commit: `test(export): implement end-to-end integration and regression test suite for quarto pdf export`
