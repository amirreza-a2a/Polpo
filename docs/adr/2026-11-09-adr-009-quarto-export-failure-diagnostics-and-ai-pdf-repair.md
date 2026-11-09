# ADR-009: Quarto PDF Export Failure Diagnostics, Source Mapping, and AI-Assisted Candidate PDF Repair

- **Status:** Proposed (Awaiting Maintainer Approval)
- **Date:** 2026-11-09
- **Scope:** Quarto PDF Compilation Pipeline, Staging Failure Capture, Diagnostic Bundle, Source-to-TeX Mapping, Error Classification, Candidate Full-Document Recompilation, PyMuPDF Vector Inspection, Repair Scope Separation
- **Authoritative Baseline:** Commit `25bb95d198c34c035b5d66513166e06494d82cdb`
- **Related ADRs:** ADR-003 (Quarto PDF Export Integration), ADR-005 (Unified Quarto Diagram Pipeline), ADR-006 (AI LaTeX Repair), ADR-007 (Agent Orchestration), ADR-008 (Working Copy & Patch Architecture), ADR-010 (Publication Style System)
- **Confirmed Requirements:** D-USER-01 (Unified Assistant Scope), D-USER-02 (Supervised Agent Autonomy), D-USER-03 (Document vs Rendering-System Repair)

---

## 1. Context

Polpo features an integrated publication-grade PDF export pipeline based on the external Quarto CLI and XeLaTeX ([ADR-003](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/docs/adr/2026-10-12-adr-003-quarto-pdf-export-integration.md)). Export execution takes place inside an ephemeral staging workspace managed by `StagingWorkspaceManager` ([`infrastructure/pdf/staging_workspace.py`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/infrastructure/pdf/staging_workspace.py#L430)) and orchestrated by `QuartoPdfExportService` ([`application/services/quarto_pdf_export_service.py`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/application/services/quarto_pdf_export_service.py#L206)).

During compilation, complex documents can fail due to Markdown formatting defects, broken LaTeX equations, incompatible front-matter metadata, TikZ diagram syntax errors, missing TeX packages, font mismatches, or layout engine collisions (such as the observed `Package soul Error: Reconstruction failed.` failure under Persian/RTL typesetting).

While ADR-006 ([`docs/adr/2026-11-07-adr-006-ai-assisted-latex-repair.md`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/docs/adr/2026-11-07-adr-006-ai-assisted-latex-repair.md)) introduced isolated fragment repair for individual MathJax formulas and TikZ diagrams, full-document Quarto PDF export failures currently lack automated diagnostic preservation, reliable source location mapping, and whole-document validation.

---

## 2. Problem Statement

1. **Ephemeral Workspace Purge on Failure:** When Quarto or XeLaTeX fails, `StagingWorkspaceManager` deterministically deletes the staging directory (`shutil.rmtree(staging_dir)`). The intermediate generated LaTeX (`document.tex`) and the detailed compiler logs are deleted immediately, preventing subsequent inspection or diagnosis.
2. **Generated TeX vs. Markdown Line Offset Disconnect:** XeLaTeX error logs report line coordinates corresponding strictly to `document.tex` (e.g. `l.384 ... \st{Strikethrough}`). Because `document.tex` includes Pandoc templates, preambles, and macro expansions, line 384 does not match line 384 of the user's Markdown. Naive line navigation jumps to completely wrong document locations.
3. **Truncated Diagnostics:** The existing `TexLogSanitizer` truncates compiler error logs to 6–8 lines, which often discards essential context (such as undefined command sequences or package stack traces).
4. **Scope Conflation (Violation of D-USER-03):** Without clear scope classification, an AI repair system might attempt to fix a product-level template defect or package conflict (e.g. `soul` in RTL) by deleting legitimate content (e.g. removing strikethrough from the author's Markdown text) or by attempting to mutate read-only product templates (`resources/templates/polpo_preamble.tex`).
5. **Shallow Compilation Validation:** Declaring an AI repair successful based solely on process exit code (`returncode == 0`) is unreliable; documents may compile without error but suffer from layout overflow, blank pages, or dropped figures.

---

## 3. Architectural Forces & Constraints

1. **Repair Scope Separation (D-USER-03):** Document markup repairs, YAML metadata options, publication style configurations, and Polpo product defects must remain strictly isolated. A document task must never mutate the globally installed `polpo_preamble.tex`.
2. **Whole-Pipeline Verification:** A full-document PDF correction must be verified through the actual Quarto/XeLaTeX pipeline on an isolated `AgentWorkingCopy`, not merely through isolated MathJax or standalone snippet renderers.
3. **Data Minimization & Consent ([`AGENTS.md`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/AGENTS.md) Rules 10 & 11):** Transmitting compiler logs to external AI providers requires pre-transmission consent and strict redaction of host paths, usernames, and staging hashes.
4. **Disk Hygiene:** Diagnostics must not leak unbounded temporary directories on disk; retention must be bounded and automatically garbage-collected.
5. **No Model Self-Authorization:** The AI model cannot decide which scope to mutate; classification and policy gating are enforced deterministically by the application layer.

---

## 4. Confirmed Requirements (Maintainer Settled)

- **D-USER-03 (Both Document and Rendering-System Repair):** PDF repair must cover both document-level failures (Markdown source, LaTeX fragments, math, diagrams, metadata) and rendering-system failures (Pandoc transforms, generated LaTeX, preamble conflicts, publication templates, fonts, RTL configuration). These scopes must remain separate. Document tasks must never modify installed product templates; product defects must be reported for the normal development workflow.

---

## 5. Considered Alternatives

### Alternative A: Persist Entire Staging Workspace on Every Failure Indefinitely
- *Description:* Disable `shutil.rmtree()` on failure and leave `.pdf_staging/<job_id>_<token>/` intact.
- *Rejection Rationale:* Causes severe disk bloat. A few compilation attempts on large documents with high-resolution image assets would rapidly consume gigabytes of user storage.

### Alternative B: Naive Source Line Mapping
- *Description:* Treat the line number reported in `l.<N>` from the TeX log as the Markdown line number.
- *Rejection Rationale:* Fundamentally broken. In Quarto, `document.tex` contains 100–300 lines of template and preamble before document content begins. Jumps to completely unrelated sections of the author's document.

### Alternative C: Proposed Decision — Structured Diagnostic Bundle, Multi-Tier Source Mapping, and Candidate Recompilation Validation
- *Description:* Before purging the staging directory, extract a bounded `DiagnosticBundle` containing the sanitized log, intermediate TeX excerpt, and mapping metadata. Use a multi-tier mapper (exact token search $\to$ section anchor $\to$ explicit unknown flag). Recompile candidates on an `AgentWorkingCopy` and validate with PyMuPDF.
- *Selection Rationale:* Eliminates disk bloat; provides high-confidence source localization; enforces the strict scope separation required by D-USER-03.

---

## 6. Proposed Decision

We propose establishing the **Quarto PDF Diagnostic and AI-Assisted Repair System** comprising:
1. **Diagnostic Bundle Extraction:** Enhancing `QuartoPdfAdapter` and `StagingWorkspaceManager` to capture a structured `DiagnosticBundle` before workspace purge.
2. **Bounded Diagnostic Retention:** An in-memory/ephemeral diagnostic store keeping at most 5 failure bundles per job (capped at 10MB total).
3. **Multi-Tier Source Location Mapper:** Mapping generated TeX errors to canonical Markdown coordinates with explicit confidence scoring (`EXACT_TOKEN`, `SECTION_ANCHOR`, `UNKNOWN`).
4. **Deterministic First-Stage Error Classifier:** Categorizing failures into discrete scopes: `SOURCE_SYNTAX`, `PACKAGE_MISSING`, `FONT_MISSING`, `TEMPLATE_CONFLICT`, `PANDOC_TRANSFORM`, `LAYOUT_OVERFLOW`, or `PRODUCT_DEFECT`.
5. **Candidate Full-Document Recompilation & PyMuPDF Inspection:** Validating candidate patches on an `AgentWorkingCopy` via full Quarto export and verifying page count, text preservation, and vector drawing presence.

---

## 7. Detailed Design

### 7.1 Architecture & Component Boundaries

```text
Application Layer (application/)
 ├── dto/pdf_diagnostic_dto.py            ──> DiagnosticBundle, SourceMappingResult, ErrorCategory
 ├── services/pdf_diagnostic_service.py   ──> Error classification, multi-tier source mapping
 ├── services/pdf_repair_service.py       ──> AI repair prompt orchestration, candidate patch generation
 └── ports/pdf_validator_port.py          ──> IPdfInspectionEngine (PyMuPDF validation)
Infrastructure Layer (infrastructure/)
 ├── pdf/quarto_adapter.py                ──> Captures DiagnosticBundle before purging staging dir
 ├── pdf/staging_workspace.py             ──> Exposes intermediate document.tex extraction hook
 ├── pdf/tex_log_sanitizer.py             ──> Extended sanitizer (100 lines / 10KB bounded excerpt)
 └── pdf/mupdf_validator.py               ──> PyMuPDF visual and structural document inspection
```

### 7.2 The `DiagnosticBundle` Contract

```python
class ErrorCategory(Enum):
    SOURCE_SYNTAX = "SOURCE_SYNTAX"          # Broken LaTeX formula, malformed raw block, unclosed tag
    PACKAGE_MISSING = "PACKAGE_MISSING"      # Missing CTAN package (handled by PackageRecoveryService)
    FONT_MISSING = "FONT_MISSING"            # Requested font family not found in OS
    TEMPLATE_CONFLICT = "TEMPLATE_CONFLICT"  # Macro/package conflict in preamble (e.g. soul in RTL)
    PANDOC_TRANSFORM = "PANDOC_TRANSFORM"    # Pandoc AST / table / list conversion error
    LAYOUT_OVERFLOW = "LAYOUT_OVERFLOW"      # Overfull \vbox, oversized unscaled graphic
    PRODUCT_DEFECT = "PRODUCT_DEFECT"        # Bug in polpo_preamble.tex or engine barrier config
    UNKNOWN = "UNKNOWN"

@dataclass(frozen=True)
class DiagnosticBundle:
    """Sanitized failure snapshot captured from an ephemeral compilation workspace."""
    failure_id: str                          # UUID4
    job_id: int
    canonical_version: int
    error_category: ErrorCategory
    sanitized_message: str                   # Primary clean error string
    bounded_log_excerpt: str                 # Up to 100 lines / 10,000 characters of sanitized log
    failing_tex_line: Optional[int]          # Line number reported in generated document.tex
    failing_tex_snippet: str                 # 10 lines surrounding failing line in document.tex
    mapped_markdown_line: Optional[int]      # Mapped line in canonical Markdown, if found
    mapped_markdown_snippet: str             # Contextual Markdown snippet
    mapping_confidence: MappingConfidence    # EXACT_TOKEN (1.0), SECTION_ANCHOR (0.6), UNKNOWN (0.0)
    missing_packages: Tuple[str, ...]
    missing_fonts: Tuple[str, ...]
    preamble_fingerprint: str                # SHA-256 of effective polpo_preamble.tex
    captured_at: float
```

### 7.3 Multi-Tier Source Location Mapping Strategy

Because Pandoc transforms Markdown into LaTeX non-linearly, the system uses a 3-tier mapping algorithm to locate the offending source fragment:

```mermaid
flowchart TD
    Start[TeX Log Line l.384] --> ExtractTeX[Extract 5 lines around l.384 in document.tex]
    ExtractTeX --> T1{Tier 1: Exact Unique Token Search}

    T1 -->|Found uniquely in Markdown| M1[Set mapped_line = token_line<br/>Confidence = EXACT_TOKEN (1.0)]
    T1 -->|Not found or ambiguous| T2{Tier 2: Nearest Section Anchor}

    T2 -->|Nearest \\section matches Markdown header| M2[Set mapped_line = header_line<br/>Confidence = SECTION_ANCHOR (0.6)]
    T2 -->|No structural match| T3[Tier 3: Unmapped TeX Snippet]

    T3 --> M3[Set mapped_line = None<br/>Confidence = UNKNOWN (0.0)<br/>UI displays TeX snippet directly]
```

1. **Tier 1 (Exact Token Matching):** Scans the failing TeX snippet for distinctive macro sequences (e.g. `\includegraphics{crop_abc123.png}`, `\begin{tikzpicture}`, specific formula variables). Searches the active Markdown text for an exact, unambiguous match. If exactly one match is found, maps to that coordinate with `EXACT_TOKEN` confidence.
2. **Tier 2 (Structural Section Anchor):** Scans backward in `document.tex` to find the nearest `\section{...}` or `\subsection{...}` command. Maps to the corresponding Markdown `# ...` heading. Informs the user/agent that the error is located within that section (`SECTION_ANCHOR` confidence).
3. **Tier 3 (Explicit Unmapped Fallback):** If no anchor can be determined with confidence, `mapped_markdown_line` is set to `None`. The system presents the failing TeX snippet directly, avoiding deceptive navigation to unrelated Markdown lines.

### 7.4 Analysis & Resolution of Mandatory Example: The `soul` Strikethrough Bug

#### Observed Error:
```text
Package soul Error: Reconstruction failed.
l.384 ... \st{Strikethrough}
```
Originating Markdown: `~~Strikethrough~~`

#### Root Cause Investigation:
1. Pandoc translates `~~...~~` to `\st{...}` and automatically injects `\usepackage{soul}` into the generated LaTeX stream under XeLaTeX.
2. In Polpo, `polpo_preamble.tex` configures bidirectional typesetting via `babel` with `persian` as the main language using the HarfBuzz renderer (`\babelprovide[main,import]{persian}`).
3. The `soul` package analyzes its arguments by tokenizing them and attempting hyphenation reconstruction. When non-ASCII UTF-8 characters, Persian RTL glyphs, or directional primitives (`\beginL ... \endL`) are processed, `soul`'s token reconstruction fails completely, throwing `Package soul Error: Reconstruction failed.`.

#### Scope-Specific Resolution Enforcement (D-USER-03):
The classifier assigns this failure: `error_category = TEMPLATE_CONFLICT`.

The Assistant evaluates the three possible repair scopes:
- **Scope 1: Document Markup Change (REJECTED by Policy):** Removing `~~...~~` or rewriting text to avoid strikethrough alters the author's intent and is forbidden.
- **Scope 2: Document Publication Style Override (PROPOSED for Task):** The Assistant generates a style profile patch overriding the strikethrough engine in the document's assigned `PublicationStyleProfile`:
  ```latex
  \usepackage[normalem]{ulem}
  \renewcommand{\st}[1]{\sout{#1}}
  ```
  The candidate is applied to the `AgentWorkingCopy` and recompiled. `ulem` does not tokenize syllables and compiles Persian and Latin strikethrough cleanly without error.
- **Scope 3: Product Defect Report (FLAGGED for Maintainer):** The Assistant files an internal product-defect report: *"polpo_preamble.tex lacks a native ulem override for Pandoc strikeout under XeLaTeX RTL."* The Agent **never** attempts to edit `resources/templates/polpo_preamble.tex` during user task execution.

### 7.5 PyMuPDF Candidate PDF Inspection

When a candidate PDF compiles with `returncode == 0`, `PyMuPdfValidator` validates the artifact prior to presenting it to the user:

1. **File Integrity:** Verifies `%PDF-` header, non-zero file size, and successful document opening via `fitz.open()`.
2. **Page Count & Dimensions:** Verifies page count is within expected variance of base document ($\pm 20\%$) and page dimensions match profile (e.g. A4: $595 \times 842 \pm 2$ pt). Rejects sudden runaway page explosion.
3. **Text Content Preservation:** Extracts plain text from the compiled PDF and verifies that key content tokens from the Markdown source are present in the PDF text layer.
4. **Blank Page Detection:** Identifies empty or nearly empty pages (page text length $< 10$ characters and zero vector drawing rects), warning if orphan blank pages were introduced.
5. **Vector Graphics Presence:** Where technical diagrams (TikZ/PGFPlots) exist in the source, verifies that vector drawing primitives (`page.get_drawings()`) exist on the corresponding compiled page.

---

## 8. Dependency & Data Flow

```text
[PDF Export Fails in QuartoPdfAdapter]
                 │
                 ▼
[Extract DiagnosticBundle before staging purge]
 ├── Capture sanitized stdout/stderr + *.log
 ├── Read 10 lines around error in document.tex
 └── Run multi-tier source mapper
                 │
                 ▼
[Store in Ephemeral Diagnostic Store] (Job-scoped LRU)
                 │
                 ▼
[Emit Qt Signal: pdfCompilationFailedWithDiagnostic(bundle)]
                 │
                 ▼
[AgentController: User requests AI PDF Repair]
                 │
                 ▼
[Pre-Transmission Consent Dialog] (Discloses bundle excerpt)
                 │
                 ▼
[AI Provider Generates Candidate Patch] (Document or Style scope)
                 │
                 ▼
[AgentWorkingCopyService: Apply to Scratchpad]
                 │
                 ▼
[QuartoPdfAdapter: Recompile Candidate in Staging]
                 │
                 ▼
[PyMuPdfValidator: Inspect Candidate PDF]
                 │
                 ▼
[Present Side-by-Side Candidate Preview & Diff in QML]
```

---

## 9. Security & Privacy Considerations

1. **Pre-Transmission Disclosure:** Before sending diagnostic data to an external AI provider, `LaTeXRepairConsentDialog` displays the exact sanitized compiler log excerpt and failing TeX line, guaranteeing zero sensitive file path or credential leakage.
2. **Subprocess Isolation:** Recompilation of candidate PDFs always enforces `-no-shell-escape` and executes inside isolated staging directories.
3. **Product Template Immutability:** Application-level templates (`polpo_preamble.tex`) are read-only to user processes and cannot be overwritten by agent tasks.

---

## 10. Failure & Recovery Behavior

1. **Recompilation Failure:** If an AI-generated candidate patch fails recompilation, the error is fed back into a bounded follow-up loop (maximum 1 follow-up attempt). If the second attempt fails, the task terminates in `FAILED` status, presenting the remaining compiler error to the user without infinite loops.
2. **Corrupted Candidate PDF:** If compilation succeeds but PyMuPDF reports corrupted pages or zero text, the candidate is rejected with `InvalidCompiledArtifactError`.

---

## 11. Consequences

### Positive:
- **Actionable Diagnostics:** Preserves intermediate TeX evidence before staging purge, turning cryptic TeX errors into understandable diagnostics.
- **High-Precision Navigation:** Multi-tier mapping avoids jumping to false lines in the Markdown editor.
- **Deep Validation:** Candidate verification through PyMuPDF ensures visual and typographic integrity beyond naive exit codes.
- **Architectural Discipline:** Strict scope separation guarantees that product bugs are not papered over by destructive document edits.

### Negative / Trade-offs:
- **Disk Storage for Bundles:** Holding up to 5 diagnostic bundles per job consumes up to 10MB of local storage per document (managed via strict LRU eviction).
- **Recompilation Latency:** Validating a candidate through full Quarto and XeLaTeX requires 2–5 seconds of background processing per candidate.

---

## 12. Migration & Rollout Strategy

1. **Phase 1:** Update `QuartoPdfAdapter` to extract `DiagnosticBundle` before `finally: shutil.rmtree(staging_dir)` executes.
2. **Phase 2:** Implement `application/services/pdf_diagnostic_service.py` with multi-tier source mapping and the deterministic error classifier.
3. **Phase 3:** Implement `infrastructure/pdf/mupdf_validator.py` for candidate PDF inspection.
4. **Phase 4:** Connect the diagnostic bundle to the unified assistant review panel in QML.

---

## 13. Testing & Verification Strategy

- **Diagnostic Capture Tests:** Unit tests verifying that `DiagnosticBundle` captures failing TeX lines and bounded logs from simulated compiler failures.
- **Source Mapper Tests:** Test multi-tier mapping against sample `document.tex` and `document.qmd` pairs, verifying `EXACT_TOKEN`, `SECTION_ANCHOR`, and `UNKNOWN` confidence assignments.
- **RTL Strikethrough Characterization Test:** Test reproducing the `soul` failure under Persian RTL and verifying that applying the `ulem` style override compiles cleanly.
- **PyMuPDF Validator Tests:** Test detection of empty PDFs, page count mismatches, and vector drawing presence on test PDF artifacts.
