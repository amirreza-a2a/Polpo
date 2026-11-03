# 03: Diagram Rendering Port and Infrastructure Adapter

**What to build:**
Define technology-agnostic diagram renderer application port `IDiagramRenderer` and implement `StandaloneTeXDiagramRenderer` that compiles LaTeX diagrams in isolated temporary workspaces with `-no-shell-escape`, extracts vector SVGs via PyMuPDF, and caches results in a bounded, content-addressed `DiagramSvgCache`.

**Blocked by:**
02: Canonical Quarto Raw-LaTeX AST Support

**Status:**
ready-for-agent

- [ ] Create `application/ports/diagram_renderer.py` with typed request, result, and structured exceptions.
- [ ] Implement `DiagramSvgCache` in `infrastructure/diagram/cache.py` with SHA-256 content-addressing and LRU eviction.
- [ ] Implement `StandaloneTeXDiagramRenderer` in `infrastructure/diagram/standalone_tex_renderer.py` using XeLaTeX/pdfLaTeX and PyMuPDF vector SVG extraction.
- [ ] Implement execution timeout (10s), process supervision, and full process cleanup.
- [ ] Add unit tests in `tests/unit/test_diagram_renderer.py` covering valid TikZ, PGFPlots, CircuitTikZ, syntax errors, timeouts, and cache operations.
