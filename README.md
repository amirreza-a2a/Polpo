# Polpo — Desktop Document Intelligence & Review Workspace

[![CI](https://github.com/amirreza-a2a/Polpo/actions/workflows/ci.yml/badge.svg)](https://github.com/amirreza-a2a/Polpo/actions/workflows/ci.yml)
[![Periodic & Pre-Release Validation](https://github.com/amirreza-a2a/Polpo/actions/workflows/periodic-validation.yml/badge.svg)](https://github.com/amirreza-a2a/Polpo/actions/workflows/periodic-validation.yml)

**Polpo** is a desktop-first, local-first application for intelligent PDF transcription, optical character recognition (OCR), interactive visual-region extraction, AI-powered Markdown conversion, synchronized document review, and publication-oriented document export.

Built with **PySide6, QML, and Python**, Polpo follows a layered Clean Architecture. Application processing, document persistence, review workflows, and export orchestration run locally on the user's workstation without requiring a remote Polpo backend, web framework, or loopback HTTP listener.

AI-powered operations communicate directly with configured providers over HTTPS using Bring Your Own Key (BYOK). PDF publication uses Quarto and XeLaTeX when the required external tools are available.

---

## 1. Core Capabilities

### Dual-Pane Review and Editing Workspace

- **Synchronized PDF and Markdown workspace:** Review PDF pages alongside a segmented Markdown preview and editable source document.
- **Bidirectional scroll and caret synchronization:** The `ReviewWorkspaceSyncCoordinator` and `CaretOffsetTranslator` coordinate normalized scroll positions and source/preview navigation while preventing feedback loops.
- **Native Markdown editor:** Plain-text editing with theme-aware syntax highlighting, undo/redo, regular-expression search and replacement, dirty-buffer previewing, and optimistic concurrency control (OCC).
- **Format-preserving three-way merge:** `MarkdownMergeService` reconciles concurrent human edits and pipeline updates. Conflict sessions expose structured conflict information and an interactive resolution interface with local, remote, and combined resolution choices.

### Visual Region Extraction and Crash-Consistent Publication

- **Interactive bounding-box editor:** Draw, resize, and inspect visual regions over PDF pages. Normalized coordinates are mapped to PDF point space, and image crops are generated from the source document using PyMuPDF.
- **Region context menu:** `RegionActionProvider` and `RegionContextMenu.qml` expose contextual actions for Markdown insertion, clipboard operations, review, and diagnostics.
- **Lossless visual-token mutation:** `VisualTokenMutator` modifies canonical Markdown image tokens without reserializing the entire Markdown document through an AST.
- **Crash-consistent document publication:** `DocumentPublicationService` coordinates publication intents, isolated crop staging, filesystem synchronization, atomic file replacement, SHA-256 integrity verification, and startup recovery for interrupted publication operations.

### Authoritative Pandoc Parsing and Headless MathJax Rendering

- **Pandoc-based Markdown parsing:** `PandocParser` and `PandocRunner` convert Markdown into a read-only core AST with source-position information. Supported extensions include TeX mathematics, raw TeX, pipe tables, strikeout, task lists, and source positions.
- **Legacy syntax normalization:** The Markdown normalization layer handles supported legacy wiki-link syntax while preserving source-coordinate information.
- **Supervised MathJax rendering:** A dedicated Node.js worker runs `mathjax-full` and communicates with the desktop application through line-delimited JSON-RPC 2.0 messages.
- **Self-contained mathematical SVGs:** The rendering pipeline generates vector SVG output with embedded glyph paths, avoiding runtime font requests for rendered formulas.
- **Reliability controls:** Startup and request timeouts, process termination, circuit-breaker behavior, failure memoization, and an LRU cache help isolate problematic formulas and renderer failures.
- **Deterministic error fallback:** Failed formulas produce structured error delegates with diagnostic information and raw TeX copying rather than silently disappearing from the preview.

### Publication-Oriented Export and Theme System

- **Quarto and XeLaTeX PDF export:** `QuartoPdfExportService` and `QuartoPdfAdapter` compile canonical Markdown versions into PDF documents through isolated staging workspaces.
- **Persian and bidirectional typesetting:** The LaTeX preamble supports Persian right-to-left text, mixed Persian/English content, mathematical notation, and font shaping when the required fonts and TeX packages are available.
- **Vector diagrams:** Supported raw-LaTeX blocks can contain TikZ, PGFPlots, and CircuiTikZ diagrams, subject to the configured TeX toolchain and document template.
- **Export diagnostics:** Runtime discovery and diagnostic sanitization help report missing dependencies and compiler failures without unnecessarily exposing sensitive information.
- **PDF setup assistance:** The desktop interface provides runtime readiness checks and guided recovery for supported Quarto and TeX environments.
- **Portable Markdown and ZIP export:** `ExportPackageService` creates Markdown documents and ZIP packages containing document content, assets, and a manifest with SHA-256 checksums.
- **Theme-aware interface:** `ThemeController` manages `system`, `dark`, and `light` modes using semantic design tokens, with coordinated theme updates across QML views, dialogs, syntax highlighting, and mathematical SVG rendering.

---

## 2. Architectural Highlights

### Desktop-First and Local-First

Polpo's primary runtime is the native desktop application. It does not require a remotely hosted application backend, FastAPI, Uvicorn, or a local HTTP server.

The desktop runtime is independent of the legacy Telegram adapter and its server-oriented dependencies.

### Clean Architecture

The project separates presentation, application orchestration, domain logic, and infrastructure:

- `interfaces/desktop/` contains the PySide6/QML user interface, controllers, models, coordinators, and desktop-specific actions.
- `application/` contains use cases, services, DTOs, events, and abstract ports.
- `core/` contains domain entities, value objects, AST representations, geometry, and policies.
- `infrastructure/` contains concrete adapters for persistence, credentials, AI providers, document processing, parsing, mathematical rendering, and PDF export.

Dependencies are intended to point inward. Domain and application code should not depend directly on Qt, SQLite, operating-system keyrings, subprocess implementations, or vendor-specific AI SDKs.

### Direct AI Provider Communication

Polpo supports configured AI providers, including Google Gemini, OpenAI, OpenRouter, and compatible custom endpoints where supported by the provider adapters.

API requests originate from the user's workstation. Provider access requires the appropriate network connectivity, valid credentials, and compliance with the selected provider's policies and rate limits.

### Credential Protection

Credentials are referenced through application-level handles and stored using the operating system's credential facilities where supported.

The credential subsystem includes an encrypted-file fallback. Its security depends on the configured vault implementation, key-management behavior, and the user's environment. Raw credentials must not be written to SQLite records or application logs.

### SQLite Persistence and Recovery

Local application state uses SQLite with Write-Ahead Logging (WAL), foreign-key enforcement, transactional updates, and worker-local units of work.

Job claiming and other concurrency-sensitive operations use explicit transaction boundaries. Document publication uses staged artifacts and recovery metadata to handle interrupted writes.

### Background Execution and Process Supervision

Long-running document processing, AI operations, parsing, and export tasks are coordinated outside the GUI thread.

External helpers such as Pandoc, the Node.js MathJax worker, Quarto, and XeLaTeX are managed through explicit runtime-discovery and process-supervision boundaries. Platform-specific behavior must be validated on the platforms claimed as supported.

### Architectural Overview

```text
PySide6 / QML Desktop Views and Dialogs
                  |
                  v
Desktop Controllers, Coordinators, Actions and ViewModels
                  |
                  v
Application Services and Use Cases
                  |
                  v
Application Ports and Interfaces
                  |
                  v
Core Domain Entities, Value Objects, AST and Policies
                  ^
                  |
Infrastructure Adapters
(SQLite, OS Keyring, PyMuPDF, Pandoc, MathJax,
AI Providers, Quarto and XeLaTeX)
```

Infrastructure adapters implement application ports. The core domain and application layers should remain independent of concrete infrastructure implementations.

---

## 3. Installation and Developer Setup

This section describes a **development checkout**, not a standalone end-user installation package.

### Prerequisites

| Component | Requirement | Purpose |
|---|---|---|
| Python | 3.10+; Python 3.12 is the primary CI version | Application runtime and PySide6 |
| Pandoc | Project-pinned version; currently documented as 3.1.11 | Markdown parsing and source-position extraction |
| Node.js | Project-pinned version; currently documented as 22.23.2 | Headless MathJax worker |
| npm | Compatible with the selected Node.js distribution | Installing worker dependencies |
| Quarto CLI | Optional; version 1.4.0 or later is the documented baseline | PDF export |
| XeLaTeX distribution | TinyTeX, TeX Live, or MiKTeX as appropriate | PDF typesetting |

Check the repository's runtime manifest, lockfiles, and packaging documentation before changing dependency versions.

### 1. Create a Virtual Environment

From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Install the project dependencies:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

If the project dependency manifests change, use the dependency installation process documented by the repository maintainers.

### 2. Install MathJax Worker Dependencies

For development checkouts, install the pinned Node.js dependencies in the worker directory:

```bash
npm ci --prefix resources/mathjax
```

Use the repository's supported Node.js version. A successful npm installation does not by itself establish that a frozen or packaged application can discover the worker and all required runtime assets.

### 3. Runtime Binary Discovery

Polpo's runtime discovery layer resolves external executables through configured resource paths, explicit user or environment overrides, and system `PATH` lookup, according to the applicable runtime contract.

Relevant components include:

- `infrastructure/paths.py`
- `resources/runtimes.json`
- `resources/mathjax/`
- `AppSettings` and the relevant runtime-discovery services

The precise behavior depends on the executable and whether the application is running from a development checkout or a frozen distribution.

#### Environment Variables

The following variables are relevant to runtime configuration where supported by the corresponding implementation:

| Variable | Purpose |
|---|---|
| `POLPO_NODE_PATH` | Explicit Node.js executable path |
| `POLPO_MATHJAX_STARTUP_TIMEOUT` | MathJax worker startup/readiness timeout |
| `POLPO_MATHJAX_REQUEST_TIMEOUT` | MathJax request timeout |
| `XDG_DATA_HOME` | Linux base directory for user-specific application data when honored by the runtime |

The documented MathJax defaults are a 15-second startup timeout and a 5-second per-request timeout. Confirm the current implementation before relying on these values for operational configuration.

### Linux Runtime Libraries

On Ubuntu or another Debian-based distribution, the following packages may be required by Qt or the system graphics stack:

```bash
sudo apt-get update
sudo apt-get install -y \
  libgl1 \
  libegl1 \
  libxkbcommon-x11-0 \
  libdbus-1-3
```

The exact runtime libraries depend on the Qt modules, display environment, and system installation. Additional dependencies may be needed on minimal or headless systems.

### 4. Launch the Desktop Application

From the repository root with the virtual environment activated:

```bash
python -m interfaces.desktop.app
```

The application initializes its persistence and recovery services during startup. Depending on the database state, this can include schema migrations, compatibility backfills, interrupted-publication recovery, stale-job reconciliation, and schedule recovery.

---

## 4. Testing and Quality Gates

Polpo uses automated tests and repository-specific quality gates to validate application behavior, architectural boundaries, security invariants, and platform-specific contracts.

### Run the Test Suite

```bash
pytest -q
```

For headless Qt/QML tests:

```bash
QT_QPA_PLATFORM=offscreen pytest -q
```

### Run Canonical Quality Gates

```bash
python scripts/run_quality_gates.py --mode=working-tree
```

This runner performs the repository's configured quality checks. Follow the current CI workflow and `AGENTS.md` for the canonical validation procedure.

### Verify the MathJax Worker

```bash
python scripts/verify_mathjax_worker.py
```

This command verifies the standalone worker contract to the extent implemented by the script. It does not replace application-level integration tests or packaged-application smoke tests.

### Run Architectural and Security Tests

```bash
pytest \
  tests/unit/test_architecture_boundaries.py \
  tests/unit/test_architecture_security.py \
  -v
```

Run the full canonical CI procedure before proposing a merge. Individual test commands are useful for diagnosis but are not necessarily equivalent to the complete CI workflow.

### CI Workflows

- [Continuous Integration](.github/workflows/ci.yml)
- [Periodic and Pre-Release Validation](.github/workflows/periodic-validation.yml)

CI validation is distinct from packaging validation. A successful unit-test run does not, by itself, prove that a distributable application installs and launches correctly on a clean machine.

---

## 5. Platform Support Policy

Polpo distinguishes continuous CI coverage from periodic validation and best-effort compatibility.

The following classifications describe the project's intended validation tiers. They must not be interpreted as proof that standalone installer packages are available for every listed platform.

| Tier | Classification | Target environments | Verification scope |
|---|---|---|---|
| Tier 1 | Continuously tested | Ubuntu 24.04 LTS x86_64, Python 3.12; Windows Server 2025 x64, Python 3.12 | Blocking CI checks for the configured quality gates, tests, and security checks |
| Tier 2 | Periodically validated | macOS 15 Apple Silicon/ARM64, Python 3.12; Python 3.10 and 3.11 on the configured Ubuntu runner | Periodic, manual, and configured pre-release validation |
| Tier 3 | Best effort | Other modern glibc-based Linux distributions, WSL2, selected older macOS environments, and supported headless configurations | Compatibility depends on system libraries, display configuration, keyring availability, and runtime dependencies |
| Tier 4 | Unsupported | Musl-based Linux distributions such as Alpine, 32-bit systems, Windows versions below 10, macOS versions below 12, and remote server deployments | Outside the intended support boundary |

### Important Limitations

- A Windows Server CI runner does not constitute complete interactive verification on Windows 10 or Windows 11 desktop systems.
- Passing CI on Ubuntu does not prove compatibility with every Linux distribution or desktop environment.
- A macOS test runner does not automatically prove that a signed, notarized, installable macOS application is available.
- Headless testing does not replace manual verification of window behavior, display scaling, accessibility, and interactive dialogs.
- The availability of Quarto, XeLaTeX, system keyrings, and other optional external components affects feature availability.

The release documentation should state which operating systems have actually been tested with the corresponding distributable artifacts.

---

## 6. Repository Structure

The following is a high-level map of the principal project areas.

```text
Polpo/
├── core/
│   ├── ai/
│   ├── domain/
│   ├── entities/
│   ├── exceptions/
│   ├── geometry/
│   ├── markdown/
│   └── policies/
├── application/
│   ├── dto/
│   ├── ports/
│   ├── services/
│   └── events.py
├── infrastructure/
│   ├── ai/
│   ├── document/
│   ├── events/
│   ├── logging/
│   ├── markdown/
│   ├── math/
│   ├── pdf/
│   ├── persistence/
│   ├── rate_limiting/
│   ├── security/
│   ├── storage/
│   └── paths.py
├── interfaces/
│   ├── desktop/
│   │   ├── actions/
│   │   ├── controllers/
│   │   ├── coordinators/
│   │   ├── models/
│   │   ├── providers/
│   │   ├── qml/
│   │   ├── syntax/
│   │   ├── workers/
│   │   ├── app.py
│   │   ├── bridge.py
│   │   ├── composition.py
│   │   └── theme.py
│   └── telegram/
├── prompts/
├── resources/
│   ├── mathjax/
│   ├── templates/
│   └── runtimes.json
├── scripts/
├── docs/
│   ├── adr/
│   └── plans/
├── tests/
├── requirements.txt
├── requirements-dev.txt
├── AGENTS.md
├── CONTEXT.md
└── README.md
```

Some historical files and modules may remain for compatibility. Consult the current source tree rather than assuming every example above represents a public API or a supported runtime entry point.

---

## 7. Architecture Decision Records and Documentation

The following documents describe important architectural and operational decisions:

- [AGENTS.md](AGENTS.md) — Development rules, architectural boundaries, security requirements, and implementation constraints.
- [CONTEXT.md](CONTEXT.md) — Domain terminology, entity invariants, and architectural context.
- [ADR-001: Three-Way Merge and Conflict Resolution](docs/adr/2026-09-18-adr-001-phase10f5-three-way-merge-conflict-resolution.md)
- [ADR-002: MathJax Supervisor Reliability](docs/adr/2026-09-30-adr-002-mathjax-supervisor-reliability-contract.md)
- [ADR-003: Quarto PDF Export Integration](docs/adr/2026-10-12-adr-003-quarto-pdf-export-integration.md)
- [Packaging Guide](docs/packaging.md) — Runtime bundling, binary discovery, and platform-specific signing and notarization considerations.
- [CI Repository Rules](docs/ci-repository-rules.md) — Repository CI and merge-gating guidance.
- [Prompt Inventory](prompts/README.md) — Reusable English and Persian STEM transcription and diagram-reconstruction prompt templates.

These links assume the corresponding files exist at the listed paths on the current branch. Verify the paths if documentation is reorganized.

---

## 8. Legacy Telegram Transport

The legacy Telegram adapter is retained for backward compatibility. It is separate from the primary desktop application runtime.

The desktop application is intended to operate without Telegram, MySQL, or a remote application server.

Historical server-oriented deployment notes are retained under [`docs/plans/historical/`](docs/plans/historical/) where applicable. They do not define the deployment architecture of the desktop application.

---

## 9. Security and Privacy

- **Local persistence:** Application job state and related local data are stored on the user's workstation.
- **Credential handling:** Configured AI credentials are managed through the credential subsystem. Secrets must not be committed to source control, stored as plaintext in SQLite, or exposed in logs.
- **Direct provider requests:** AI processing transmits the relevant request content to the provider configured by the user. Local-first architecture does not mean that AI input is never sent outside the machine.
- **External executables:** Pandoc, Node.js, Quarto, and XeLaTeX are separate runtime components whose versions and installation sources should be controlled.
- **Document processing:** Input documents and generated artifacts should be treated as potentially sensitive. Users should protect local storage and review the privacy policies of any configured AI provider.
- **Release integrity:** Distributable builds should be accompanied by appropriate integrity metadata and release documentation when those artifacts become available.

Never include API keys, access tokens, private documents, or other secrets in bug reports or diagnostic logs.

---

## 10. Release Status and Packaging Architecture

Polpo provides standalone, hermetic desktop distributions built with PyInstaller, containing all required runtime dependencies (statically linked Pandoc 3.1.11, headless Node.js 22.23.2 MathJax daemon, schema migrations, and prompt templates). End users do not need to install Python, Pandoc, or Node.js separately.

### 10.1 Supported Platform Tiers

* **Tier-1 Packaged Distributions (Automated CI & Releases):**
  * **Linux (x86_64):** Standalone archive (`Polpo-v<version>-linux-x86_64.tar.gz`) verified on Ubuntu 24.04+. Requires standard desktop GUI libraries (`libgl1`, `libegl1`, `libxkbcommon-x11-0`, `libdbus-1-3`).
  * **Windows (x64):** Standalone archive (`Polpo-v<version>-windows-x64.zip`) verified on Windows Server 2025 and Windows 10/11 x64.
* **Tier-2 Source-Validated Platforms (Periodic CI & Manual Packaging):**
  * **macOS (Apple Silicon / ARM64 & Intel / x86_64):** Validated on native `macos-15` runners with Python 3.12. Standalone `.app` bundles require inside-out code signing and Apple notarization per [`docs/packaging.md`](docs/packaging.md).
  * **Python Matrix:** Tested across Python 3.10, 3.11, and 3.12 on Linux.

### 10.2 Binary Verification and Frozen Self-Test

Every official release artifact can be verified before installation:

1. **Integrity Check:** Compare against the published `SHA256SUMS.txt` sidecar:
   ```bash
   sha256sum -c Polpo-v*-linux-x86_64.tar.gz.sha256
   ```
2. **Provenance Attestation:** Official release assets are cryptographically attested using GitHub Actions artifact attestations (`actions/attest-build-provenance`):
   ```bash
   gh attestation verify Polpo-v*-linux-x86_64.tar.gz --owner amirreza-a2a
   ```
3. **Hermetic Self-Test:** Run the bundled binary in diagnostic mode to verify runtime integrity, SQLite migrations, QML engine, and MathJax vector rendering:
   ```bash
   # Linux
   ./Polpo/Polpo --self-test

   # Windows
   .\Polpo\Polpo.exe --self-test
   ```

Check the repository's [GitHub Actions](https://github.com/amirreza-a2a/Polpo/actions) and [GitHub Releases](https://github.com/amirreza-a2a/Polpo/releases) for official downloads and validation status.

---

## License

Polpo is licensed under the **GNU Affero General Public License v3** (`AGPL-3.0-only`). See [LICENSE](LICENSE) for terms and third-party notices.
