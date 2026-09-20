# PolpoT — Desktop Document Intelligence & Review Workspace

[![CI](https://github.com/amirreza-a2a/Polpo/actions/workflows/ci.yml/badge.svg)](https://github.com/amirreza-a2a/Polpo/actions/workflows/ci.yml)
[![Periodic & Pre-Release Validation](https://github.com/amirreza-a2a/Polpo/actions/workflows/periodic-validation.yml/badge.svg)](https://github.com/amirreza-a2a/Polpo/actions/workflows/periodic-validation.yml)

PolpoT is a **desktop-first, local-first, serverless embedded application** for intelligent document transcription, OCR, visual region extraction, and AI-powered Markdown conversion.

---

## 1. Core Capabilities

* **Synchronized Dual-Pane Review Workspace:** Side-by-side PDF document rendering and segmented Markdown preview for visual inspection and immediate verification.
* **Continuous Normalized Scroll Synchronization:** High-precision bidirectional scroll sync throttled at 16ms with directional origin locking to prevent feedback loops and scroll jitter.
* **Interactive Visual Region Bounding Box Editor:** On-canvas visual region selection, interactive resizing handles, and automatic region recropping against original high-resolution page rasters.
* **Native Markdown Source Editor:** Source editing with syntax highlighting, undo/redo stack, regex search and replace, and Optimistic Concurrency Control (OCC) revision tracking.
* **Format-Preserving Three-Way Merge (`diff3`):** High-fidelity merge engine capable of reconciling concurrent human edits and background AI stream updates with an in-buffer conflict resolution toolbar.

---

## 2. Architectural Highlights

* **Desktop-First, Local-First, Serverless:** Built with **PySide6 / QML** and embedded Python application services. Zero FastAPI, zero uvicorn, zero remote backend servers, and zero loopback HTTP listeners (`localhost:8000`).
* **Direct Outbound AI Communications:** Outbound HTTPS requests connect directly from the user workstation to configured AI providers (Google Gemini, OpenAI, OpenRouter, and custom endpoints) using Bring-Your-Own-Key (BYOK).
* **Secure Keyring Storage:** API credentials are encrypted and stored in the **OS Keyring** (Windows Credential Manager, macOS Keychain, Linux Secret Service) with an authenticated Fernet PBKDF2 vault fallback. Raw secrets never touch the database or log streams.
* **Persistent SQLite WAL Persistence:** Local job history, queue state, settings, and schedules survive crashes and restarts via SQLite with Write-Ahead Logging (WAL) and atomic transactions.
* **Robust Background Concurrency:** Document parsing, PyMuPDF page rendering, and AI provider calls run asynchronously on background threads via `QThreadPool` and `DesktopJobRuntime` with cooperative pause, resume, and cancellation boundaries.

```text
PySide6 / QML Desktop Views
        ↓
Desktop Controllers, Coordinators & ViewModels
        ↓
Application Services (Submission, Execution, Recovery, Settings, Prompts)
        ↓
Application Ports (UnitOfWork, CredentialResolver, Storage, AIProvider, DocumentProcessor)
        ↓
Infrastructure Adapters
        ↓
Local SQLite (WAL) / OS Keyring / Local Artifact Storage / AI Provider SDKs / PyMuPDF
```

---

## 3. Installation & Developer Setup

### Prerequisites
* Python 3.10+
* Virtual Environment

### Install Dependencies
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install production dependencies
pip install -r requirements.txt

# Install development and test tooling
pip install -r requirements-dev.txt
```

---

## 4. Quickstart & Launch

To launch the desktop application:

```bash
python -m interfaces.desktop.app
```

---

## 5. Testing & Verification

Run the automated test suite and canonical quality gates:

```bash
# Run all automated tests
pytest -q

# Run canonical quality gates runner (whitespace, bytecode compilation, AST invariants)
python scripts/run_quality_gates.py --mode=working-tree

# Run architectural boundary isolation tests
pytest tests/unit/test_architecture_boundaries.py -v
```

---

## 6. Platform Support Policy

PolpoT follows a four-tier platform support model defining continuous testing and compatibility expectations:

### Tier 1 — Continuously Tested / Merge-Blocking
* **Ubuntu 24.04 LTS x86_64** (Python 3.12)
* **Windows Server 2025 x64** (GitHub Actions runner: `windows-2025`, Python 3.12)

> [!NOTE]
> Windows CI executes on GitHub-hosted `windows-2025` runners, verifying Windows OS family contracts, Win32 kernel semantics, NTFS file locking, and file URI path normalization. This validates headless backend and service execution, but does not by itself constitute interactive consumer Windows 10/11 desktop-shell or display hardware verification. Windows Server 2022 remains a non-continuously-tested compatibility target.

### Tier 2 — Supported / Periodically Verified
* **macOS 15 Apple Silicon / ARM64** (Python 3.12, GitHub Actions runner: `macos-15`, verified weekly and prior to releases via the `Periodic & Pre-Release Validation` workflow)
* **Python 3.10 & 3.11** on the Ubuntu 24.04 reference runner
* **Modern glibc Linux distributions** (Fedora 38+, Debian 12+, Arch Linux) supported via standard binary ABI and POSIX compliance (not continuously tested in this phase)

> [!NOTE]
> Ubuntu 24.04 serves as the project's Tier-1 POSIX reference distribution and does not imply continuous automated testing across every individual Linux distribution.

### Tier 3 — Best Effort
* macOS 12 & 13, legacy Intel x86_64 Mac hardware
* Windows Subsystem for Linux (WSL2)
* Headless Linux without D-Bus (supported using `EncryptedFileCredentialStore` encrypted vault fallback)

### Tier 4 — Unsupported
* Musl-based Linux distributions (e.g. Alpine Linux)
* 32-bit operating systems
* Windows < 10
* macOS < 12
* Remote web/cloud server environments (PolpoT is strictly local-first and desktop-embedded)

---

## 7. Repository Structure

```text
PolpoT/
├── core/                           ← Pure domain models, policies & geometry
│   ├── entities/                   ← Job, ApiSlot, AppSettings, Prompt, Artifact
│   ├── geometry/                   ← BoundingBox, CropPolicy, coordinate normalization
│   ├── markdown/                   ← Segmentation, AST tokens, Diff3 merge algorithms
│   ├── policies/                   ← JobStateTransitionPolicy, RetryPolicy, FallbackPolicy
│   └── exceptions/                 ← Domain exceptions
├── application/                    ← Use cases, DTOs, events, and ports
│   ├── dto/                        ← Typed command and query DTOs
│   ├── events.py                   ← Transport-neutral application events
│   ├── ports/                      ← Abstract interfaces (UoW, Storage, AIProvider, etc.)
│   └── services/                   ← JobExecution, Recovery, Submission, ApiKey, Settings
├── infrastructure/                 ← Concrete infrastructure implementations
│   ├── persistence/sqlite/         ← SQLite connection, WAL migrations, repositories
│   ├── security/                   ← KeyringCredentialResolver, EncryptedFileCredentialStore
│   ├── ai/                         ← Google & OpenAI adapters, RateLimitedAIExecutor
│   ├── document/                   ← PyMuPDFDocumentProcessor (fitz, rasterization, crops)
│   ├── markdown/                   ← Diff3MergeEngine, structural unification
│   ├── storage/                    ← LocalStorageAdapter (Artifact and PDF ingestion)
│   ├── notifier/                   ← EventNotifier
│   ├── rate_limiting/              ← RateLimiterAdapter
│   ├── logging/                    ← Structured logging & secret redaction
│   └── events/                     ← InMemoryEventBus
├── interfaces/
│   ├── desktop/                    ← Canonical Desktop Presentation Layer (PySide6 / QML)
│   │   ├── app.py                  ← Desktop application entrypoint (create_app)
│   │   ├── composition.py          ← DesktopAppContainer (Composition Root)
│   │   ├── bridge.py               ← QtSignalEventBridge (EventBus → Qt Signals)
│   │   ├── controllers/            ← JobController, ApiKeyController, MarkdownEditorController
│   │   ├── coordinators/           ← ReviewWorkspaceSyncCoordinator, scroll sync & viewport
│   │   ├── models/                 ← JobQueueModel, JobHistoryModel, ApiSlotModel, etc.
│   │   ├── syntax/                 ← MarkdownHighlighter, search match formatters
│   │   ├── workers/                ← DesktopJobRuntime, JobWorkerRunnable, Scheduler
│   │   └── qml/                    ← QML user interface views and components
│   └── telegram/                   ← Frozen legacy transport adapter
└── tests/                          ← Unit, invariant, security, and lifecycle tests
```

---

## 8. Frozen Legacy Telegram Transport

> [!NOTE]
> Telegram is a **frozen legacy transport adapter** retained strictly for backward compatibility. The desktop runtime operates completely local-first and does not require MySQL, cPanel, or remote server infrastructure.
>
> Historical server-based deployment documentation for the Telegram transport has been archived under [`docs/plans/historical/`](docs/plans/historical/).
