# Architecture Research Report: Polpo Unified Agentic Document Engineering System

- **Mission Baseline:** `25bb95d198c34c035b5d66513166e06494d82cdb` (main)
- **Release Baseline:** `v0.1.0-rc.2` (strictly preserved)
- **Date:** 2026-11-09 (Revised: 2026-11-11)
- **Status:** Complete / Awaiting Maintainer Approval
- **Authoritative Directives:** D-USER-01 (Unified Assistant Scope), D-USER-02 (Supervised Agent Autonomy), D-USER-03 (Document vs Rendering-System Repair)
- **Architectural Rules:** [`AGENTS.md`](AGENTS.md), [`CONTEXT.md`](CONTEXT.md)

---

## 1. Executive Summary

This report establishes the technical and architectural foundation for the next major capability milestone of Polpo: the **Unified Agentic Document Engineering System**.

Polpo is an embedded, desktop-first, local-first, serverless Document Intelligence & Review Workspace built on PySide6/QML, SQLite WAL, and direct outbound client-to-cloud AI provider connections (BYOK). While Polpo currently possesses specialized AI capabilities — such as isolated LaTeX fragment repair ([ADR-006](docs/adr/2026-11-07-adr-006-ai-assisted-latex-repair.md)), OCR text refinement (Pipeline 2, [ADR-004](docs/adr/2026-10-22-adr-004-pipeline2-artifact-lineage-and-canonical-publication-contract.md)), and technical diagram rendering ([ADR-005](docs/adr/2026-11-03-adr-005-unified-quarto-diagram-preview-and-authoring.md)) — these capabilities operate as disconnected, point-to-point features.

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
 ├── pdf_validator_port.py                     ──> IPdfInspectionEngine (Foundation Contract, Finding B)
 └── diagram_renderer.py / math_renderer.py    ──> IDiagramRenderer, IMathRenderer
Infrastructure Adapters (infrastructure/)
 ├── ai/executor_service.py                    ──> RateLimitedAIExecutor (fallback chain progression)
 ├── pdf/quarto_adapter.py                     ──> Subprocess execution, exit code mapping
 ├── pdf/staging_workspace.py                  ──> Ephemeral staging, asset closure, barrier isolation
 ├── pdf/tex_log_sanitizer.py                  ──> Redacts absolute paths, extracts line numbers
 ├── pdf/mupdf_validator.py                    ──> PyMuPdfValidator (Structural inspection, Finding H)
 └── security/keyring_resolver.py              ──> Hardware-grade OS keyring credential resolution
```

### 2.1 AI Execution & Fallback Infrastructure
- **Component:** [`IAIExecutionService`](application/ports/ai_executor.py) and [`RateLimitedAIExecutor`](infrastructure/ai/executor_service.py).
- **Current Behavior:** Manages an ordered chain of `ApiSlot` configurations (e.g. Gemini 1.5 Pro $\to$ Gemini 1.5 Flash $\to$ OpenAI GPT-4o). Handles centralized rate limiting via `IRateLimiter.wait_if_needed()`, catches standardized `AIError` domain exceptions, and executes automatic fallback chain advancement via [`FallbackChainPolicy.advance_chain()`](core/policies/fallback_policy.py).
- **Reuse Assessment:** The text execution method `execute_text_with_fallback(chain, prompt, input_text, ...)` is completely provider-neutral. It supports structured JSON output schemas via prompt instructions and post-parsing validation. The new Agent Orchestrator can leverage this directly without needing vendor-specific proprietary tool-calling SDKs in the application core.

### 2.2 LaTeX Repair Service & Pre-Transmission Consent
- **Component:** [`LaTeXRepairService`](application/services/latex_repair_service.py) and [`LaTeXRepairConsentDialog.qml`](interfaces/desktop/qml/dialogs/LaTeXRepairConsentDialog.qml).
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
- **Component:** [`QuartoPdfExportService`](application/services/quarto_pdf_export_service.py), [`QuartoPdfAdapter`](infrastructure/pdf/quarto_adapter.py), and [`StagingWorkspaceManager`](infrastructure/pdf/staging_workspace.py).
- **Current Behavior:**
  1. Validates destination path and pre-flight OCC state against `uow.publish_intents` and `uow.document_versions`.
  2. Creates an ephemeral staging workspace (`.pdf_staging/<job_id>_<token>/`).
  3. Injects barrier `_quarto.yml` enforcing `-no-shell-escape`, `-interaction=nonstopmode`, `-halt-on-error`.
  4. Copies `polpo_preamble.tex` and resolves visual asset closure with path containment checks.
  5. Executes Quarto CLI under [`SubprocessSupervisor`](infrastructure/pdf/subprocess_supervisor.py) governance with a 60-second timeout.
  6. On success: copies compiled PDF out of staging and atomically publishes it via [`PdfArtifactPublisher`](application/ports/pdf_export_port.py).
- **Identified Critical Gap:**
  When compilation fails (non-zero return code), `QuartoPdfAdapter.render_pdf()` aggregates stdout/stderr and `.log` files, parses them with `TexLogSanitizer`, and raises `PdfCompilationError`.
  *Critically*, exiting the `with self._staging.create_session(...)` block triggers `shutil.rmtree(staging_dir)` in the `finally` block. Consequently, the intermediate generated `document.tex` and detailed compiler logs are deleted immediately!
  Furthermore, the line number in the TeX error log (e.g. `l.384`) points to `document.tex`, not the user's `document.qmd` or canonical Markdown. Because `document.tex` was destroyed, neither the user nor the system can inspect what was at line 384.

### 2.4 Document Publication & Optimistic Concurrency Control (OCC)
- **Component:** [`DocumentPublicationService`](application/services/document_publication_service.py).
- **Current Behavior:** Sole authoritative gateway for publishing canonical Markdown. Executes a 4-phase crash-consistent commit:
  1. *Intent Reservation:* `PublishIntentRecord` inserted in SQLite under `BEGIN IMMEDIATE`, verifying `base_version == latest_version`.
  2. *Staging & Disk Flush:* Content written to `.tmp` file and fsynced.
  3. *Atomic Activation:* Renamed to canonical filename (`output_{job_id}_v{version}.md`) via `os.replace`.
  4. *Pointer & Version Update:* `DocumentVersionRecord` inserted and `jobs.output_path` updated in SQLite.
- **Architectural Boundary:** The Agent must NEVER write directly to `document_versions` or overwrite canonical Markdown files. All agent-generated document modifications must be validated on an isolated `AgentWorkingCopy` and applied to the active editor buffer or committed through `DocumentPublicationService` under OCC.

---

## 3. External Technical Research & Standards Verification

### 3.1 Persian RTL Strikethrough Investigation: `soul` vs. `ulem` & Pandoc Macro Ownership (Finding A, Finding D)
- **Primary Sources:**
  - TeX Live 2023 CTAN: *The `soul` package: Hyphenation for letterspacing, underlining, and striking out* (HO, v3.1, 2023-06-14)
  - CTAN: *The `ulem` package: Package for underlining and strikethrough* (Donald Arseneau, 2019-11-18)
  - LaTeX Project / Babel Documentation: *Babel bilingual and multilingual documents* (Javier Bezos)
  - Pandoc 3.1.3 Default LaTeX Template (`pandoc -D latex`)
- **Empirical Findings on Host Toolchain (Ubuntu 24.04 LTS, TeX Live 2023, XeLaTeX, Pandoc 3.1.3, Quarto 1.9.38):**
  1. **Pandoc Template Sequence:** In Pandoc 3.1.3's default LaTeX template, lines 293–303 load `\usepackage{soul}` when `strikeout` is true under XeLaTeX. Pandoc emits `\st{...}` for `~~...~~`.
  2. **The Macro Definition Collision Hazard:** Pandoc injects `$for(header-includes)$` at line 380, *after* `\usepackage{soul}`. Because `soul` defines `\st`, any attempt to define `\newcommand{\st}[1]{\sout{#1}}` in `header-includes` or preamble fails with:
     `! LaTeX Error: Command \st already defined.`
     Redefining `\st` safely requires either `\renewcommand{\st}[1]{\sout{#1}}` in a controlled preamble hook (Option A) or intercepting the AST with a Pandoc Lua filter before LaTeX generation so Pandoc never emits `\st` or loads `soul` (Option B).
  3. **Disproved Assumption:** The assumption that `soul` is inherently incompatible with any Persian text was disproved. Under `soul` v3.1, pure Persian text (`\st{متن فارسی}`) without font or language switches compiles with exit code 0!
  4. **Proven Failure Conditions in `soul`:** `soul` crashes with `! Package soul Error: Reconstruction failed.` whenever:
     - An inline language boundary or direction switch occurs within the strikethrough fragment (e.g. `\st{متن English}` or `\st{\foreignlanguage{english}{text}}`).
     - Font changes occur inside strikethrough (e.g. `\st{\textsf{دیگر}}`).
     - Nested formatting macros exist without explicit boxing (`\st{\textbf{پررنگ}}`).
     - Inline math occurs inside (`\st{$x^2$}`).
     - Moving arguments are used (headings `\section{\st{...}}`).
  5. **`ulem` Behavior:** `ulem`'s `\sout{...}` operates at the line/word box level without syllable tokenization, handling language and font switches cleanly. However, `ulem` MUST be loaded with `\usepackage[normalem]{ulem}` to prevent hijacking `\emph`, inline math must be wrapped in `\mbox{$...$}`, and moving arguments require `\protect\sout`.
- **10-Case Reproducible Regression Matrix:**

  | # | Test Scenario | Input LaTeX | `soul` v3.1 Result | `ulem` (2019) Result | Controlled Alternative (Lua Filter / Preamble) |
  |---|---|---|---|---|---|
  | 1 | English strikethrough (ambient Persian) | `\st{English text}` | ❌ Fails under Babel bidi | ✅ Passes (rc=0) | `\sout{English text}` |
  | 2 | Pure Persian strikethrough | `\st{متن فارسی}` | ✅ Passes (rc=0) | ✅ Passes (rc=0) | `\sout{متن فارسی}` |
  | 3 | Mixed Persian & English | `\st{متن فارسی English}` | ❌ Fails (`Reconstruction failed`) | ✅ Passes (rc=0) | `\sout{متن فارسی English}` |
  | 4 | Nested formatting macros | `\st{متن \textbf{پررنگ}}` | ❌ Fails (`Reconstruction failed`) | ✅ Passes (rc=0) | `\sout{متن \textbf{پررنگ}}` |
  | 5 | Font switch inside | `\st{\textsf{فونت دیگر}}` | ❌ Fails (`Reconstruction failed`) | ✅ Passes (rc=0) | `\sout{\textsf{فونت دیگر}}` |
  | 6 | Direction changes | `\st{\foreignlanguage{english}{LTR} متن}` | ❌ Fails (`Reconstruction failed`) | ✅ Passes (rc=0) | `\sout{...}` handles LTR box |
  | 7 | Headings & moving arguments | `\section{\st{عنوان}}`, `\item \st{مورد}` | ❌ Fails in moving argument | ✅ Passes with `\protect\sout` | Robust AST translation |
  | 8 | Inline math inside strikeout | `\st{قبل} $x^2$ \st{بعد}` vs `\st{$x^2$}` | ❌ Fails (`Missing $`) if math inside | ❌ Fails if unboxed | Boxed math: `\sout{\mbox{$x^2$}}` |
  | 9 | Quarto PDF Output | `~~متن نمونه~~` in Quarto QMD | ❌ Fails if mixed/nested | ✅ Passes with `ulem` override | Clean Quarto XeLaTeX PDF |
  | 10 | Exact installed versions | `soul` v3.1 vs `ulem` 2019-11-18 | Syllable reconstruction bug on switch | Stable word-box underline (`[normalem]`) | Pinned toolchain verified |

- **Architectural Policy:** Zero arbitrary AI-generated macro injection into preambles. Document style options exposed strictly as typed tokens (`strikethrough_engine: "ulem"`). Decision `D-PROP-08` remains `PROPOSED — AWAITING EXPLICIT MAINTAINER APPROVAL`.

### 3.2 PDF Quality Inspection & Structural vs. Visual Separation (Finding F, Finding H)
- **Primary Sources:**
  - PyMuPDF Documentation: *Document, Page, and Rect API* ([https://pymupdf.readthedocs.io/](https://pymupdf.readthedocs.io/))
- **Findings:**
  A successful exit code from XeLaTeX (`returncode == 0`) does NOT prove document correctness. Common silent failures include:
  1. Overfull `\hbox` creating text overflowing off the page margin.
  2. Accidental page-count explosion (e.g. unconstrained TikZ diagrams pushing content onto blank trailing pages).
  3. Corrupted font glyphs (missing characters when a font lacks glyph coverage).
  4. Missing vector graphics on diagram pages.
- **Structural vs Visual Separation & Contract Minimization:**
  - Fast structural inspection via PyMuPDF object models (`fitz.open()`, `doc.page_count`, `page.rect`, `page.get_text()`, `page.get_drawings()`) runs in $< 1.5$ seconds for documents under 50 pages on reference hardware.
  - **Vector Drawing Default:** `require_vector_drawings = False` by default. Text-only PDFs must not fail validation. Vector presence is asserted only on explicitly designated `expected_diagram_pages`.
  - **Input Minimization:** The inspection request does NOT accept full `source_markdown: str`. It accepts bounded `expected_text_anchors: List[str]` and structural geometry expectations.
  - **No Quality Guarantee Invariant:** Structural passing (`is_valid = True`) does NOT guarantee visual beauty, mathematical accuracy, Persian RTL text correctness, or semantic fidelity.
  - Optional visual inspection generates 150 DPI raster preview PNGs with strict memory limits (50MB), page sampling (max 5 pages), and a 5.0-second execution timeout.

### 3.3 PySide6 / Qt Concurrency & GUI Responsiveness
- **Primary Sources:**
  - Qt 6 Documentation: *Thread Support in Qt & QThreadPool* ([https://doc.qt.io/qt-6/threads.html](https://doc.qt.io/qt-6/threads.html))
  - PySide6 Documentation: *Signals and Slots Across Threads* ([https://doc.qt.io/qtforpython-6/](https://doc.qt.io/qtforpython-6/))
- **Findings:**
  1. Long-running tasks (compilation, AST analysis, AI requests) must run in `QThreadPool` worker threads.
  2. Worker threads must never touch QML items or QObject properties directly. Communication must occur via Qt Signals with queued connections (`Qt.QueuedConnection`).
  3. Presentation controllers expose thread-safe slots and emit Qt signals that the QML engine consumes reactively.

### 3.4 Provider-Neutral Tool Orchestration vs. Native Tool Calling
- **Investigation:** Should the Agent use provider-native function calling or structured JSON schema planning?
- **Analysis:**
  - *Native Tool Calling:* Vendor-specific APIs differ significantly between Gemini, OpenAI, Anthropic, and local endpoints. Supporting native tool calling requires concrete vendor SDK imports or complex translation adapters in application ports, violating Clean Architecture ([`AGENTS.md`](AGENTS.md) Rule 6.2).
  - *Provider-Neutral JSON Schema Planning:* The existing `RateLimitedAIExecutor` reliably executes text prompts with fallback across all configured providers. Prompts can specify a typed JSON plan/tool schema. The application service parses, validates, and dispatches tool execution deterministically.
- **Decision:** The foundational Agent Orchestrator uses provider-neutral JSON schema orchestration.

---

## 4. Ubiquitous Domain Language & Boundaries (Finding K)

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
6. **`TaskInputSnapshot`:** The immutable baseline capturing active editor draft text, canonical version, and revisions at task creation (Finding C).
7. **`AgentWorkingCopy`:** An isolated, in-memory/scratchpad document snapshot initialized from `TaskInputSnapshot`, where candidate patches, metadata adjustments, and style profiles are tested without touching canonical files.
8. **`DocumentPatch`:** A discrete, targeted modification describing additions, deletions, or replacements with stable contextual anchors.
9. **`DiagnosticBundle`:** A structured snapshot of compiler failure evidence, including sanitized logs, intermediate TeX excerpts, mapped source lines, and preamble fingerprints (Finding G).
10. **`PublicationStyleProfile`:** A versioned, declarative specification of document typography, geometry, colors, and layout separate from desktop application themes (Finding F).
11. **`AgentExecutionJournal`:** An append-only SQLite log recording state transitions, tool invocations, and user decisions under strict allowlist governance (Finding E).

---

## 5. Decision Register

### 5.1 Confirmed Maintainer Directives

| Decision | Selected Option | Evidence | ADR | Tickets | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **D-USER-01** (Unified Assistant Scope) | Single unified desktop AI assistant coordinating all document engineering tools through a shared state machine and tool registry. | Clean Architecture desktop-first rules ([`AGENTS.md`](AGENTS.md)); shared UX, unified settings, and reusable orchestrator. | [ADR-007](docs/adr/2026-11-09-adr-007-unified-agent-orchestration-and-tool-governance.md) | `TICK-ADE-01`, `TICK-ADE-04`, `TICK-ADE-15` | Confirmed (Directive) |
| **D-USER-02** (Supervised Agent Autonomy) | Supervised autonomy: autonomous plan generation and scratchpad experimentation gated by explicit plan approval, pre-transmission consent, high-risk approval, and interactive patch review before editor adoption. | Prevents unconsented data egress and silent canonical commits; ensures author maintains continuous control over document evolution. | [ADR-007](docs/adr/2026-11-09-adr-007-unified-agent-orchestration-and-tool-governance.md) | `TICK-ADE-01`, `TICK-ADE-02`, `TICK-ADE-04`, `TICK-ADE-15` | Confirmed (Directive) |
| **D-USER-03** (Document vs System Repair) | Strict taxonomy separating document markup fixes, YAML metadata choices, publication style tokens, and product defect reports; absolute prohibition on AI macro generation. | Empirical root cause analysis of Pandoc/XeLaTeX preambles; prevents masking product-level template defects with brittle document hacks; protects global preambles against accidental corruption. | [ADR-009](docs/adr/2026-11-09-adr-009-quarto-export-failure-diagnostics-and-ai-pdf-repair.md) | `TICK-ADE-10`, `TICK-ADE-11` | Confirmed (Directive) |

### 5.2 Four Open Architectural Decisions (Awaiting Explicit Maintainer Approval)

All four decisions below remain explicitly unresolved and require formal maintainer sign-off. Neither option has been adopted as an irrevocable architectural fact.

| Decision ID | Decision Statement | Option A | Option B | Recommendation | Evidence | Affected ADR | Affected Tickets | Implementation Consequence | Status | Maintainer Approval |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`D-PROP-01`** | **Tool Invocation Strategy:** How should the orchestrator structure tool execution requests across heterogeneous AI providers? | **Option A:** Provider-neutral structured JSON planning via existing `RateLimitedAIExecutor`. | **Option B:** Provider-native function calling encapsulated inside infrastructure adapters. | **Option A** (Recommended for provider portability & fallback simplicity, but Option B is architecturally viable within infrastructure). | Zero vendor SDK coupling in application layer; works uniformly across Gemini, OpenAI, Anthropic, and custom endpoints without adapter drift. | [ADR-007](docs/adr/2026-11-09-adr-007-unified-agent-orchestration-and-tool-governance.md) | `TICK-ADE-02`, `TICK-ADE-04` | Option A requires JSON schema prompt engineering; Option B requires per-provider function-call mapping in `infrastructure/ai/`. | `PROPOSED — AWAITING EXPLICIT MAINTAINER APPROVAL` | PENDING |
| **`D-PROP-02`** | **Task Persistence Architecture:** Where and how should Agent task state, execution history, and journals be persisted? | **Option A:** Dedicated tables (`agent_tasks`, `agent_task_journal`, `publication_style_profiles`) in canonical `polpot.db` under Migration 006. | **Option B:** Separate SQLite database (`agent_tasks.db`) decoupled from main application database. | **Option A** (Recommended for transactional foreign key integrity with `jobs(id)` and unified backup/migration runner). | Preserves SQLite WAL concurrency; leverages existing `SQLiteMigrationRunner`; enforces `ON DELETE CASCADE` when parent jobs are deleted. | [ADR-007](docs/adr/2026-11-09-adr-007-unified-agent-orchestration-and-tool-governance.md) | `TICK-ADE-01`, `TICK-ADE-03` | Option A integrates with migration 006; Option B requires dual-database connection management and cross-database referential integrity checks. | `PROPOSED — AWAITING EXPLICIT MAINTAINER APPROVAL` | PENDING |
| **`D-PROP-06`** | **Publication Profile Packaging & Storage:** How should standard publication style profiles be stored, versioned, and customized? | **Option A:** Version-controlled built-in JSON assets in `resources/styles/` plus SQLite storage for user-customized overrides. | **Option B:** Database-seeded built-ins inserted directly into SQLite `publication_style_profiles` during migration. | **Option A** (Recommended for immutable git versioning of default styles and clean separation of user customizations). | Built-in profiles cannot be corrupted or accidentally edited; user overrides take precedence by UUID/key; migrations remain simple. | [ADR-010](docs/adr/2026-11-09-adr-010-publication-style-profile-system.md) | `TICK-ADE-13`, `TICK-ADE-14` | Option A requires a file-loader asset resolver in `PublicationStyleService`; Option B requires database seeding logic in migration scripts. | `PROPOSED — AWAITING EXPLICIT MAINTAINER APPROVAL` | PENDING |
| **`D-PROP-08`** | **Persian RTL Strikethrough Engine:** How should strikethrough typography in bilingual Persian/RTL documents be reconciled with Pandoc and XeLaTeX? | **Option A:** Controlled static ULEM engine in `polpo_preamble.tex` with `[normalem]` and `\renewcommand{\st}[1]{\sout{#1}}`. | **Option B:** Controlled Pandoc Lua AST filter (`--lua-filter`) intercepting `Strikeout` AST elements before LaTeX writing. | **Option A or B** (Both are experimentally sound; maintainer must decide between static preamble ownership vs Lua filter asset). | Pandoc 3.1.3 default template emits `\usepackage{soul}` under XeLaTeX. Naive `\newcommand{\st}` fails with `Command \st already defined`. Both options resolve the collision. | [ADR-009](docs/adr/2026-11-09-adr-009-quarto-export-failure-diagnostics-and-ai-pdf-repair.md) | `TICK-ADE-10`, `TICK-ADE-11`, `TICK-ADE-14` | Option A touches static preamble with macro redefinition; Option B introduces a Lua filter script into Quarto export pipeline. | `PROPOSED — AWAITING EXPLICIT MAINTAINER APPROVAL` | PENDING |

### 5.3 Supporting Architecture Consensus Proposals

| Decision ID | Summary | Recommendation & Contract | Affected ADR | Affected Tickets | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`D-PROP-03`** | **Staging Failure Retention:** Extract structured `DiagnosticBundle` before purging ephemeral staging directory. | Bounded capture (100 lines / 10KB + intermediate `document.tex` snippet) stored in ephemeral LRU cache before `finally: shutil.rmtree()`. | [ADR-009](docs/adr/2026-11-09-adr-009-quarto-export-failure-diagnostics-and-ai-pdf-repair.md) | `TICK-ADE-08` | Architectural Consensus |
| **`D-PROP-04`** | **Source Mapping Strategy:** Multi-tier mapping algorithm translating intermediate TeX errors to canonical Markdown coordinates. | 4-tier confidence mapper (`EXACT_TOKEN` $\to$ `CONTEXTUAL_MATCH` $\to$ `SECTION_ANCHOR` $\to$ `UNKNOWN`). Ambiguous `UNKNOWN` halts automated patching fail-closed. | [ADR-009](docs/adr/2026-11-09-adr-009-quarto-export-failure-diagnostics-and-ai-pdf-repair.md) | `TICK-ADE-09`, `TICK-ADE-10` | Architectural Consensus |
| **`D-PROP-05`** | **Working Copy & Snapshot:** Freeze editor draft at task creation and isolate experimental mutations. | `TaskInputSnapshot` captures active buffer text; `AgentWorkingCopy` scratchpad tracks changes; line-based patch engine enforces three-way OCC safety. | [ADR-008](docs/adr/2026-11-09-adr-008-agent-working-copy-patch-and-occ-safety.md) | `TICK-ADE-05`, `TICK-ADE-06`, `TICK-ADE-07` | Architectural Consensus |
| **`D-PROP-07`** | **Inspection Port Ownership:** Decouple `IPdfInspectionEngine` foundational port into `application/ports/` before repair orchestrator. | Places `IPdfInspectionEngine` and DTOs in `application/ports/` (`TICK-ADE-10`); mock doubles in `TICK-ADE-11`; PyMuPDF implementation in `TICK-ADE-12`. | [ADR-009](docs/adr/2026-11-09-adr-009-quarto-export-failure-diagnostics-and-ai-pdf-repair.md) | `TICK-ADE-10`, `TICK-ADE-11`, `TICK-ADE-12` | Architectural Consensus |

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
| **R-07** | **False Sense of Correctness** | Medium | High | Compiler exit code 0 is NOT treated as semantic success; candidate PDFs undergo PyMuPDF structural inspection. |

---

## 7. Conclusion & Next Steps

The existing Polpo architecture is exceptionally well-suited to host the Unified Agentic Document Engineering System. Its strict Clean Architecture boundaries, mature OCC publication pipeline, and existing LaTeX repair patterns provide the necessary building blocks.

The four formal ADRs (ADR-007 through ADR-010) and the comprehensive implementation plan follow directly from this research. Implementation will be commissioned upon formal maintainer review and approval of these architectural deliverables.
