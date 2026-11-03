# 01: Architecture Investigation and Unified Decision

**What to build:**
Complete the source-to-preview-to-PDF trace across Pandoc, Quarto, XeLaTeX, Desktop QML preview, and prompt pipelines. Establish ADR-005 superseding ADR-003 Decision D03, defining the canonical Quarto raw-LaTeX authoring contract, standalone TeX diagram compiler strategy, PyMuPDF vector SVG extraction, and content-addressed cache.

**Blocked by:**
None (can start immediately).

**Status:**
ready-for-agent

- [x] Trace end-to-end authoring, parsing, live preview, and PDF export pathways.
- [x] Verify toolchain capabilities (`xelatex`, `quarto`, `pandoc`, `fitz` PyMuPDF).
- [x] Create ADR-005 documenting settled architecture decisions.
- [x] Update ADR-003 marking Decision D03 as superseded.
- [x] Create tracer-bullet ticket decomposition with explicit blocking edges.
