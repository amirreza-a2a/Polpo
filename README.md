# PolpoT — Desktop Document Intelligence

PolpoT is a **desktop-first, local-first, serverless embedded application** for intelligent document transcription, OCR, image extraction, and AI-powered Markdown conversion.

---

## 1. Architectural Principles

* **Desktop-First & Local-First:** Built with **PySide6 / QML** and embedded Python application services.
* **Zero Server Dependency:** Runs entirely on the user's machine without FastAPI, uvicorn, remote backend servers, JWT authentication, or loopback HTTP listeners.
* **Direct Outbound AI Communications:** Outbound HTTPS requests directly connect to configured AI providers (Google Gemini, OpenAI, OpenRouter, compatible custom endpoints) using user-owned API keys (BYOK).
* **Secure Credential Storage:** API keys are secured via the **OS Keyring** (Windows Credential Manager / macOS Keychain / Linux Secret Service) with an authenticated Fernet PBKDF2 encrypted file vault fallback (`EncryptedFileCredentialStore`). Raw secrets never touch the database.
* **Persistent SQLite WAL Persistence:** Local history, queue states, settings, and scheduling survive restarts and app crashes via SQLite with Write-Ahead Logging (WAL) and atomic transactions.
* **Cooperative Background Concurrency:** Long-running document conversions, PDF rendering (PyMuPDF), and AI API calls run on background threads via `QThreadPool` / `DesktopJobRuntime` with cooperative pause, resume, and cancellation.

```text
PySide6 / QML Views
        ↓
Desktop Controllers & ViewModels
        ↓
Application Services (Submission, Execution, Recovery, Settings, Prompts)
        ↓
Application Ports (UnitOfWork, CredentialResolver, Storage, AIProvider)
        ↓
Infrastructure Adapters
        ↓
Local SQLite (WAL) / OS Keyring / Local Artifact Storage / AI Provider SDKs
```

---

## 2. Installation & Setup

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

## 3. Running PolpoT Desktop

To launch the desktop application:

```bash
python -m interfaces.desktop.app
```

---

## 4. Running the Test Suite

Run the full automated test suite:

```bash
pytest -q
```

---

## 5. Repository Structure

```text
PolpoT/
├── core/                           ← Pure Python domain entities & policies
│   ├── entities/                   ← Job, ApiSlot, AppSettings, Prompt, Artifact
│   └── policies/                   ← JobStateTransitionPolicy, RetryPolicy
├── application/                    ← Use cases, DTOs, events, and ports
│   ├── dto/                        ← Typed command and query DTOs
│   ├── events/                     ← Transport-neutral application events
│   ├── ports/                      ← Abstract interfaces (UoW, CredentialResolver, etc.)
│   └── services/                   ← JobExecution, Recovery, Submission, ApiKey, Settings
├── infrastructure/                 ← Concrete infrastructure implementations
│   ├── persistence/sqlite/         ← SQLite connection, WAL migrations, repositories
│   ├── security/                   ← KeyringCredentialResolver, EncryptedFileCredentialStore
│   ├── ai/                         ← Google & OpenAI adapters, RateLimitedAIExecutor
│   ├── storage/                    ← LocalStorageAdapter (Artifact and PDF ingestion)
│   ├── document/                   ← PyMuPDFDocumentProcessor (fitz)
│   └── events/                     ← InMemoryEventBus
├── interfaces/
│   └── desktop/                    ← Canonical Desktop Presentation Layer
│       ├── app.py                  ← Desktop application entrypoint (create_app)
│       ├── composition.py          ← DesktopAppContainer (Composition Root)
│       ├── bridge.py               ← QtSignalEventBridge (EventBus → Qt Signals)
│       ├── controllers/            ← JobController, ApiKeyController, SettingsController, etc.
│       ├── models/                 ← JobQueueModel, JobHistoryModel, ApiSlotModel, etc.
│       ├── workers/                ← DesktopJobRuntime, JobWorkerRunnable, Scheduler
│       └── qml/                    ← QML user interface views and components
└── tests/                          ← Unit, invariant, security, and lifecycle tests
```

---

## 6. Frozen Legacy Telegram Transport (Compatibility Subsystem)

> [!NOTE]
> Telegram is a **frozen legacy transport adapter** retained for backward compatibility. Desktop code has zero dependencies on Telegram transport modules or legacy MySQL infrastructure.

<details>
<summary>Legacy cPanel / MySQL Deployment Instructions (Click to expand)</summary>

### ۱. ساخت دیتابیس MySQL
1. وارد cPanel شوید
2. MySQL Databases → ساخت دیتابیس جدید
3. یک کاربر MySQL بسازید و به دیتابیس دسترسی کامل بدهید
4. اطلاعات را در `config.py` وارد کنید

### ۲. نصب Python App در cPanel
1. Software → Setup Python App
2. Python Version: 3.10+
3. Application Root: pdf_bot
4. Application Startup File: main.py

### ۳. راه‌اندازی دیتابیس
```bash
python database/connection.py
```

### ۴. تنظیم Cron Job برای Worker
در cPanel → Cron Jobs:
```
* * * * * /usr/bin/python3 /home/username/pdf_bot/services/worker.py >> /home/username/pdf_bot/worker.log 2>&1
```

### ۵. اجرای بات
```bash
python main.py
```
</details>
