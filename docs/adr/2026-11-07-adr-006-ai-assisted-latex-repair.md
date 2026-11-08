# ADR-006: AI-Assisted LaTeX Error Repair, Compiler Validation Contract, and Native QML Review Workflow

- **Status:** Accepted
- **Date:** 2026-11-07
- **Scope:** LaTeX/MathJax Rendering Pipeline, Application Services, AI Provider Orchestration, Desktop Editor Controller, QML Review UX, OCC Publication Protocol
- **Authoritative Baseline:** Commit `af6ecdc`
- **Related ADRs:** ADR-001 (Three-Way Merge Conflict Resolution), ADR-002 (MathJax Reliability), ADR-003 (Quarto PDF Export), ADR-004 (Artifact Lineage & Canonical Publication), ADR-005 (Unified Quarto Diagram Preview & Authoring)

---

## 1. Context

Polpo is an embedded, desktop-first, local-first Document Intelligence & Review Workspace. Document authoring and automated transcription frequently produce complex LaTeX constructs — including inline/display MathJax formulas and standalone TikZ/PGFPlots/CircuitTikZ/TikZ-CD diagrams embedded inside canonical Quarto ````{=latex}```` raw blocks.

During rendering (either via standalone XeLaTeX/dvisvgm, MathJax Node.js subprocesses, or full-document Quarto PDF exports), LaTeX compilation can fail due to:
- Syntax errors (unbalanced braces, missing semicolons, broken environments).
- Undefined macros or commands.
- Unsupported TeX packages or syntax variations.
- Encoding or unescaped character problems (e.g., bare `%`, `&`, `_`).

Historically, rendering failures surfaced only raw compiler error logs or error placeholder badges, requiring the author to manually interpret TeX diagnostic logs, locate the offending fragment in the raw editor pane, and perform manual edits.

This ADR formalizes the architectural design and safety boundaries for an **AI-assisted LaTeX error repair capability**. When a rendering failure occurs, the user can request the configured AI provider to repair the failing fragment. The candidate is compiled through the exact same rendering pipeline that failed; only candidates that compile cleanly to valid SVG are offered to the user in a native side-by-side review dialog; and accepted repairs are applied directly to the in-memory editor buffer without bypassing Optimistic Concurrency Control (OCC).

---

## 2. Settled Architectural Decisions

### D01 — Structured Diagnostic Context & Log Sanitization
- **Decision:** Compiler diagnostic information is structured into typed domain DTOs (`DiagramRenderError`, `MathRenderError`) and sanitized before presentation to AI models or UI components.
- **Rules:**
  1. **Log Sanitization:** Raw compiler outputs (XeLaTeX stdout/stderr, `dvisvgm` logs) are filtered through regex sanitizers to redact host filesystem paths, username directories (`/home/...`, `C:\Users\...`), and internal toolchain paths.
  2. **Bounded Log Excerpts:** Error logs are capped at 40 lines and 4,000 characters to prevent prompt token bloat while capturing the root TeX error marker (e.g., `! Missing } inserted.`).
  3. **Fragment-Relative Coordinates:** Line numbers reported by compilers are normalized relative to the fragment boundary, avoiding confusion caused by internal preamble line offsets.
  4. **Preamble & Toolchain Profiling:** The repair request includes the active environment profile (e.g., `default`, `circuitikz`, `tikz-cd`) so the AI provider understands available TeX packages and macros without generating redundant `\usepackage` commands.

### D02 — Machine-Readable Repair Contract & Security Boundaries
- **Decision:** Communication between the application and AI providers uses a strict machine-readable JSON schema enforced by `LaTeXRepairService`.
- **JSON Contract (`schema_version: 1`):**
  ```json
  {
    "schema_version": 1,
    "repaired_source": "<minimal_corrected_latex_fragment>",
    "change_summary": "<concise_english_explanation>",
    "preserved_invariants": ["syntax_valid", "semantic_intent_preserved"]
  }
  ```
- **Security & Safety Invariants:**
  1. **Minimal Fragment Only:** The candidate must be a minimal snippet drop-in replacement. Standalone document wrappers (`\documentclass`, `\usepackage`, `\begin{document}`, `\end{document}`) are strictly forbidden and cause instant candidate rejection.
  2. **No Command Execution:** Shell escape primitives (`\write18`, `\input`, `\openin`, `\catcode`) are strictly forbidden. All XeLaTeX compilations run with `-no-shell-escape`.
  3. **No Full Document Overwrite:** Candidates beginning with Markdown document headers (`# `) or YAML frontmatter (`---`) when the target fragment did not are rejected.
  4. **Size Bounds:** Candidates exceeding 64 KB are rejected to prevent memory exhaustion.

### D03 — Same-Pipeline Compiler Validation Gate
- **Decision:** No AI repair proposal may be presented to the user without prior automated verification through the identical rendering pipeline that originally failed.
- **Rules:**
  1. Math formulas (`math_inline`, `math_display`, `math_block`) are validated via `IMathRenderer.render()`.
  2. Diagrams (`diagram`, `tikz`, `pgfplots`) are validated via `IDiagramRenderer.render()`.
  3. A proposal is marked `validation_passed = True` ONLY if compilation succeeds and produces valid, non-empty SVG XML.
  4. The generated SVG XML and layout metrics are packaged directly into `LaTeXRepairProposal` to enable instant zero-latency visual previewing in the review dialog.
  5. If validation fails, `validation_passed` is set to `False` and the compiler's diagnostic message is captured.

### D04 — Bounded Self-Correction Loop
- **Decision:** If the AI provider's initial repair candidate fails compilation or returns an invalid schema, the service executes at most ONE follow-up attempt (`max_retries = 1`, maximum 2 attempts total).
- **Rules:**
  1. The follow-up prompt incorporates the exact compiler diagnostic failure from the first attempt.
  2. If the second attempt fails, the process terminates and returns a failed proposal. Unbounded retry loops are strictly prohibited to avoid rate limit depletion and unbounded latency.
  3. The request runs asynchronously in background worker threads (`QThreadPool` / `ThreadPoolExecutor`); the Qt GUI thread is never blocked.

### D05 — Native QML Review & Adoption Dialog
- **Decision:** Presentation and adoption of repair proposals are handled by dedicated native Qt/QML components (`LaTeXRepairConsentDialog.qml` and `LaTeXRepairDialog.qml`).
- **UX Requirements:**
  1. **Pre-Transmission Data Disclosure & Consent:** Prior to dispatching source code or logs to the configured AI provider, an explicit modal consent dialog discloses the exact LaTeX code and compiler diagnostic being sent, reassuring the user that no other document content or local files are transmitted.
  2. **Reactive Provider Availability:** Repair trigger actions on error cards are dynamically enabled/disabled via `canRepair`, which monitors active BYOK provider configuration.
  3. **Side-by-Side Comparison:** Clear visual diff between the original broken source and the proposed repaired source.
  4. **Vector Graphic Preview:** Renders the verified SVG preview natively so the user can visually confirm diagram/formula correctness before applying.
  5. **Theme Compliance:** All styling uses semantic tokens (`theme.accent`, `theme.surface`, `theme.error`, `theme.success`) without raw hex color literals.
  6. **Clipboard Copy:** Authors can copy the repaired LaTeX code without applying it directly to the editor buffer.
  7. **Explicit User Control:** The repair is applied ONLY upon explicit user action ("Apply Repair"). The application never silently mutates user documents.

### D06 — Atomic Editor Replacement & OCC Watermark Preservation
- **Decision:** When the user applies a repair proposal, `MarkdownEditorController` replaces the exact target fragment in the active editor buffer using `QTextCursor` atomic edit blocks.
- **Invariants:**
  1. **Duplicate Fragment Disambiguation:** When a LaTeX fragment occurs multiple times in a document, `MarkdownEditorController.applyRepair()` uses the proposal's `source_range` (line boundaries) to target the exact occurrence. If the location remains ambiguous, the operation fails closed without modifying the buffer, advising the user to replace manually.
  2. **Undo/Redo Integrity:** The edit block preserves the Qt `QUndoStack`, allowing the user to press `Ctrl+Z` to revert the repair immediately.
  3. **In-Memory Modification Only:** Applying a repair marks the document buffer dirty (`is_dirty = True`) but DOES NOT commit a new canonical version to SQLite automatically.
  4. **OCC Watermark Invariant:** The editor buffer's `active_version` watermark remains unchanged. When the user subsequently saves the document, standard Optimistic Concurrency Control verifies that `base_version == canonical_version`. If a concurrent edit occurred, `StaleDocumentVersionError` is raised and standard 3-way merge conflict resolution is invoked.
  5. **Stale Repair Rejection:** If the canonical document version changed or if the original fragment was modified while the repair request was in flight, the repair proposal is rejected with a descriptive error.

### D07 — Architectural Boundary & Dependency Direction
- **Decision:** In accordance with `AGENTS.md` and Clean Architecture principles:
  1. `LaTeXRepairService` resides in `application/services/`, depending only on domain entities (`ApiSlot`, `Job`) and application ports (`IAIExecutionService`, `IDiagramRenderer`, `IMathRenderer`, `IUnitOfWorkFactory`).
  2. It has zero dependencies on PySide6, Qt, QML, sqlite3, or vendor SDKs.
  3. `MarkdownEditorController` in `interfaces/desktop/controllers/` orchestrates background execution and exposes typed Qt signals/slots (`repairProposalReady`, `repairFailed`, `requestRepair`, `applyRepair`, `canRepair`).
  4. Quarto PDF export compilation errors in `ExportController` map specific failing TeX control sequences to Markdown editor positions, avoiding naive line number jumping.

---

## 3. Verification & Compliance Record

| Gate / Test Suite | Scope | Status |
|-------------------|-------|--------|
| `test_diagram_renderer_diagnostics.py` | Line numbers, log excerpts, syntax error categorization | PASS (6 tests) |
| `test_latex_repair_service.py` | Schema parsing, isolation boundaries, same-pipeline validation, bounded retry, openout rejection, template injection safety | PASS (14 tests) |
| `test_markdown_editor_repair.py` | Async dispatch, buffer replacement, undo stack, OCC conflict prevention, canRepair, duplicate occurrence disambiguation | PASS (11 tests) |
| `test_latex_repair_qml.py` | QML review dialog, consent dialog loading, property bindings, theme token compliance | PASS (3 tests) |
| `test_export_pdf_repair.py` | Quarto PDF error mapping, token-based navigation, boundary checks | PASS (2 tests) |
| `test_latex_repair_e2e.py` | End-to-end integration: seed SQLite, AI fix, validation, buffer replace, OCC save | PASS (2 tests) |
| `scripts/run_quality_gates.py` | Diff hygiene, bytecode compilation, 88 architecture AST rules (164 subtests) | PASS |
| `test_latex_repair_security.py` | Comment-aware tokenizer, forbidden TeX commands, path traversal prevention | PASS (5 tests) |
| `test_diagram_security.py` | Kpathsea sandbox, canary reads/writes, shell escape blocking, package preservation | PASS (6 tests) |
| `test_acceptance_preflight.py` | Strict OS target classification (Ubuntu 24.04 x86_64) and overall status computation | PASS (11 tests) |
| `test_qml_e2e_acceptance.py` | Full Markdown -> Pandoc -> AST -> ViewerService -> Scene Graph offscreen render | PASS (1 test) |
| `test_interactive_repair_journey.py` | 26-step interactive journey: consent, validation, reject, apply, undo, redo, OCC | PASS (2 tests) |
| `scripts/acceptance/run_rendering_acceptance.py` | Full 9-stage acceptance harness on Ubuntu 24.04 LTS | PASS (8 passed, 1 skipped) |

---

## 4. Amendment 1: TeX Execution Security Sandbox, Kpathsea Enforcement, and Native QML Scene Graph Acceptance

- **Date:** 2026-11-08
- **Scope:** Standalone TeX Diagram Renderer, Subprocess Supervisor, LaTeX Repair Safety Tokenizer, QML E2E Scene Graph Testing

### D08 — TeX Subprocess Filesystem Isolation & Kpathsea Paranoid Policy
1. **Kpathsea Paranoid Configuration:** Because `-no-shell-escape` alone does not prevent native TeX primitives (`\input`, `\openin`, `\openout`, `\read`, `\write`) from traversing the local filesystem, all TeX subprocesses (XeLaTeX) are invoked with an explicit sandbox environment:
   - `openin_any = "p"` (disallows opening dot-files or absolute paths outside the tree).
   - `openout_any = "p"` (disallows writing to dot-files or parent directories).
   - `shell_escape = "f"` (strictly forbids shell escapes).
   - `TEXMFOUTPUT = str(workdir)` (confines all auxiliary outputs to the isolated temporary workspace).
2. **Credential Sanitization:** All environment variables matching sensitive patterns (`API`, `KEY`, `SECRET`, `TOKEN`, `AUTH`, `CREDENTIAL`, `PASS`, `POLPO`, `GEMINI`, `OPENAI`, `AWS`, `GITHUB`) are completely scrubbed before spawning TeX compiler child processes.
3. **Comment-Aware Safety Tokenizer:** In `LaTeXRepairService`, candidate LaTeX sources are tokenized by first stripping unescaped TeX comments (`%...`) while preserving escaped percent characters (`\%`). Unescaped forbidden control sequences (`\input`, `\include`, `\openin`, `\openout`, `\read`, `\write`, `\write18`, `\catcode`, `\special`, `\documentclass`, `\usepackage`, `\begin{document}`) and directory traversals (`../`) are strictly rejected. Legitimate diagram text (such as `\node {input};` or comments discussing input files) are safely preserved.

### D09 — Native QML Offscreen Scene Graph Acceptance Contract
1. Synthetic image provider unit tests are supplemented by a full offscreen Qt Quick Scene Graph pipeline test (`test_qml_e2e_acceptance.py`).
2. The acceptance harness parses a full mixed Quarto document (Markdown, MathJax formulas, TikZ diagrams, PGFPlots curves, CircuitTikZ schematics, TikZ-CD lattices, and intentional error syntax cards), projects AST nodes through `MarkdownViewerService`, injects vector SVGs into `DiagramSvgCache` and `MathSvgCache`, mounts `MarkdownDocumentModel` into `MarkdownView.qml`, and captures the rendered window via `QQuickView.grabWindow()`.
3. Acceptance requires visual verification of rendered multi-color vector graphics (>20 distinct pixel colors) with zero crashes or unhandled QML exceptions.
