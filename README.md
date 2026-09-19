# PolpoT — Desktop Document Intelligence & Review Workspace

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

## 3. Installation & Setup

### Prerequisites
* Python 3.10+
* Virtual Environment

### Install Dependencies
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

---

## 4. Quickstart & Launch

To launch the desktop application:

```bash
python -m interfaces.desktop.app
```

---

## 5. Testing & Verification

Run the automated test suite:

```bash
# Run all automated tests
pytest -q

# Run architectural boundary isolation tests
pytest tests/unit/test_architecture_boundaries.py -v
```

---

## 6. Repository Structure

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

## 7. Frozen Legacy Telegram Transport

> [!NOTE]
> Telegram is a **frozen legacy transport adapter** retained strictly for backward compatibility. The desktop runtime operates completely local-first and does not require MySQL, cPanel, or remote server infrastructure.
>
> Historical server-based deployment documentation for the Telegram transport has been archived under [`docs/plans/historical/`](docs/plans/historical/).
