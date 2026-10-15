# ADR-003: Quarto PDF Export Runtime Integration & Product Contract

- **Status:** Accepted
- **Date:** 2026-10-12
- **Scope:** Desktop PDF Export Subsystem (`interfaces/desktop/controllers/export_controller.py`, `application/services/`, `application/ports/`, `infrastructure/pdf/`, `resources/templates/`)
- **Authoritative Baseline:** Commit `38c604a` ([TICK-P05] Eliminate synchronous RichText math pixmap warnings (#96))
- **Related Workstreams:** Phase 10E (Document Publication & Visual Regions), ADR-001 (Three-Way Merge OCC), ADR-002 (Subprocess Reliability), TICK-EXP (Export Package & Portable Markdown)

---

## 1. Context

PolpoT is an embedded, desktop-first, local-first Document Intelligence & Review Workspace. Document review workflows require exporting finalized canonical Markdown documents into publication-grade, vector-quality PDF files.

The document corpus processed by PolpoT presents demanding technical typesetting requirements:
1. **Mathematical Notation:** Dense inline ($...$) and display ($$...$$) mathematical expressions conforming to standard LaTeX syntax.
2. **Engineering & Technical Diagrams:** Vector TikZ/PGF schematics, geometry visualizations, and state machines embedded within the document flow.
3. **Persian & Bidirectional Typesetting:** Native Right-to-Left (RTL) Persian paragraphs, mixed-direction sentences with Latin technical terms, Persian and Western numerals, and appropriate font shaping (e.g. Vazirmatn).
4. **Local Visual-Region Assets:** Automatic inclusion and scaling of bounding-box image crops (`crop_{region_id}.png`) extracted from analyzed PDF documents.
5. **Cross-Platform Parity:** Tier-1 support across Linux (Ubuntu 24.04 reference) and Windows (Windows 10/11 and Windows Server 2025 reference) without platform-specific functional degradation.
6. **Local-First & Offline-Aware Operation:** Compliance with [`AGENTS.md`](../../AGENTS.md) Rules 2 and 3: no remote backend server, no loopback HTTP services, predictable local execution, and no silent background network downloads during ordinary export operations.

Following architectural investigation and product requirements grilling, **Quarto CLI with XeLaTeX-based compilation** was selected as the document renderer. This ADR records the settled product decisions, authoring contracts, preview boundaries, missing-dependency recovery policies, and architectural constraints governing the PDF export workstream.

---

## 2. Problem Statement

How must PDF export be integrated into Polpo Desktop to guarantee:
1. Strict adherence to Clean Architecture without coupling presentation controllers or application services to operating-system subprocesses or concrete external CLI tools?
2. Absolute preservation of canonical Markdown source text, document versions, and Optimistic Concurrency Control (OCC) invariants?
3. Deterministic discovery and capability validation of external Quarto and TeX runtimes across Linux and Windows without machine-specific hardcoded paths?
4. A secure, non-networked ordinary export execution contract that protects the desktop environment against untrusted document inputs, shell escape vulnerabilities, and silent network activity?
5. A graceful, user-authorized recovery model for missing TeX distributions or individual document packages that respects system privilege boundaries and operates cleanly when offline?
6. Preservation of 60 FPS live preview responsiveness in the dual-pane desktop editor without introducing unstable interactive diagram compilation loops?

---

## 3. Settled Product Decisions

### D01 — Decision A: Quarto Distribution Policy
- **Decision:** **External Prerequisite with an In-App Setup & Diagnostics Assistant.**
- **No Bundling in Initial Implementation:** Polpo Desktop will not package or vendor the Quarto distribution (~350–400 MB) or its internal runtime components (Deno, Dart Sass, Pandoc, Lua filters) in the application installer.
- **Automatic Runtime Discovery:** Polpo discovers Quarto dynamically through the system `PATH` and a user-configured executable path in `AppSettings`. Discovery must not depend on fixed, machine-specific installation directories.
- **In-App Setup Guidance:** If Quarto is absent, the application displays a clear Setup & Diagnostics Assistant explaining that PDF export requires Quarto, providing platform-appropriate installation guidance prioritizing official sources (`winget install --id Posit.Quarto` on Windows, official `.deb` / packages on Linux).
- **User-Configured Path Support:** Users can configure an explicit Quarto executable path when standard discovery is insufficient. The application validates the configured executable via capability probing before declaring it ready.
- **Graceful Degradation:** The rest of Polpo remains 100% functional when Quarto is absent. Only PDF export features report the missing prerequisite; application startup is never blocked.
- **No Automatic Quarto Installation:** Polpo must never silently download, install, or update Quarto. Initial Quarto installation remains an explicit user action outside the application process.
- **Cross-Platform & Version-Agnostic:** Linux and Windows are supported from the initial implementation. Compatibility is governed by documented capability checks (minimum Quarto $\ge 1.4.0$) rather than brittle exact-version pins. Bundling may be reconsidered in a future phase only if external installation demonstrates an unacceptable user barrier.

### D02 — Decision B: Canonical TikZ Authoring Contract
- **Decision:** **Standard Pandoc/Quarto Raw LaTeX Fenced Blocks using `{=latex}`.**
- **Canonical Authoring Syntax:** Authors write technical TikZ/PGF diagrams using standard raw LaTeX blocks:
  ````markdown
  ```{=latex}
  \begin{tikzpicture}
    \draw[thick] (0,0) -- (2,1);
  \end{tikzpicture}
  ```
  ````
- **No Proprietary Diagram Syntax:** Polpo will not introduce custom diagram fences (e.g. ```` ```tikz ````), proprietary token markers, or mandatory source-to-source translation layers for TikZ.
- **Canonical Source Preservation:** Authored Markdown remains the authoritative source of truth. PDF export preparation must never rewrite or persist transformed TikZ content into canonical document storage. Export-specific adjustments (such as preamble injection) are confined strictly to the ephemeral export pipeline.
- **Preamble & Directionality Support:** The PDF rendering template automatically injects required LaTeX packages (`tikz`, `pgfplots`) and font configurations. In Persian/RTL documents, diagrams containing Latin labels or standard coordinates require explicit LTR scoping (`\beginL ... \endL` / `\begin{LTR} ... \end{LTR}`) to prevent coordinate or label inversion by the ambient bidirectional engine.
- **Interoperability Scope:** This syntax targets Pandoc/Quarto-compatible toolchains. Outside these toolchains, raw LaTeX blocks remain readable source text and are not expected to render visually in generic Markdown viewers.
- **Security Boundary:** Raw LaTeX is expressive input. The export pipeline must enforce `--no-shell-escape`, but this does not constitute complete sandboxing. The residual security boundaries must be explicitly maintained.

### D03 — Decision C: TikZ Preview Scope
- **Decision:** **PDF-Export-Only in the Initial Implementation.**
- **Export-Only Compilation:** TikZ diagrams are compiled exclusively during explicit PDF export operations. The live editor preview never invokes XeLaTeX, TikZ compilation, or external rendering subprocesses in response to editor changes.
- **Editor & Preview Presentation:**
  * In the **Source Editor (`MarkdownEditorPane`)**, TikZ content remains standard plain text with LaTeX syntax highlighting.
  * In the **Rendered Preview (`MarkdownView`)**, the raw LaTeX block is presented as a clearly distinguishable, styled code/LaTeX container or lightweight explanatory placeholder, utilizing existing preview infrastructure.
- **No Interactive Preview Subsystem:** Polpo will not introduce a local compilation bridge, `dvisvgm` pipeline, background rendering daemon, or separate TikZ preview cache in this workstream.
- **Responsiveness Invariant:** Preserves 60 FPS typing responsiveness and application stability ([`CONTEXT.md`](../../CONTEXT.md) Section 2.9). Incomplete or syntactically invalid TikZ code during interactive typing cannot crash background worker processes or trip circuit breakers. Interactive previewing remains a decoupled future candidate.

### D04 — Decision D: Missing LaTeX Package Policy & Offline Recovery
- **Decision:** **Grouped-Consent Guided Recovery with Offline Diagnostic Fallback.**
- **Strict Non-Silent Ordinary Export:** Ordinary PDF exports strictly disable automatic package downloads (`latex-auto-install: false`). PDF export fails fast if dependencies are missing, with zero unexpected background network calls.
- **Actionable Diagnostics:** When compilation fails due to a missing package or font, Polpo stops and presents an actionable diagnostic extracting the missing `.sty` filename and mapped package name, while sanitizing machine-specific paths from user-facing error messages.
- **Grouped-Consent Recovery for TinyTeX:**
  * When Quarto-managed TinyTeX is active and packages are missing, Polpo offers an explicit **"Install Missing Dependencies"** recovery action.
  * The dialog displays the identified missing dependency set before proceeding.
  * A single user confirmation authorizes installing the identified missing package set for that recovery operation.
  * Installation executes asynchronously off the GUI thread with progress reporting and cancellation support.
  * Polpo revalidates TeX environment readiness post-installation before declaring recovery successful.
  * Consent applies strictly to the identified dependency set; unrelated packages are never installed.
- **Existing System TeX Distributions:**
  * Preserves existing system TeX environments (e.g. Debian/Ubuntu `apt` TeX Live, MiKTeX).
  * Polpo **never** invokes `sudo`, administrative elevation, or operating-system package managers automatically.
  * For OS-managed distributions, Polpo provides verified platform-appropriate manual commands (e.g. `sudo apt install texlive-pictures`).
  * If package management mechanisms cannot be reliably established, Polpo falls back safely to diagnostics and instructions.
- **Offline Recovery Handling:**
  * When installation cannot proceed because the workstation is offline or CTAN is unreachable, Polpo halts cleanly without indefinite retries.
  * The error reports the missing dependency set, explains that network connectivity is required for automated recovery, and provides manual guidance.

---

## 4. Architectural Invariants & Seams

```text
Presentation Layer (interfaces/desktop)
         ↓ Translates UI events to Commands / DTOs
Application Services (application/services)
         ↓ Orchestrates Canonical Source & Asset Closure
Application Ports (application/ports)
         ↓ IPdfExportEngine (Protocol)
Infrastructure Adapters (infrastructure/pdf)
         ↓ Quarto CLI / XeLaTeX Subprocess Execution
Local Filesystem & External Runtimes
```

### 4.1 Canonical Source Authority & OCC
* **Immutable Document Source:** PDF export operates strictly on canonical, versioned on-disk documents (`output_{job_id}_v{version}.md`) validated through `IUnitOfWorkFactory`.
* **Zero UI Scraping:** Export never scrapes, rasterizes, or converts transient QML preview scene graphs.
* **Save-Before-Export Gate:** If the active editor holds unsaved changes or unresolved conflicts, `ExportController` emits `saveBeforeExportRequired`. When confirmed, `MarkdownEditorController` commits a new monotonic version (`v{N+1}`) under Optimistic Concurrency Control (OCC) before export dispatches.
* **Format Preservation:** The canonical Markdown file is never modified in-place or mutated to accommodate PDF generation.

### 4.2 Asset Closure & Path Containment
* **Resolver Reuse:** PDF export reuses [`DocumentAssetResolver`](../../application/services/document_asset_resolver.py) and pure token rewriting in `core/markdown/`.
* **Path Containment:** All local image references (`crop_{region_id}.png`) are strictly verified to reside within `job_artifacts_dir`, preventing directory traversal attacks.
* **Isolated Staging Workspace:** Each export executes in an isolated temporary staging directory (`<artifacts_dir>/.pdf_staging/<job_id>_<token>/`). The staging directory is deterministically purged in a `finally` block on success, failure, timeout, or cancellation.

### 4.3 Clean Architecture & Subprocess Boundaries
* **Application Port:** An abstract interface (`IPdfExportEngine`) in `application/ports/` defines the export contract (`check_readiness()`, `render_pdf()`, `install_packages()`).
* **Infrastructure Encapsulation:** All operating system interactions, binary resolution, subprocess lifecycle management, and TeX log parsing belong exclusively in `infrastructure/pdf/`. Presentation controllers and application services contain zero imports of `subprocess` or OS binary inspection tools.
* **Desktop Concurrency:** PDF compilation and dependency installation execute off the Qt GUI thread using managed worker threads (`ThreadPoolExecutor`).
* **Win32 & POSIX Subprocess Hardening:**
  * Windows processes are spawned with `creationflags=CREATE_NO_WINDOW` (`0x08000000`) to suppress console window flashing.
  * Execution is governed by strict bounded timeouts (default 60s for compilation).
  * Cancellation cooperatively terminates the entire child process tree (`taskkill /F /T` on Windows, process group `SIGKILL` on POSIX).
  * Standard I/O collection is strictly bounded to prevent memory exhaustion from verbose TeX logs.

---

## 5. Security and Network Execution Contract

1. **Ordinary Export Network Isolation:**
   * Ordinary PDF exports enforce `latex-auto-install: false`.
   * *Explicit Limitation:* While `latex-auto-install: false` prevents Quarto from invoking `tlmgr` for missing packages, it does not provide kernel-level network sandboxing against arbitrary external web requests embedded in raw LaTeX macros or Pandoc filters. Absolute network isolation would require OS-level containerization, which is outside desktop application scope.
2. **Restricted TeX Execution:**
   * XeLaTeX is invoked with restricted shell escape:
     ```yaml
     pdf-engine-opt: ["-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error"]
     ```
   * Full shell escape (`\write18`) is strictly prohibited.
   * *Explicit Limitation:* Disabling shell escape prevents external binary execution, but does not provide complete filesystem read sandboxing. TeX can still read accessible files on the host filesystem within TeX's search path.
3. **Disabled Script Evaluation:**
   * Quarto CLI is invoked with `--no-execute`. Embedded Jupyter, Knitr, or Python code blocks are rendered as verbatim syntax-highlighted text and are never evaluated.
4. **Log Sanitization:**
   * Compiler diagnostics, standard error, and TeX log files are parsed by an infrastructure sanitizer. Host-specific absolute paths, system usernames, and internal directory hierarchies are stripped before displaying errors in UI banners or logs.

---

## 6. Consequences & Unresolved Technical Verification Items

### 6.1 Positive Consequences
* **Architectural Consistency:** Reuses existing export controllers, dirty-editor save guards, asset containment logic, and OCC infrastructure without parallel workflows.
* **High-Fidelity Output:** Fully enables publication-grade PDF documents with native vector TikZ diagrams, complex mathematics, and Persian RTL text.
* **Lightweight Application:** Keeps Polpo Desktop installation small while supporting user-managed TeX Live and automated TinyTeX workflows.
* **User Trust & Predictability:** Users retain absolute control over external package downloads, privilege boundaries, and runtime installations.

### 6.2 Accepted Trade-offs
* **Initial Setup Friction:** First-time users without Quarto or TeX must complete setup steps guided by the Setup Assistant.
* **No Live Diagram Preview:** TikZ diagrams are rendered exclusively at export time; in-editor preview displays formatted source blocks.
* **Fail-Fast on Missing Packages:** Documents referencing uninstalled packages fail export fast and require an explicit recovery step.

### 6.3 Technical Items Reserved for `/codebase-design`
The following technical details are explicitly identified as design and implementation tasks for `/codebase-design`:
1. **Concrete Port & DTO Signatures:** Exact dataclass shapes for `PdfExportRequest`, `PdfExportResult`, `EngineReadinessDTO`, and `PackageRecoveryRequest`.
2. **Subprocess Adapter Architecture:** Concrete structure of `QuartoPdfAdapter`, `QuartoBinaryResolver`, and `TinyTexManager`.
3. **TeX Log Sanitizer Implementation:** Concrete regex parsing for extracting missing `.sty` files, font errors, and line numbers from multi-megabyte TeX log streams.
4. **Package Name Mapping:** Mapping heuristics from missing `.sty` filename stems to installable TeX Live / CTAN package identifiers.
5. **Windows Shim Resolution:** Verifying reliable discovery of `quarto.cmd` vs `quarto.exe` on Windows without console flashing.
6. **Integration Test Suite:** Designing integration test fixtures that exercise real Quarto/XeLaTeX compilations when installed, and cleanly mock execution on CI runners lacking TeX.

---
*ADR-003 complete. Recorded as authoritative durable documentation.*
