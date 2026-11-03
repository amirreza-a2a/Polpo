# ADR-005: Unified Quarto Document Authoring, Native TikZ/PGFPlots Preview, and PDF Pipeline

- **Status:** Accepted (Supersedes ADR-003 Decision D03)
- **Date:** 2026-11-03
- **Scope:** Markdown AST, Pandoc Parser, Diagram Rendering Subsystem, Desktop Preview, PDF Export, Prompt Engineering
- **Authoritative Baseline:** Commit `d1ce969`
- **Related ADRs:** ADR-002 (Subprocess Reliability), ADR-003 (Quarto PDF Export Integration), ADR-004 (Artifact Lineage)

---

## 1. Context

Polpo is an embedded, desktop-first, local-first Document Intelligence & Review Workspace. Document review and authoring workflows require rich rendering of technical STEM documents containing mathematical formulas and engineering diagrams (such as block diagrams, circuit schematics, and function plots).

In ADR-003 (Decision D03), TikZ diagrams were scoped to **PDF-Export-Only**, with the live editor preview rendering raw LaTeX blocks as textual placeholders or code containers. While this choice preserved 60 FPS typing responsiveness during the initial Quarto integration, user requirements and authoring ergonomics demand true visual diagram previewing directly within Polpo's native dual-pane editor without having to compile an entire PDF export.

Furthermore, legacy AI prompt templates generated proprietary ` ```tikz ` fenced blocks with standalone document wrappers (`\documentclass`, `\usepackage`, `\begin{document}`), which violated the Quarto authoring standard, broke Pandoc document parsing, and frequently failed during PDF compilation.

This ADR records the architectural decisions establishing a **unified, production-quality Quarto-compatible document pipeline** across the entire lifecycle: AI transcription, canonical AST representation, background diagram rendering, native Qt/QML preview, and Quarto/XeLaTeX PDF export.

---

## 2. Settled Architectural Decisions

### D01 — Canonical Quarto Authoring Syntax
- **Decision:** **Standard Pandoc/Quarto Raw LaTeX Fenced Blocks using ````{=latex}````.**
- **Canonical Authoring Contract:**
  ````markdown
  ```{=latex}
  \begin{tikzpicture}
      \draw[thick] (0,0) -- (2,1);
  \end{tikzpicture}
  ```
  ````
- **Rules:**
  1. No proprietary ` ```tikz ` or custom markdown fences.
  2. No standalone document wrappers inside document bodies (`\documentclass`, `\usepackage`, `\begin{document}`, `\end{document}` are strictly prohibited).
  3. Shared LaTeX packages and macros reside exclusively in the controlled document preamble (`resources/templates/polpo_preamble.tex`).
  4. Authored source remains the single source of truth across editing, preview, and PDF compilation. The canonical document is never rewritten or mutated during rendering.

### D02 — Pandoc AST Representation & RawBlock Node
- **Decision:** Pandoc represents ````{=latex} ... ```` as `RawBlock("latex", content)`.
- **AST Model:** Extend Polpo's canonical Markdown AST (`core/markdown/ast.py`) with:
  ```python
  class BlockType(str, Enum):
      ...
      RAW_BLOCK = "raw_block"

  @dataclass(frozen=True)
  class RawBlock(MarkdownBlock):
      format: str
      content: str
      block_type: BlockType = field(default=BlockType.RAW_BLOCK, init=False)
  ```
- **Parsing Invariants:**
  1. `PandocParser` transforms Pandoc AST `{"t": "RawBlock", "c": [format, content]}` into `RawBlock`.
  2. Preserves exact raw source and reverse-maps 1-indexed coordinates (`source_start_line`, `source_end_line`, `source_start_col`, `source_end_col`).
  3. Ordinary fenced code blocks (`CodeBlock`) remain unchanged and are not treated as raw executable blocks.
  4. Unknown or non-LaTeX raw blocks are preserved in the AST without silent deletion.

### D03 — Diagram Extraction & Environment Validation
- **Decision:** Introduce a bounded, deterministic TeX environment extractor (`core/markdown/diagram_extractor.py`).
- **Extraction Rules:**
  1. Scans raw LaTeX blocks for supported top-level environments: `tikzpicture`, `circuitikz`.
  2. Verifies balanced begin/end environment pairs.
  3. Checks nested environments (such as `axis`, `scope`, `pgfonlayer`) to ensure they close within the enclosing picture.
  4. Incomplete environments during interactive typing are identified and marked as incomplete, preventing futile compiler invocations.
  5. Arbitrary non-diagram raw LaTeX (e.g. `\newpage`, `\vspace{...}`) is preserved as generic `RawBlock` and not dispatched to diagram compilation.

### D04 — Diagram Rendering Application Port & Architecture Seam
- **Decision:** Define a narrow, technology-agnostic port `IDiagramRenderer` (`application/ports/diagram_renderer.py`).
- **Contracts:**
  - `DiagramRenderRequest(source: str, profile: str = "default", preamble: str = "")`
  - `DiagramRenderResult(hash: str, svg_xml: str, width: float, height: float, status: str)`
  - `DiagramRenderError(code: str, message: str)` with specialized subclasses:
    - `DiagramSyntaxError`
    - `DiagramTimeoutError`
    - `DiagramRendererUnavailableError`
    - `DiagramCancelledError`
- **Clean Architecture Invariant:** Port contracts contain zero dependencies on Qt, PySide6, QML, concrete subprocesses, or TeX distribution paths.

### D05 — Standalone TeX Diagram Compiler & SVG Generation
- **Decision:** Implement `StandaloneTeXDiagramRenderer` in `infrastructure/diagram/standalone_tex_renderer.py`.
- **Compilation Strategy:**
  1. Constructs an ephemeral compilation wrapper using the standard `standalone` class:
     ```latex
     \documentclass[tikz,border=2pt]{standalone}
     \usepackage{amsmath,amssymb}
     \usepackage{tikz}
     \usepackage{pgfplots}
     \pgfplotsset{compat=1.18}
     \usepackage{circuitikz}
     \begin{document}
     <DIAGRAM_SOURCE>
     \end{document}
     ```
  2. Executes XeLaTeX (or pdflatex fallback) in an isolated temporary directory with `-interaction=nonstopmode -no-shell-escape`.
  3. Converts the compiled single-page PDF to standalone vector SVG using PyMuPDF (`fitz.open(pdf)[0].get_svg_image()`), with fallback to `dvisvgm --pdf` if available. PyMuPDF is an existing core project dependency, guaranteeing cross-platform SVG extraction without additional external CLI dependencies.
  4. Enforces a 10.0-second execution timeout and terminates entire process trees on cancellation or expiry.
  5. Validates generated SVG content before returning.

### D06 — Content-Addressed Diagram Cache
- **Decision:** Bounded, content-addressed in-memory and ephemeral disk cache (`DiagramSvgCache`).
- **Key Derivation:** SHA-256 of `f"{renderer_version}|{profile}|{source}"`.
- **Invariants:**
  1. Cache hits return in < 1ms without invoking any compiler processes.
  2. Cache storage is strictly isolated from canonical user document artifacts (`jobs/{id}/artifacts/`).
  3. Transient preview rendering never triggers document OCC advances, database writes, or artifact modifications.

### D07 — Desktop Preview Integration & Asynchronous Concurrency
- **Decision:** Diagram compilation executes exclusively in background worker threads; the Qt GUI thread is never blocked.
- **Preview Flow:**
  1. User edits document text; editor triggers `MarkdownViewerController.scheduleLivePreview(job_id, raw_text, base_version)`.
  2. 250ms debounce timer coalesces rapid keystrokes.
  3. `MarkdownViewerService.render_preview()` executes in `MarkdownViewerWorker` thread pool.
  4. Diagram extractor identifies complete diagram blocks.
  5. Cache hits resolve immediately; cache misses compile in background.
  6. Projection produces `MarkdownNodeDTO` with `node_type="diagram"` and `diagram_hash`.
  7. `MarkdownNodeDelegate.qml` loads `diagramComponent`, querying `image://diagram/{theme}/{hash}`.
  8. `DiagramImageProvider` rasterizes SVG via `QSvgRenderer` on demand.
  9. Option B strict revision matching (`draft_revision == self._draft_revision`) drops stale results.

### D08 — Quarto PDF Export Alignment
- **Decision:** Reuse existing `QuartoPdfAdapter` and isolated PDF staging workspace.
- **Compilation Behavior:**
  1. Pandoc passes ````{=latex} ... ```` raw blocks directly into the LaTeX stream.
  2. `resources/templates/polpo_preamble.tex` provides shared packages (`tikz`, `pgfplots`, `circuitikz`) and LTR isolation hooks (`\AtBeginEnvironment{tikzpicture}{\begin{otherlanguage*}{english}}`).
  3. PDF export is decoupled from preview cache; compilation operates directly from canonical document source.

### D09 — Prompt Engineering & Persistence Reconciliation
- **Decision:** Standardize all AI prompt templates to emit canonical Quarto ````{=latex}```` syntax and update database defaults safely.
- **Prompt Rules:**
  1. All templates under `prompts/templates/fa/` and `prompts/pipeline2/` emit standard raw LaTeX blocks without standalone wrappers.
  2. Prompts mandate preservation of Persian labels, exact numerical values, circuits, and mathematical formulas.
  3. Stock prompt templates in SQLite are updated safely via idempotent reconciliation: records matching known previous default contents are updated; user-customized prompts are never overwritten.

### D10 — Security Trust Boundaries
- **Disclosures:**
  1. Compilation child processes run with ordinary desktop user privileges.
  2. `-no-shell-escape` strictly prohibits arbitrary shell command execution via `\write18`.
  3. `-no-shell-escape` does NOT provide complete OS filesystem read sandboxing for native TeX primitives (`\input`, `\openin`).
  4. Subprocesses use strict argument lists (`shell=False`), isolated temp directories, and sanitized error diagnostics without path leakage.

---

## 3. Supported Capabilities Matrix

| Package / Environment | Native Preview Support | Quarto PDF Export Support | Required Toolchain |
| :--- | :---: | :---: | :--- |
| `tikzpicture` | Yes (Vector SVG) | Yes (Vector PDF) | XeLaTeX / pdfLaTeX + PyMuPDF |
| `pgfplots` (`axis`, `\addplot`, `ycomb`) | Yes (Vector SVG) | Yes (Vector PDF) | XeLaTeX / pdfLaTeX + PyMuPDF |
| `circuitikz` | Yes (Vector SVG) | Yes (Vector PDF) | XeLaTeX / pdfLaTeX + PyMuPDF |
| `tikz-cd` | Yes (Vector SVG) | Yes (Vector PDF) | XeLaTeX / pdfLaTeX + PyMuPDF |
| Persian Labels in Diagrams | Yes (via XeLaTeX) | Yes (via XeLaTeX + Babel LTR guard) | XeLaTeX |
