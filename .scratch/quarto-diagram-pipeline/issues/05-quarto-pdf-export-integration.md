# 05: Quarto PDF Export Integration

**What to build:**
Ensure canonical raw LaTeX `{=latex}` blocks pass through the existing Quarto PDF export pipeline correctly. Verify preamble packages, RTL isolation, staging security, PyMuPDF vector drawing assertions in output PDFs, and decoupling from preview cache.

**Blocked by:**
02: Canonical Quarto Raw-LaTeX AST Support

**Status:**
ready-for-agent

- [ ] Verify Quarto CLI passes `{=latex}` blocks directly to XeLaTeX writer.
- [ ] Align preamble packages in `resources/templates/polpo_preamble.tex` with preview toolchain.
- [ ] Add integration tests in `tests/integration/test_quarto_pdf_diagrams.py` verifying real PDF compilation, non-empty drawings, correct page counts, and absence of raw TikZ source in extracted text.
- [ ] Verify export functions independently of preview cache.
