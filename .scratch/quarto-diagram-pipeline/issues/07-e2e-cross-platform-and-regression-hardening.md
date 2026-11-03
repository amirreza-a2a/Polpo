# 07: End-to-End, Cross-Platform, and Regression Hardening

**What to build:**
Assemble comprehensive test fixtures (Fixtures A through G: simple TikZ, PGFPlots function, discrete signal `ycomb`, Persian/English diagram, multiple diagrams, malformed diagram, mixed document). Verify cross-platform safety (Windows subprocess flags, Linux offscreen Qt), and ensure zero regression of existing MathJax, visual region crops, or table rendering.

**Blocked by:**
04: Native Desktop Preview Integration, 05: Quarto PDF Export Integration, 06: Prompt and Persistence Reconciliation

**Status:**
ready-for-agent

- [ ] Add Fixtures A through G in `tests/fixtures/diagrams/`.
- [ ] Add mixed-document end-to-end integration tests verifying simultaneous MathJax and TikZ rendering.
- [ ] Verify Windows compatibility (`CREATE_NO_WINDOW`, path separators, file locking).
- [ ] Verify memory bounding, process cleanup, and absence of zombie subprocesses.
- [ ] Run full test suite to confirm zero regressions.
