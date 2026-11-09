# Architecture Research Report: Polpo Unified Agentic Document Engineering System

- **Mission Baseline:** `25bb95d198c34c035b5d66513166e06494d82cdb` (main)
- **Release Baseline:** `v0.1.0-rc.2` (strictly preserved)
- **Date:** 2026-11-09
- **Status:** Complete / Awaiting Maintainer Approval
- **Authoritative Directives:** D-USER-01 (Unified Assistant Scope), D-USER-02 (Supervised Agent Autonomy), D-USER-03 (Document vs Rendering-System Repair)
- **Architectural Rules:** [`AGENTS.md`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/AGENTS.md), [`CONTEXT.md`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/CONTEXT.md)

---

## 1. Executive Summary

This report establishes the technical and architectural foundation for the next major capability milestone of Polpo: the **Unified Agentic Document Engineering System**.

Polpo is an embedded, desktop-first, local-first, serverless Document Intelligence & Review Workspace built on PySide6/QML, SQLite WAL, and direct outbound client-to-cloud AI provider connections (BYOK). While Polpo currently possesses specialized AI capabilities — such as isolated LaTeX fragment repair ([ADR-006](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/docs/adr/2026-11-07-adr-006-ai-assisted-latex-repair.md)), OCR text refinement (Pipeline 2, [ADR-004](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/docs/adr/2026-10-22-adr-004-pipeline2-artifact-lineage-and-canonical-publication-contract.md)), and technical diagram rendering ([ADR-005](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/docs/adr/2026-11-03-adr-005-unified-quarto-diagram-preview-and-authoring.md)) — these capabilities operate as disconnected, point-to-point features.

Under maintainer directives **D-USER-01**, **D-USER-02**, and **D-USER-03**, Polpo will introduce a single **Unified AI Assistant** governed by a provider-neutral orchestration architecture. The assistant operates under a **Supervised Agent** autonomy model: it can formulate multi-step plans, invoke typed internal tools, inspect document and compiler diagnostics, create isolated working copies, and re-validate candidates through full Quarto/XeLaTeX compilations; however, consequential actions (external data transmission, package installation, canonical document commits) are gated on deterministic application policy and explicit human approval.

This research report audits existing systems, identifies concrete reusable seams, investigates external compiler and typesetting constraints (specifically Pandoc/Quarto error tracking and XeLaTeX RTL failure modes such as the `soul` strikethrough bug), details the domain model, and records formal decisions.

---

## 2. Codebase Audit: Existing Architecture & Reusable Seams

A thorough audit of the repository at commit `25bb95d` reveals robust foundational subsystems that must be leveraged and generalized rather than duplicated.

```text
Presentation Layer (interfaces/desktop)
 ├── controllers/export_controller.py          ──> Triggers PDF exports, intercepts errors
 ├── controllers/markdown_editor_controller.py ──> Manages RAM draft buffer, OCC watermark, undo stack
 └── qml/dialogs/LaTeXRepairDialog.qml         ──> Native modal review dialog for AI proposals
Application Services (application/services)
 ├── latex_repair_service.py                   ──> Structured JSON repair, compiler validation, bounded retries
 ├── quarto_pdf_export_service.py              ──> Quarto orchestration, OCC checking, atomic publication
 ├── document_publication_service.py           ──> 4-phase crash-consistent canonical publication
 └── metadata_normalizer.py                    ──> Versioned YAML allowlist schema validation
Application Ports (application/ports)
 ├── ai_executor.py                            ──> Abstract port: IAIExecutionService
 ├── pdf_export_port.py                        ──> IPdfExportEngine, IPdfRuntimeDiscovery
 └── diagram_renderer.py / math_renderer.py    ──> IDiagramRenderer, IMathRenderer
Infrastructure Adapters (infrastructure/)
 ├── ai/executor_service.py                    ──> RateLimitedAIExecutor (fallback chain progression)
 ├── pdf/quarto_adapter.py                     ──> Subprocess execution, exit code mapping
 ├── pdf/staging_workspace.py                  ──> Ephemeral staging, asset closure, barrier isolation
 ├── pdf/tex_log_sanitizer.py                  ──> Redacts absolute paths, extracts line numbers
 └── security/keyring_resolver.py              ──> Hardware-grade OS keyring credential resolution
```

### 2.1 AI Execution & Fallback Infrastructure
- **Component:** [`IAIExecutionService`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/application/ports/ai_executor.py#L10) and [`RateLimitedAIExecutor`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/infrastructure/ai/executor_service.py#L26).
- **Current Behavior:** Manages an ordered chain of `ApiSlot` configurations (e.g. Gemini 1.5 Pro $\to$ Gemini 1.5 Flash $\to$ OpenAI GPT-4o). Handles centralized rate limiting via `IRateLimiter.wait_if_needed()`, catches standardized `AIError` domain exceptions, and executes automatic fallback chain advancement via [`FallbackChainPolicy.advance_chain()`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/core/policies/fallback_policy.py).
- **Reuse Assessment:** The text execution method `execute_text_with_fallback(chain, prompt, input_text, ...)` is completely provider-neutral. It supports structured JSON output schemas via prompt instructions and post-parsing validation. The new Agent Orchestrator can leverage this directly without needing vendor-specific proprietary tool-calling SDKs in the application core.

### 2.2 LaTeX Repair Service & Pre-Transmission Consent
- **Component:** [`LaTeXRepairService`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/application/services/latex_repair_service.py#L143) and [`LaTeXRepairConsentDialog.qml`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/interfaces/desktop/qml/dialogs/LaTeXRepairConsentDialog.qml).
- **Current Behavior:** Implements ADR-006:
  1. Structures failing fragment context and sanitizes diagnostics.
  2. Discloses exact payload to user in a modal consent dialog before network dispatch.
  3. Dispatches structured prompt (`prompts/repair/latex_repair_v1.txt`).
  4. Parses and validates machine-readable JSON output (`schema_version: 1`).
  5. Enforces safety rules: strips comments, rejects `\documentclass`, `\usepackage`, path traversals, and file I/O primitives.
  6. Compiles candidate through identical compiler pipeline (MathJax or Standalone XeLaTeX/dvisvgm).
  7. Executes at most one bounded self-correction attempt (`max_retries = 1`).
- **Reuse Assessment:** Demonstrates the exact pattern for the broader Agent: pre-transmission consent, structured JSON contracts, same-pipeline automated verification, and bounded retry governance.

### 2.3 Quarto PDF Export & Staging Workspace
- **Component:** [`QuartoPdfExportService`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/application/services/quarto_pdf_export_service.py#L206), [`QuartoPdfAdapter`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/infrastructure/pdf/quarto_adapter.py#L52), and [`StagingWorkspaceManager`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/infrastructure/pdf/staging_workspace.py#L430).
- **Current Behavior:**
  1. Validates destination path and pre-flight OCC state against `uow.publish_intents` and `uow.document_versions`.
  2. Creates an ephemeral staging workspace (`.pdf_staging/<job_id>_<token>/`).
  3. Injects barrier `_quarto.yml` enforcing `-no-shell-escape`, `-interaction=nonstopmode`, `-halt-on-error`.
  4. Copies `polpo_preamble.tex` and resolves visual asset closure with path containment checks.
  5. Executes Quarto CLI under [`SubprocessSupervisor`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/infrastructure/pdf/subprocess_supervisor.py) governance with a 60-second timeout.
  6. On success: copies compiled PDF out of staging and atomically publishes it via [`PdfArtifactPublisher`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/application/ports/pdf_export_port.py).
- **Identified Critical Gap:**
  When compilation fails (non-zero return code), [`QuartoPdfAdapter.render_pdf()`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/infrastructure/pdf/quarto_adapter.py#L161) aggregates stdout/stderr and `.log` files, parses them with `TexLogSanitizer`, and raises `PdfCompilationError`.
  *Critically*, exiting the `with self._staging.create_session(...)` block triggers `shutil.rmtree(staging_dir)` in the `finally` block. Consequently, the intermediate generated `document.tex` and detailed compiler logs are deleted immediately! The caller only receives `PdfCompilationError` containing a truncated 6-line sanitized message and a line number.
  Furthermore, the line number in the TeX error log (e.g. `l.384`) points to `document.tex`, not the user's `document.qmd` or canonical Markdown. Because `document.tex` was destroyed, neither the user nor the system can inspect what was at line 384.

### 2.4 Document Publication & Optimistic Concurrency Control (OCC)
- **Component:** [`DocumentPublicationService`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/application/services/document_publication_service.py#L46).
- **Current Behavior:** Sole authoritative gateway for publishing canonical Markdown. Executes a 4-phase crash-consistent commit:
  1. *Intent Reservation:* `PublishIntentRecord` inserted in SQLite under `BEGIN IMMEDIATE`, verifying `base_version == latest_version`.
  2. *Staging & Disk Flush:* Content written to `.tmp` file and fsynced.
  3. *Atomic Activation:* Renamed to canonical filename (`output_{job_id}_v{version}.md`) via `os.replace`.
  4. *Pointer & Version Update:* `DocumentVersionRecord` inserted and `jobs.output_path` updated in SQLite.
- **Architectural Boundary:** The Agent must NEVER write directly to `document_versions` or overwrite canonical Markdown files. All agent-generated document modifications must be validated on an isolated `AgentWorkingCopy` and applied to the active editor buffer or committed through `DocumentPublicationService` under OCC.

---

## 3. External Technical Research & Standards Verification

All external technical facts and constraints have been verified against primary documentation and standards.

### 3.1 Quarto & Pandoc Error Semantics and Intermediate File Retention
- **Primary Sources:**
  - Quarto CLI Documentation: *PDF Options & LaTeX Compilation* ([https://quarto.org/docs/output-formats/pdf-basics.html](https://quarto.org/docs/output-formats/pdf-basics.html))
  - Pandoc User's Guide: *Creating a PDF with Pandoc* ([https://pandoc.org/MANUAL.html#creating-a-pdf-with-pandoc](https://pandoc.org/MANUAL.html#creating-a-pdf-with-pandoc))
- **Findings:**
  1. Quarto executes Pandoc to transform Markdown/QMD into LaTeX (`document.tex`), then invokes the configured PDF engine (XeLaTeX).
  2. By default, Quarto deletes `document.tex` upon exit. Passing `keep-tex: true` in YAML metadata instructs Quarto to leave `document.tex` on disk.
  3. When XeLaTeX fails, Quarto outputs the error log to stderr/stdout. However, XeLaTeX error messages report line numbers corresponding strictly to `document.tex`.
  4. Pandoc can output source-position annotations in intermediate representations (such as Pandoc AST with `--track-changes` or custom filters), but raw XeLaTeX error line numbers do not automatically map to Markdown line numbers because Pandoc macro expansions, template headers, and fenced raw blocks shift line offsets unpredictably.

### 3.2 XeLaTeX & TeX Live RTL Typesetting: The `soul` Strikethrough Failure
- **Primary Sources:**
  - TeX Live Documentation: *The `soul` package: Hyphenation for letterspacing, underlining, and striking out* (CTAN: `soul`, Melchior Franz)
  - CTAN: *The `ulem` package: Package for underlining and strikethrough* (Donald Arseneau)
  - LaTeX Project / Babel: *Babel user guide: Bilingual and multilingual documents* (Javier Bezos)
- **Failure Analysis of Mandatory Example:**
  ```text
  Package soul Error: Reconstruction failed.
  l.384 ... \st{Strikethrough}
  ```
  The author wrote `~~Strikethrough~~` in Markdown.
  1. Pandoc's default LaTeX template contains:
     ```latex
     $if(strikeout)$
     \usepackage{soul}
     $endif$
     ```
     and translates `~~...~~` into `\st{...}`.
  2. The `soul` package operates by examining arguments token-by-token and reconstructing hyphenation/syllables.
  3. Under XeLaTeX, when bidirectional text or non-ASCII Unicode (such as Persian with `babel` using `bidi=default` and HarfBuzz) is present inside or adjacent to `\st{...}`, `soul`'s token scanner encounters font switching primitives, directional nodes (`\beginL ... \endL`), or multi-byte UTF-8 character tokens.
  4. `soul` cannot reconstruct the hyphenation register for these tokens, raising `Package soul Error: Reconstruction failed.`.
- **Architectural Resolution across Scopes (D-USER-03):**
  - *Document-Level Scope:* The author's Markdown `~~Strikethrough~~` is semantically correct. Replacing or removing strikethrough is a destructive and improper workaround.
  - *Configuration/Preamble Scope:* The document's `PublicationStyleProfile` or front-matter `header-includes` can override the strikeout engine by loading `ulem` without altering Markdown source:
    ```latex
    \usepackage[normalem]{ulem}
    \renewcommand{\st}[1]{\sout{#1}}
    ```
    The `ulem` package does not tokenize syllables and safely wraps Persian and Latin text across direction switches.
  - *Product-Level Scope:* Polpo's `resources/templates/polpo_preamble.tex` must include this override natively to prevent all future users from encountering this known XeLaTeX/RTL conflict.

### 3.3 PySide6 / Qt Concurrency & GUI Responsiveness
- **Primary Sources:**
  - Qt 6 Documentation: *Thread Support in Qt & QThreadPool* ([https://doc.qt.io/qt-6/threads.html](https://doc.qt.io/qt-6/threads.html))
  - PySide6 Documentation: *Signals and Slots Across Threads* ([https://doc.qt.io/qtforpython-6/](https://doc.qt.io/qtforpython-6/))
- **Findings:**
  1. Long-running tasks (compilation, AST analysis, AI requests) must run in `QThreadPool` worker threads or a managed `ThreadPoolExecutor`.
  2. Worker threads must never touch QML items or QObject properties directly. Communication must occur via Qt Signals with queued connections (`Qt.QueuedConnection`).
  3. Presentation controllers expose thread-safe slots and emit Qt signals that the QML engine consumes reactively.

### 3.4 Provider-Neutral Tool Orchestration vs. Native Tool Calling
- **Investigation:** Should the Agent use provider-native function calling (e.g. OpenAI function calling, Gemini Tools) or structured JSON schema planning?
- **Analysis:**
  - *Native Tool Calling:* Vendor-specific APIs differ significantly between Gemini (`tools=[...]`), OpenAI (`tools=[{"type": "function", ...}]`), Anthropic, and local endpoints. Supporting native tool calling requires concrete vendor SDK imports or complex translation adapters in application ports, violating Clean Architecture ([`AGENTS.md`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/AGENTS.md) Rule 6.2).
  - *Provider-Neutral JSON Schema Planning:* The existing `RateLimitedAIExecutor` reliably executes text prompts with fallback across all configured providers. Prompts can specify a typed JSON plan/tool schema. The application service parses, validates, and dispatches tool execution deterministically.
- **Decision:** The foundational Agent Orchestrator uses provider-neutral JSON schema orchestration. Native tool calling is deferred to a future provider adapter layer without changing the core application contracts.

### 3.5 PDF Quality Inspection & Visual Validation
- **Primary Sources:**
  - PyMuPDF Documentation: *Document, Page, and Rect API* ([https://pymupdf.readthedocs.io/](https://pymupdf.readthedocs.io/))
- **Findings:**
  A successful exit code from XeLaTeX (`returncode == 0`) does NOT prove document correctness. Common silent failures include:
  1. Overfull `\hbox` creating text overflowing off the page margin.
  2. Accidental page-count explosion (e.g. unconstrained TikZ diagrams or oversized images pushing content onto blank trailing pages).
  3. Corrupted font glyphs (empty squares or missing characters when a font lacks glyph coverage).
  4. Missing vector graphics.
  Using PyMuPDF (`fitz`), Polpo can deterministically validate:
  - Page count and page dimensions (A4: 595x842 pt).
  - Text presence and character count matching the source document.
  - Visual bounding boxes of vector graphics (`page.get_drawings()`).
  - Blank or near-blank page detection.
  - High-resolution offscreen raster comparison against previous successful builds.

---

## 4. Ubiquitous Domain Language & Boundaries

In accordance with Clean Architecture and Domain-Driven Design, the following terms define the canonical vocabulary for the Agent subsystem:

```text
                               ┌────────────────────────────────┐
                               │           AgentTask            │
                               │  (State Machine & Journal)     │
                               └───────────────┬────────────────┘
                                               │
                         ┌─────────────────────┴─────────────────────┐
                         ▼                                           ▼
            ┌────────────────────────┐                  ┌────────────────────────┐
            │       AgentPlan        │                  │    AgentWorkingCopy    │
            │  (Ordered PlanSteps)   │                  │  (Isolated Scratchpad) │
            └────────────┬───────────┘                  └────────────┬───────────┘
                         │                                           │
                         ▼                                           ▼
            ┌────────────────────────┐                  ┌────────────────────────┐
            │        ToolCall        │                  │     DocumentPatch      │
            │  (Allowlisted Action)  │                  │  (Line Range / Meta)   │
            └────────────┬───────────┘                  └────────────┬───────────┘
                         │                                           │
                         ▼                                           ▼
            ┌────────────────────────┐                  ┌────────────────────────┐
            │   ToolExecutionResult  │                  │    ValidationResult    │
            │  (Observation/Output)  │                  │  (Compiler + PyMuPDF)  │
            └────────────────────────┘                  └────────────────────────┘
```

1. **`AgentTask`:** The root aggregate governing a single user-initiated document engineering mission. Tracks lifecycle states, user approvals, execution policies, and cost/token budgets.
2. **`AgentPlan`:** A structured sequence of `PlanStep` entities generated by the assistant to fulfill the task. Subject to explicit user approval before consequential execution.
3. **`PlanStep`:** An individual, atomic action within a plan, identifying the target tool, arguments, risk level, and preconditions.
4. **`ToolCall`:** A typed request to execute an allowlisted internal capability registered in the `ToolRegistry`.
5. **`ToolExecutionResult`:** The structured output produced by a tool, containing execution status, returned data, error details, and execution duration.
6. **`ExecutionPolicy`:** Deterministic rules governing tool execution, such as maximum step count, timeout limits, and required authorization levels.
7. **`UserApproval`:** An explicit, recorded user decision granting permission to proceed with a plan, a high-risk action, or canonical patch adoption.
8. **`ExternalTransmissionConsent`:** Explicit, informed authorization to transmit specific document fragments and sanitized diagnostics to an external AI provider.
9. **`AgentWorkingCopy`:** An isolated, in-memory/scratchpad document snapshot where candidate patches, metadata adjustments, and style profiles are tested without touching canonical files.
10. **`DocumentPatch`:** A discrete, targeted modification describing additions, deletions, or replacements of source lines, front-matter metadata, or style profiles.
11. **`ValidationResult`:** The outcome of automated verification, combining full-pipeline compiler exit codes, sanitized diagnostics, and PyMuPDF layout analysis.
12. **`DiagnosticBundle`:** A structured snapshot of compiler failure evidence, including sanitized logs, intermediate TeX excerpts, mapped source lines, and preamble fingerprints.
13. **`PublicationStyleProfile`:** A versioned, declarative specification of document typography, geometry, colors, and layout separate from desktop application themes.
14. **`AgentExecutionJournal`:** An append-only SQLite log recording state transitions, tool invocations, and user decisions for auditability and recovery.

---

## 5. Decision Register

The following register tracks settled architectural decisions, clearly distinguishing maintainer-confirmed constraints from proposed agent recommendations awaiting review:

| ID | Topic / Question | Status | Recommendation | Alternatives Considered | Rationale | Impact | Related ADR | Maintainer Approval |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **D-USER-01** | Unified Assistant Scope | **Confirmed** | Single unified AI assistant orchestrating multiple document-engineering tools. | Disconnected, specialized chatbots for each feature. | Settled maintainer directive; unified UX, reusable orchestration, shared state. | Entire system | ADR-007 | Confirmed |
| **D-USER-02** | Supervised Agent Autonomy | **Confirmed** | Supervised autonomy: autonomous planning and temporary scratchpad work, but gated on plan approval, consent, and patch review. | 1. Fully autonomous agent.<br>2. Confirmation modal for every step. | Settled maintainer directive; guarantees user safety and privacy without UX fatigue. | UX, Controller, Core Policy | ADR-007 | Confirmed |
| **D-USER-03** | Document vs System Repair | **Confirmed** | Separate document-level, config-level, preamble-level, and product-defect repairs. | Blindly editing document or modifying global preamble. | Settled maintainer directive; prevents document workarounds for product bugs and protects global templates. | Repair Service, Diagnostics | ADR-009 | Confirmed |
| **D-PROP-01** | Orchestrator Protocol | **Proposed** | Provider-neutral structured JSON schema orchestration via `IAIExecutionService`. | Vendor-native function-calling SDKs (OpenAI, Gemini). | Preserves Clean Architecture; zero vendor SDK leakage in core/application layers; fallback works seamlessly. | AI Execution, Application Services | ADR-007 | Required |
| **D-PROP-02** | Working Copy Isolation | **Proposed** | RAM/scratchpad `AgentWorkingCopy` tracking base version, draft revision, and SHA-256 hash. | 1. Modifying active editor buffer directly.<br>2. Creating intermediate SQLite canonical versions. | Protects human editor undo stack and OCC invariants; ensures zero silent canonical commits. | Editor Controller, Working Copy | ADR-008 | Required |
| **D-PROP-03** | Staging Failure Retention | **Proposed** | Capture structured `DiagnosticBundle` (bounded log + `.tex` snippet) before purging staging workspace. | 1. Retain entire staging dir indefinitely.<br>2. Delete everything and keep only 6-line message. | Prevents disk bloat while capturing essential generated TeX evidence required for root-cause diagnosis. | Staging Manager, Quarto Adapter | ADR-009 | Required |
| **D-PROP-04** | Source Mapping Strategy | **Proposed** | Multi-tier mapper: 1. Exact token search; 2. Section/heading anchor; 3. Explicit `UNKNOWN` flag. | Naive line-number translation. | XeLaTeX lines point to `document.tex`; naive line mapping jumps to completely wrong Markdown positions. | Diagnostics, Repair UX | ADR-009 | Required |
| **D-PROP-05** | Publication Style System | **Proposed** | Versioned `PublicationStyleProfile` schema with built-in templates and SQLite user profiles. | Unrestricted raw LaTeX injection in document front matter. | Prevents malicious TeX command injection; provides reproducible, declarative styling across documents. | Metadata, Quarto Export | ADR-010 | Required |
| **D-PROP-06** | Task Persistence & Recovery | **Proposed** | Append-only SQLite `agent_tasks` and `agent_journal` tables; mark running tasks `INTERRUPTED` on startup. | 1. Silently resume network operations on restart.<br>2. Pure in-memory state (lost on restart). | Prevents duplicate API costs and unapproved mutations after crash; provides complete audit trail. | Persistence, Lifecycle | ADR-007 | Required |

---

## 6. Risk Register & Technical Debt Prevention

| Risk Identifier | Threat / Failure Mode | Severity | Likelihood | Mitigation Strategy in Proposed Architecture |
| :--- | :--- | :--- | :--- | :--- |
| **R-01** | **Unbounded AI Cost / Rate-Limit Exhaustion** | High | Medium | Task-level budgets: maximum 10 steps per plan, maximum 2 self-correction retries, exponential backoff via existing `IRateLimiter`. |
| **R-02** | **Silent Document Corruption** | Critical | Low | Zero canonical write access for the Agent. All changes must be validated on `AgentWorkingCopy` and approved by user into editor buffer under OCC. |
| **R-03** | **Sensitive Data Leakage to External AI** | Critical | Medium | Pre-transmission consent modal (`LaTeXRepairConsentDialog` pattern) disclosing exact payload; automated path/credential redaction in logs. |
| **R-04** | **Arbitrary Code Execution via TeX** | High | Low | TeX compilation always runs with `-no-shell-escape`; metadata normalizer allowlists keys; preamble extensions strictly vetted. |
| **R-05** | **Disk Exhaustion from Diagnostics** | Medium | Medium | Diagnostic bundle retention is bounded: max 5 bundles per job, max 10MB total, automatic LRU cleanup on job completion. |
| **R-06** | **UI Freezing During Compilation** | High | Medium | All compilation and validation tasks run off-thread via `QThreadPool`; signals decoupled via `Qt.QueuedConnection`. |
| **R-07** | **False Sense of Correctness** | Medium | High | Compiler exit code 0 is NOT treated as semantic success; candidate PDFs undergo PyMuPDF inspection (text extraction, page bounds, drawings). |

---

## 7. Conclusion & Next Steps

The existing Polpo architecture is exceptionally well-suited to host the Unified Agentic Document Engineering System. Its strict Clean Architecture boundaries, mature OCC publication pipeline, and existing LaTeX repair patterns provide the necessary building blocks.

The four formal ADRs (ADR-007 through ADR-010) and the comprehensive implementation plan follow directly from this research. Implementation will be commissioned upon formal maintainer review and approval of these architectural deliverables.
