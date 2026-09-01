# PolpoT Phase 8: Final Implementation-Ready Architecture Plan
**Document Version:** 2.0.0 (Reconciled & Corrected)  
**Status:** Implementation-Ready Architectural Specification  
**Target Milestone:** Phase 8 (Embedded Local-First Desktop: PySide6 + QML + SQLite + OS Keyring)  
**Authoritative Baseline Checkpoint:** `de47943` (Phase 6 legacy decommissioning) / `c2ef3d5` (HEAD reconciliation)  

---

## 1. Executive Summary

PolpoT is officially transitioning from a server/Telegram-centric application into a **fully local-first, single-user, serverless desktop application**. The desktop client is the primary product. There are **zero** remote server dependencies: no FastAPI, uvicorn, HTTP transport between UI and application, JWT tokens, cPanel, VPS, MySQL, Telegram-based storage, or remote application backend. The **only** network communication is direct, outbound HTTPS from the user's machine to AI provider endpoints (Google Gemini, OpenAI, OpenRouter, or compatible custom endpoints) using user-supplied API keys (Bring Your Own Key — BYOK).

This final architecture plan reconciles all findings from the actual repository at HEAD and resolves critical concurrency, security, and lifecycle questions:
1. **Zero Secret Persistence & BYOK:** API keys are never stored in SQLite, job JSON, DTOs, logs, or long-lived application memory. Credentials reside strictly in the OS Keyring (Windows Credential Manager, macOS Keychain, Linux Secret Service), with an explicit user-controlled passphrase + PBKDF2/Fernet fallback if OS keyring is unavailable.
2. **Atomic Job Claiming & Multi-Worker Concurrency:** Replaces `execute_next_job()` with an atomic `claim_job(job_id)` / `claim_next_pending()` transaction using SQLite `BEGIN IMMEDIATE` and WAL mode, guaranteeing that up to 4+ concurrent workers never race or double-execute jobs.
3. **Dedicated, Unbundled Domain Entities:** `AppSettings` holds only application, UI, and runtime preferences. `ApiSlot` holds only BYOK metadata and `CredentialRef`. `Job` holds execution state. `ScheduleInfo` holds persistent scheduling metadata.
4. **Pure Application Event Architecture:** Domain and application services emit transport-neutral events (`JobProgressEvent`, `ApiSwitchEvent`, `JobCompletedEvent`, `JobFailedEvent`, `JobCancelledEvent`, `JobStateChangedEvent`) that a Qt bridge translates to thread-safe Qt Signals for reactive QML consumption.
5. **Persistent Scheduling & Crash Recovery:** SQLite persistently tracks `scheduled_at` and `status`. An in-process scheduler and startup reconciler automatically handle missed schedules, graceful app shutdowns, and stale in-flight jobs after unexpected crashes.
6. **Complete REST & JWT Decommissioning:** Purges `interfaces/api/`, `FastAPI`, `uvicorn`, and `token_service.py` while preserving reusable application use cases.

---

## 2. Current Repository Assessment

An exhaustive audit of the codebase at HEAD (`c2ef3d5`) and baseline (`de47943`) reveals the following architectural reality:

```
                                  [ HEAD AUDIT ]
┌─────────────────────────────────────────────────────────────────────────────────┐
│ interfaces/api/ (FastAPI, uvicorn, JWT Bearer, Starlette Problem+JSON)          │
│ └── 10 REST route files, OpenAPI schema generation, auth middleware             │ ──► OBSOLETE
└────────────────────────────────────────┬────────────────────────────────────────┘
                                         │
┌───────────────────────────┐            │            ┌───────────────────────────┐
│ interfaces/telegram/      │ ───────────┼──────────► │ application/services/     │
│ (Frozen Legacy Transport) │            │            │ (JobSubmission, Recovery, │
└───────────────────────────┘            │            │  Query, Execution, etc)   │
                                         │            └─────────────┬─────────────┘
                                         ▼                          │
                               ┌───────────────────┐                │
                               │application/ports/ │ ◄──────────────┘
                               └─────────┬─────────┘
                                         │
        ┌────────────────────────────────┼────────────────────────────────┐
        ▼                                ▼                                ▼
┌─────────────────────────┐   ┌──────────────────────────┐   ┌────────────────────────────┐
│infrastructure/          │   │infrastructure/storage/   │   │infrastructure/ai/          │
│persistence/             │   │(LocalStorageAdapter)     │   │(GoogleAdapter,             │
│(PyMySQL Connection Pool)│   └──────────────────────────┘   │ OpenAIAdapter, Detector)   │
└─────────────────────────┘                                  └────────────────────────────┘
```

### 2.1 Critical Audit Findings & Reconciliations
1. **Duplicate `ApiSlot` Definitions:**
   - `core/entities/api_slot.py`: Modern dataclass with `CredentialRef`.
   - `core/ai/types.py`: Legacy dataclass containing plaintext `api_key: str`.
   - *Resolution:* Purge `core/ai/types.py:ApiSlot`. Standardize all code on `core/entities/api_slot.py:ApiSlot`.
2. **Server-Era User & Quota Coupling:**
   - `core/entities/user.py` contains `User`, `QuotaAllocation`, `UserPreferences` and daily reset logic.
   - `application/services/job_submission.py` and `quick_convert.py` enforce server-side quota checks (`QuotaPolicy.can_consume`).
   - *Resolution:* Eliminate `QuotaPolicy` and `User`. Replace with `AppSettings` (runtime preferences) and `LocalProfile`.
3. **Thread-Unsafe File Rate Limiter & POSIX Worker:**
   - `utils/rate_limiter.py` reads and writes `rate_limits.json` without file locking, causing corruption under multi-threaded execution.
   - `services/worker.py` uses `fcntl.flock` (Linux-only, broken on Windows) and executes single-job cron loops.
   - *Resolution:* Replace with an in-memory `ThreadSafeMemoryRateLimiter` (`threading.Lock`) and a multi-threaded `DesktopJobRuntime` (`QThreadPool` + `QRunnable`).
4. **Direct External PDF Referencing Risk:**
   - `JobSubmissionService` previously stored file paths directly. If the user moves or deletes the original PDF during processing, execution fails.
   - *Resolution:* Source PDFs are copied immediately upon submission into `~/.local/share/polpot/artifacts/job_{id}/source_document.pdf`.
5. **Database Driver Coupling:**
   - Persistence code is coupled to PyMySQL and MySQL `GET_LOCK()`.
   - *Resolution:* Introduce `infrastructure/persistence/sqlite/` using standard library `sqlite3` in WAL mode with atomic `BEGIN IMMEDIATE` transactions.

---

## 3. Final Target Architecture

The target architecture is an embedded, reactive, local-first desktop application with clean separation of concerns:

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                POLPOT DESKTOP APPLICATION                               │
│                                                                                         │
│  ┌───────────────────────────────────────────────────────────────────────────────────┐  │
│  │ PRESENTATION LAYER: interfaces/desktop/ (PySide6 / QML)                           │  │
│  │   ├── QML Views (MainWindow, JobQueueView, HistoryView, SettingsView, KeyModal)   │  │
│  │   ├── Desktop Controllers (JobController, ApiKeyController, SettingsController)   │  │
│  │   ├── Desktop ViewModels (JobQueueListModel, ApiSlotListModel, PromptListModel)   │  │
│  │   └── Event Bridge (QtSignalEventBridge: adapts Application Events to Qt Signals) │  │
│  └─────────────────────────────────────┬─────────────────────────────────────────────┘  │
│                                        │ Direct In-Process Python Method Calls (DTOs)   │
│                                        ▼                                                │
│  ┌───────────────────────────────────────────────────────────────────────────────────┐  │
│  │ APPLICATION LAYER: application/                                                   │  │
│  │   ├── Services:                                                                   │  │
│  │   │   ├── JobSubmissionService      ├── PromptService                             │  │
│  │   │   ├── JobExecutionService       ├── ApiKeyService (BYOK Keyring Management)   │  │
│  │   │   ├── JobRecoveryService        ├── LocalSettingsService                      │  │
│  │   │   ├── JobQueryService           └── QuickConvertService                       │  │
│  │   ├── Events (JobProgressEvent, ApiSwitchEvent, JobCompletedEvent, JobFailedEvent)│  │
│  │   └── Ports (IUnitOfWork, IJobRepo, ICredentialResolver, IArtifactStorage, etc)  │  │
│  └──────────────────────┬────────────────────────────────────────────┬───────────────┘  │
│                         │                                            │                  │
│                         ▼                                            ▼                  │
│  ┌──────────────────────────────────────────┐      ┌─────────────────────────────────┐  │
│  │ CORE DOMAIN LAYER: core/                 │      │ INFRASTRUCTURE LAYER:           │  │
│  │   ├── Entities: Job, ApiSlot, Prompt     │      │   infrastructure/               │  │
│  │   ├── Value Objects: CredentialRef       │      │   ├── persistence/sqlite/       │  │
│  │   ├── Policies: JobState, Fallback, Retry│      │   ├── security/ (Keyring+Fernet)│  │
│  │   └── Exceptions: DomainError, AIError   │      │   ├── storage/ (LocalArtifacts) │  │
│  └──────────────────────────────────────────┘      │   ├── rate_limiting/ (MemoryLock│  │
│                                                    │   └── ai/ (Google, OpenAI SDKs) │  │
│                                                    └─────────────────┬───────────────┘  │
└──────────────────────────────────────────────────────────────────────┼──────────────────┘
                                                                       │ Outbound HTTPS (BYOK)
                                                                       ▼
                                                     ┌───────────────────────────────────┐
                                                     │ Remote AI Provider APIs           │
                                                     │ (Gemini, OpenAI, OpenRouter)      │
                                                     └───────────────────────────────────┘
```

---

## 4. Architecture Decision Records (ADRs)

### ADR-01: Embedded SQLite Persistence with WAL Mode & Thread-Local UoW
* **Context:** The application is a local single-user desktop program running on Windows, macOS, and Linux. It requires ACID guarantees without an external database daemon.
* **Decision:** Use Python's built-in `sqlite3` configured with `PRAGMA journal_mode=WAL;`, `PRAGMA busy_timeout=5000;`, `PRAGMA foreign_keys=ON;`, and `PRAGMA synchronous=NORMAL;`. Connections are strictly thread-local: each worker thread opens its own connection and Unit of Work.
* **Consequences:** Eliminates external DB servers. WAL mode allows concurrent readers alongside a single active writer without lock contention.

### ADR-02: BYOK Credential Storage: OS Keyring Primary with Passphrase KDF Fallback
* **Context:** Users provide their own AI API keys. Plaintext storage in SQLite or config files is unacceptable. Machine identifiers alone are predictable and insufficient as root encryption keys.
* **Decision:** Use Python `keyring` as the primary secret store (Windows Credential Manager, macOS Keychain, Linux Secret Service). If OS Keyring is unavailable (headless environments, minimal Linux, or test CI), fallback to `EncryptedFileCredentialStore` where keys are encrypted with `Fernet` (AES-128-CBC + HMAC-SHA256) using a key derived via PBKDF2HMAC (600,000 iterations + 16-byte random salt) from an explicit user-provided master passphrase.
* **Consequences:** Zero plaintext credentials in SQLite or job metadata. Full portability with cryptographic safety.

### ADR-03: Elimination of In-Process HTTP / REST / JWT Boundary
* **Context:** Phase 7 implemented FastAPI and JWT tokens for an anticipated client-server desktop client. The architecture is now strictly embedded.
* **Decision:** Remove FastAPI, uvicorn, HTTP route modules, and JWT token services. Desktop controllers call application services directly in-process.
* **Consequences:** Zero network port conflicts, instant application startup, no local firewall warnings, reduced dependencies, and lower memory footprint.

### ADR-04: Multi-Worker Concurrent Desktop Runtime with Atomic Job Claiming
* **Context:** Desktop users may submit multiple PDFs simultaneously. A single `execute_next_job()` loop causes race conditions when multiple workers run.
* **Decision:** Implement `DesktopJobRuntime` backed by `QThreadPool`. Jobs are claimed using atomic SQLite updates (`claim_job(job_id)` / `claim_next_pending()`) wrapped in `BEGIN IMMEDIATE` transactions.
* **Consequences:** Safe concurrent execution of up to 4+ jobs simultaneously with deterministic state transitions and cooperative cancellation.

### ADR-05: Separation of Concerns (Anti-God-Object Architecture)
* **Context:** Replacing `User` with `AppSettings` risks creating an oversized god object.
* **Decision:** Strictly separate state: `AppSettings` (singleton UI/runtime preferences), `ApiSlot` (BYOK configuration), `Job` (PDF processing execution state), and `ScheduleMetadata` (future execution triggers).
* **Consequences:** High cohesion, granular SQLite tables, and clean domain boundaries.

### ADR-06: Transport-Agnostic Application Events & Qt Signal Bridge
* **Context:** The application layer must not depend on `PySide6`, but the UI requires reactive progress updates.
* **Decision:** Define pure Python event dataclasses (`JobProgressEvent`, `ApiSwitchEvent`, etc.) published via an event bus port. A dedicated `QtSignalEventBridge` in the desktop layer subscribes to the bus and emits Qt Signals to the GUI thread.
* **Consequences:** 100% testable application layer with zero Qt imports; clean 60 FPS reactive UI.

### ADR-07: Local Source PDF Immutability via Ingestion Copying
* **Context:** If a user submits a PDF from `/Downloads` and later deletes/moves it while the job is pending or processing, crash recovery fails.
* **Decision:** `JobSubmissionService` copies the input PDF into `~/.local/share/polpot/artifacts/job_{id}/source_document.pdf` immediately upon submission.
* **Consequences:** Immutable source files guaranteed for page re-rendering, retries, and crash recovery.

---

## 5. Dependency Rules & Architectural Invariants

```
┌─────────────────────────────────────────────────────────────┐
│                   interfaces/desktop/ (Qt)                  │
│                              │                              │
│                              ▼                              │
│                    application/services/                    │
│                     application/ports/                      │
│                              │                              │
│                              ▼                              │
│                            core/                            │
│                              ▲                              │
│                              │ (implements ports)           │
│                       infrastructure/                       │
└─────────────────────────────────────────────────────────────┘
```

### 5.1 Forbidden Dependency Matrix (AST Enforced)

| Source Layer | Strictly Forbidden Imports |
| :--- | :--- |
| `core/` | `PySide6`, `Qt`, `sqlite3`, `keyring`, `fastapi`, `starlette`, `telegram`, `infrastructure`, `application`, `interfaces` |
| `application/` | `PySide6`, `Qt`, `sqlite3`, `keyring`, `fastapi`, `starlette`, `telegram`, `infrastructure`, `interfaces` |
| `interfaces/desktop/controllers/` | `sqlite3`, `pymysql`, raw SQL strings, concrete AI SDKs (`google.genai`, `openai`), `keyring` |
| `interfaces/desktop/qml/` | Direct Python backend modules, file system I/O |

---

## 6. Domain Reconciliation

### 6.1 Entity Audit & Transformations

```
Source Entity / File               Phase 8 Classification    Action & Rationale
─────────────────────────────────────────────────────────────────────────────────────────────────────────
core/entities/user.py              REPLACE                   Replace User, QuotaAllocation, UserPreferences
                                                             with core/entities/settings.py (AppSettings).
core/entities/job.py               KEEP + MODIFY             Add scheduled_at, cancel_requested, claimed_at;
                                                             remove user_id foreign key constraint.
core/entities/api_slot.py          KEEP + MODIFY             Retain CredentialRef; remove 'public' and
                                                             'donated' slot types; standardize on 'byok'.
core/entities/credential_ref.py    KEEP                      Pure value object (identifier, provider, slot_type).
core/entities/artifact.py          KEEP                      ArtifactHandle and ArtifactType definitions.
core/entities/prompt.py            KEEP                      Prompt entity (id, name, text, prompt_type, is_default).
core/ai/types.py                   KEEP + MODIFY             DELETE duplicate ApiSlot; retain VisionPromptRequest,
                                                             TextPromptRequest, AIResponse.
core/ai/exceptions.py              KEEP                      Provider-neutral AI exception hierarchy.
core/policies/quota_policy.py      DELETE                    Server-era user quotas are obsolete for desktop.
core/policies/job_state_policy.py  KEEP + MODIFY             Add CANCELLED status and legal transition rules.
core/policies/fallback_policy.py   KEEP                      Pure domain logic for advancing API fallback chains.
core/policies/retry_policy.py      KEEP                      Pure domain logic for retry eligibility.
```

### 6.2 AppSettings Entity Structure (`core/entities/settings.py`)

```python
@dataclass
class AppSettings:
    """Application preferences and runtime configuration."""
    theme: str = "system"                   # 'system' | 'dark' | 'light'
    max_concurrent_jobs: int = 2            # Parallel worker pool capacity (1-8)
    auto_retry: bool = True                 # Auto-retry on transient failure
    auto_pipeline2: bool = False            # Auto-trigger markdown refinement
    default_prompt_id: Optional[int] = None
    default_pipeline2_prompt_id: Optional[int] = None
    artifact_retention_days: int = 30       # Artifact auto-cleanup threshold
    missed_schedule_policy: str = "prompt"  # 'run_immediately' | 'prompt' | 'mark_paused'
```

---

## 7. Application Service Reconciliation

```
Application Service            Phase 8 Status    Changes & Reconciliations
──────────────────────────────────────────────────────────────────────────────────────────────────
JobSubmissionService           KEEP + REFACTOR   - Ingests file path or bytes, copies into job artifact folder.
                                                 - Supports optional scheduled_at parameter.
                                                 - Removes User quota validation calls.
JobExecutionService            KEEP + REFACTOR   - Executes a specific claimed job_id in-process.
                                                 - Verifies cancel_requested before each page & AI call.
                                                 - Publishes pure application events.
                                                 - Removes quota increments and remote backup calls.
JobRecoveryService             KEEP + REFACTOR   - Resumes PAUSED / FAILED jobs.
                                                 - Startup reconciler marks stale PROCESSING jobs as PAUSED.
                                                 - Removes user_id ownership checks.
JobQueryService                KEEP + REFACTOR   - Provides filtered/sorted DTO queries for QML list models.
                                                 - Supports active queue, history, and status filtering.
QuickConvertService            KEEP + REFACTOR   - Converts single images/screenshots directly via AI executor.
                                                 - Removes user quota checks.
PromptService                  KEEP              - Reusable as-is for CRUD on prompts.
ApiKeyService                  REFACTOR          - Replaces ApiManagementService.
                                                 - Manages BYOK slots (Google, OpenAI, OpenRouter, Custom).
                                                 - Delegates secret storage to ICredentialResolver.
                                                 - Purges all donation and public pool methods.
LocalSettingsService           REPLACE           - Replaces UserManagementService.
                                                 - CRUD operations on singleton AppSettings.
ArtifactService                KEEP + REFACTOR   - Integrates with OS: reveal in folder, open default app,
                                                   copy to clipboard, export markdown/zip.
AuthService                    DELETE            - Obsolete (zero JWT / exchange codes in desktop app).
```

---

## 8. Final SQLite Schema & Data Architecture

The SQLite database resides in the platform-standard user application data directory:
- **Windows:** `%APPDATA%\PolpoT\polpot.db`
- **macOS:** `~/Library/Application Support/PolpoT/polpot.db`
- **Linux:** `~/.local/share/polpot/polpot.db` (or `$XDG_DATA_HOME/polpot/polpot.db`)

### 8.1 Production Desktop DDL (`schema_v1.sql`)

```sql
-- Schema Migration Tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Singleton Application Settings
CREATE TABLE IF NOT EXISTS app_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    theme TEXT NOT NULL DEFAULT 'system',
    max_concurrent_jobs INTEGER NOT NULL DEFAULT 2,
    auto_retry INTEGER NOT NULL DEFAULT 1,
    auto_pipeline2 INTEGER NOT NULL DEFAULT 0,
    default_prompt_id INTEGER,
    default_pipeline2_prompt_id INTEGER,
    artifact_retention_days INTEGER NOT NULL DEFAULT 30,
    missed_schedule_policy TEXT NOT NULL DEFAULT 'prompt',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(default_prompt_id) REFERENCES prompts(id) ON DELETE SET NULL,
    FOREIGN KEY(default_pipeline2_prompt_id) REFERENCES prompts(id) ON DELETE SET NULL
);

-- Extraction and Refinement Prompts
CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    text TEXT NOT NULL,
    prompt_type TEXT NOT NULL CHECK(prompt_type IN ('pipeline1', 'pipeline2', 'quick_convert')),
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- API Slots (BYOK Metadata Only — Secrets stored in OS Keyring)
CREATE TABLE IF NOT EXISTS api_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL CHECK(provider IN ('google', 'openai', 'openrouter', 'custom')),
    label TEXT NOT NULL,
    credential_identifier TEXT NOT NULL UNIQUE, -- Keyring lookup key (e.g. google_abc123)
    selected_model TEXT,
    base_url TEXT,
    supported_models TEXT,                      -- JSON array of strings
    priority INTEGER NOT NULL DEFAULT 1,
    is_active INTEGER NOT NULL DEFAULT 1,
    total_pages_processed INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Primary PDF Conversion Jobs
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,                    -- Path to local ingested source PDF
    total_pages INTEGER NOT NULL DEFAULT 0,
    processed_pages INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK(status IN ('pending', 'processing', 'done', 'paused', 'failed', 'cancelled')),
    prompt_id INTEGER,
    prompt_text TEXT,
    api_chain TEXT,                             -- JSON array of ApiSlot descriptors (NO RAW KEYS)
    current_api_index INTEGER NOT NULL DEFAULT 0,
    api_switch_log TEXT,                        -- JSON array of switch events
    output_path TEXT,                           -- Path to local output markdown artifact
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    auto_pipeline2 INTEGER NOT NULL DEFAULT 0,
    pipeline2_prompt_id INTEGER,
    scheduled_at TIMESTAMP,                     -- Persistent future execution trigger (UTC ISO-8601)
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    claimed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(prompt_id) REFERENCES prompts(id) ON DELETE SET NULL,
    FOREIGN KEY(pipeline2_prompt_id) REFERENCES prompts(id) ON DELETE SET NULL
);

-- Pipeline 2 Markdown Refinement Jobs
CREATE TABLE IF NOT EXISTS pipeline2_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_job_id INTEGER NOT NULL,
    prompt_id INTEGER,
    prompt_text TEXT,
    status TEXT NOT NULL CHECK(status IN ('pending', 'processing', 'done', 'paused', 'failed', 'cancelled')),
    input_path TEXT,
    output_path TEXT,
    api_chain TEXT,                             -- JSON array (NO RAW KEYS)
    current_api_index INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    claimed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(source_job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    FOREIGN KEY(prompt_id) REFERENCES prompts(id) ON DELETE SET NULL
);

-- Performance Indexes
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_scheduled ON jobs(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_p2_jobs_status ON pipeline2_jobs(status);
```

### 8.2 SQLite Concurrency & Pragma Configuration

Every connection initialized by `SQLiteDatabaseManager` executes:
```sql
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;
PRAGMA temp_store = MEMORY;
```

---

## 9. Credential & Security Architecture

### 9.1 Storage Hierarchy & Master Key Derivation

```
                              [ API Key Ingestion ]
                                        │
                                        ▼
                         Is OS Keyring Available?
                                ├── YES ──► Store secret in OS Keyring
                                │           (Windows Credential Manager / macOS Keychain / Secret Service)
                                │
                                └── NO  ──► Prompt user for Master Passphrase
                                            │
                                            ▼
                                    PBKDF2HMAC KDF
                                    (600,000 iterations, SHA-256, 16-byte random salt)
                                            │
                                            ▼
                                    Fernet Cipher (AES-128-CBC + HMAC)
                                            │
                                            ▼
                                    Encrypted Local Store (~/.local/share/polpot/vault.enc)
```

### 9.2 Complete Credential Lifecycle

| Operation | Implementation & Security Policy |
| :--- | :--- |
| **Create / Register** | Generates UUID identifier (`google_3f8a9e1b`). Writes raw key to Keyring / Fernet store. Inserts slot record into SQLite containing `credential_identifier`. Raw key is immediately discarded from caller scope. |
| **Read / Resolve** | Triggered only inside worker thread at the moment of AI invocation via `ICredentialResolver.resolve_api_key(slot.credential_ref)`. Key exists in memory only for the duration of the API call. |
| **Update** | Overwrites secret in Keyring / Fernet store for existing `credential_identifier`. |
| **Delete** | Deletes secret from Keyring / Fernet store and deletes slot record from SQLite. |
| **Import / Export** | Settings export bundles slot metadata **without** secrets. Optional encrypted backup exports API keys encrypted with a user-specified export password. |
| **Headless / CI** | Automatically detects headless environment; uses an in-memory ephemeral store or test passphrase without hanging for GUI dialogs. |

### 9.3 CredentialRef-Only Invariant Enforcement
- `Job.api_chain` serialization:
  ```json
  [
    {
      "id": 1,
      "provider": "google",
      "label": "My Gemini Flash",
      "credential_ref": "byok:google:google_3f8a9e1b",
      "selected_model": "gemini-3.5-flash",
      "base_url": null
    }
  ]
  ```
- **AST Test Verification:** Static AST analyzers scan all DTOs, domain models, and SQL strings in the codebase to guarantee that no field or query contains `api_key` outside `ICredentialResolver` and AI adapter implementations.

---

## 10. Job Execution & Concurrency Architecture

### 10.1 Atomic Job Claiming Transaction

To eliminate race conditions between multiple parallel workers, job claiming is executed in an explicit atomic transaction using `BEGIN IMMEDIATE`:

```python
def claim_next_pending_job(self) -> Optional[Job]:
    """Atomically claims the next pending job for execution."""
    with self.conn: # Auto-commits on success, rolls back on error
        cur = self.conn.cursor()
        # Find next eligible pending job (immediate or past-due schedule)
        cur.execute(
            """
            SELECT id FROM jobs 
            WHERE status = 'pending' 
              AND (scheduled_at IS NULL OR scheduled_at <= CURRENT_TIMESTAMP)
            ORDER BY id ASC LIMIT 1
            """
        )
        row = cur.fetchone()
        if not row:
            return None
        
        job_id = row[0]
        cur.execute(
            """
            UPDATE jobs 
            SET status = 'processing', claimed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
            """,
            (job_id,)
        )
        if cur.rowcount != 1:
            return None # Another worker claimed it concurrently
        
        return self.get_by_id(job_id)
```

### 10.2 Desktop Concurrency Model & Synchronization Matrix

```
┌─────────────────────────┐
│     GUI Main Thread     │ ── (User submits PDF / cancels / configures)
└────────────┬────────────┘
             │ Dispatches Job ID
             ▼
┌─────────────────────────┐
│    DesktopJobRuntime    │ ── Manages QThreadPool (Worker Pool: 1 to 8 threads)
└────────────┬────────────┘
             │ Spawns QRunnable per job
             ├───────────────────────────────────────────┐
             ▼                                           ▼
┌─────────────────────────┐                 ┌─────────────────────────┐
│   Worker Thread 1       │                 │   Worker Thread 2       │
│  - Thread-local SQLite  │                 │  - Thread-local SQLite  │
│  - Thread-local UoW     │                 │  - Thread-local UoW     │
│  - JobExecutionService  │                 │  - JobExecutionService  │
│    (processes Job #1)   │                 │    (processes Job #2)   │
└────────────┬────────────┘                 └────────────┬────────────┘
             │                                           │
             └─────────────────────┬─────────────────────┘
                                   │ Emits Application Events
                                   ▼
                    ┌─────────────────────────────┐
                    │     QtSignalEventBridge     │
                    └──────────────┬──────────────┘
                                   │ (Qt QueuedConnection Signals)
                                   ▼
                    ┌─────────────────────────────┐
                    │    GUI Thread / QML UI      │ ── (Smooth 60 FPS Updates)
                    └─────────────────────────────┘
```

| Shared Resource | Concurrency Mechanism |
| :--- | :--- |
| **SQLite Database** | Thread-isolated connections per worker + WAL mode + 5000ms busy timeout + `BEGIN IMMEDIATE` claiming. |
| **Rate Limiter** | `ThreadSafeMemoryRateLimiter` using `threading.Lock()` for atomic interval tracking per API slot. |
| **Cancellation Flags** | Thread-safe `Dict[int, threading.Event]` in `DesktopJobRuntime`; checked by worker on every page boundary. |
| **Artifact Filesystem** | Dedicated directory per job (`artifacts/job_{id}/`); zero cross-job file contention. |
| **UI State / Models** | Owned exclusively by the Qt GUI main thread; updated via Qt queued signal slots. |

---

## 11. Persistent Scheduler Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                       SQLite Database                       │
│    jobs.scheduled_at = '2026-09-01T15:30:00Z', status = 'pending'
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                     DesktopJobScheduler                     │
│  - Evaluates due jobs in SQLite                             │
│  - Handles startup catch-up for past-due jobs               │
│  - Prevents duplicate dispatches via in-memory tracking     │
└──────────────────────────────┬──────────────────────────────┘
                               │ Triggered every 10s by QTimer (GUI Thread)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                      DesktopJobRuntime                      │
│  - Enqueues due jobs into QThreadPool for execution         │
└─────────────────────────────────────────────────────────────┘
```

### 11.1 Scheduling Semantics
1. **Creation:** User submits a job with `scheduled_at: datetime` (stored as UTC ISO-8601 string).
2. **Heartbeat:** `DesktopJobScheduler` runs a 10-second ticker (`QTimer` in GUI thread).
3. **Dispatch:** Queries SQLite for pending jobs where `scheduled_at <= CURRENT_TIMESTAMP`. Enqueues due jobs into `DesktopJobRuntime`.
4. **Startup Catch-Up & Crash Reconciler:**
   - When PolpoT launches, the scheduler scans for pending jobs with `scheduled_at < CURRENT_TIMESTAMP`.
   - Based on `AppSettings.missed_schedule_policy`:
     - `'run_immediately'`: Automatically dispatches them to the queue.
     - `'prompt'`: Displays a banner in the UI asking the user to confirm execution.
     - `'mark_paused'`: Moves status to `paused` with error `Missed schedule while app was closed`.

---

## 12. Progress & Event Architecture

### 12.1 Pure Application Event Hierarchy (`application/events.py`)

```python
@dataclass(frozen=True)
class JobProgressEvent:
    job_id: int
    processed_pages: int
    total_pages: int
    percent: float

@dataclass(frozen=True)
class ApiSwitchEvent:
    job_id: int
    old_label: str
    new_label: str
    reason: str
    page: int

@dataclass(frozen=True)
class JobCompletedEvent:
    job_id: int
    output_artifact_uri: str

@dataclass(frozen=True)
class JobFailedEvent:
    job_id: int
    error_message: str
    is_retryable: bool

@dataclass(frozen=True)
class JobCancelledEvent:
    job_id: int

@dataclass(frozen=True)
class JobStateChangedEvent:
    job_id: int
    old_status: JobStatus
    new_status: JobStatus
```

### 12.2 Event Publishing & Qt Signal Bridge

```python
class IApplicationEventPublisher(ABC):
    @abstractmethod
    def publish(self, event: Any) -> None: ...

class QtSignalEventBridge(QObject, IApplicationEventPublisher):
    """Adapts Application Events to Qt Queued Signals for GUI consumption."""
    progress_signal = Signal(int, int, int, float)       # job_id, processed, total, pct
    api_switch_signal = Signal(int, str, str, str, int)  # job_id, old, new, reason, page
    completed_signal = Signal(int, str)                  # job_id, uri
    failed_signal = Signal(int, str, bool)               # job_id, error, retryable
    cancelled_signal = Signal(int)                       # job_id
    state_changed_signal = Signal(int, str, str)         # job_id, old_st, new_st

    def publish(self, event: Any) -> None:
        if isinstance(event, JobProgressEvent):
            self.progress_signal.emit(event.job_id, event.processed_pages, event.total_pages, event.percent)
        elif isinstance(event, ApiSwitchEvent):
            self.api_switch_signal.emit(event.job_id, event.old_label, event.new_label, event.reason, event.page)
        elif isinstance(event, JobCompletedEvent):
            self.completed_signal.emit(event.job_id, event.output_artifact_uri)
        elif isinstance(event, JobFailedEvent):
            self.failed_signal.emit(event.job_id, event.error_message, event.is_retryable)
        elif isinstance(event, JobCancelledEvent):
            self.cancelled_signal.emit(event.job_id)
        elif isinstance(event, JobStateChangedEvent):
            self.state_changed_signal.emit(event.job_id, event.old_status.value, event.new_status.value)
```

---

## 13. Artifact & File Lifecycle Architecture

```
~/.local/share/polpot/artifacts/ (or %APPDATA%/PolpoT/artifacts/)
└── job_42/
    ├── source_document.pdf          ── Ingested copy of original PDF
    ├── page_1.jpg                   ── Rendered PyMuPDF image
    ├── page_2.jpg
    ├── crop_42_1.jpg                ── Cropped formula/diagram image
    ├── crop_42_2.jpg
    ├── output_42.md                 ── Pipeline 1 final Markdown
    ├── p2_output_42.md              ── Pipeline 2 refined Markdown
    └── attachments.zip              ── Bundled export archive of crops
```

### 13.1 Desktop Integration Features
- **Reveal in File Manager:** `ArtifactService.reveal_in_file_manager(job_id)` calls `QDesktopServices.openUrl("file:///...")` on the job folder.
- **Open in Default App:** `ArtifactService.open_default_viewer(job_id, "markdown")` opens the system's default Markdown editor (e.g. Obsidian, VS Code, Typora).
- **Clipboard Copy:** One-click copy of full Markdown content to system clipboard via `QGuiApplication.clipboard()`.
- **Retention Pruning:** Background routine scans `artifacts/` on startup and deletes folders older than `AppSettings.artifact_retention_days`.

---

## 14. Desktop Controller Boundary & Presentation Architecture

```
interfaces/desktop/
├── app.py                      ── Application entry point (PySide6 QGuiApplication + QmlEngine)
├── composition.py              ── Desktop Composition Root
├── notifier.py                 ── QtSignalEventBridge
├── controllers/
│   ├── job_controller.py       ── QObject: submit_job, cancel_job, retry_job, resume_job
│   ├── api_key_controller.py   ── QObject: register_key, test_key, delete_key, toggle_active
│   ├── prompt_controller.py    ── QObject: create_prompt, update_prompt, delete_prompt, set_default
│   ├── settings_controller.py  ── QObject: update_settings, export_config, prune_artifacts
│   └── quick_convert_controller.py ── QObject: convert_image_bytes
├── models/
│   ├── job_queue_model.py      ── QAbstractListModel (live active queue)
│   ├── job_history_model.py    ── QAbstractListModel (paginated/filtered history)
│   ├── api_slot_model.py       ── QAbstractListModel (BYOK slot list)
│   └── prompt_list_model.py    ── QAbstractListModel (prompts list)
├── workers/
│   ├── runtime.py              ── DesktopJobRuntime (manages QThreadPool)
│   ├── job_worker.py           ── JobWorkerRunnable (executes single job)
│   └── scheduler.py            ── DesktopJobScheduler (evaluates persistent schedule)
└── qml/
    ├── Main.qml                ── App Window, Navigation Sidebar, Header
    ├── views/
    │   ├── JobQueueView.qml    ── Active conversions, progress bars, cancellation buttons
    │   ├── HistoryView.qml     ── Searchable history, preview panel, export buttons
    │   ├── QuickConvertView.qml── Image drag & drop OCR
    │   ├── ApiKeyView.qml      ── BYOK slot management, status indicators, test button
    │   ├── PromptEditorView.qml── System prompt management
    │   └── SettingsView.qml    ── Theme, concurrency slider, retention settings
    └── components/             ── Reusable buttons, cards, status badges, modal dialogs
```

---

## 15. REST / Server / JWT Decommissioning Strategy

### 15.1 Decommissioning Checklist
1. **Delete REST Interface:** Remove `interfaces/api/` completely (10 route files, `app.py`, `deps.py`, `__main__.py`).
2. **Delete JWT & Token Infrastructure:** Remove `infrastructure/security/jwt_token_service.py`, `infrastructure/security/token_service.py`, and `application/ports/token_service.py`.
3. **Delete Server Auth Service:** Remove `application/services/auth_service.py`.
4. **Clean Dependencies:** Remove `fastapi`, `uvicorn`, `python-multipart` from `requirements.txt`.
5. **Preserve Shared AI & Storage Ports:** Ensure generic ports (`IArtifactStorage`, `IDocumentProcessor`, `AIProviderPort`, `IProviderDetector`) remain intact.

---

## 16. Telegram Freeze Strategy

- **Freeze Status:** `interfaces/telegram/`, `handlers/`, and `main.py` are **frozen legacy modules**.
- **Isolation Rules:**
  - Zero imports from `telegram`, `handlers`, or `main.py` in `interfaces/desktop/` or any `application/` service.
  - Telegram remains in the repository as a reference implementation of a secondary transport adapter.
  - No future changes in domain or persistence may be blocked by Telegram constraints.

---

## 17. Testing Migration Strategy

```
┌───────────────────────────────────────────────────────────────────────────────┐
│                               TEST SUITE AUDIT                                │
│                                                                               │
│  ┌───────────────────────────┐  ┌───────────────────────────┐  ┌────────────┐ │
│  │ 1. RETAIN (55 tests)      │  │ 2. MIGRATE (45 tests)     │  │ 3. REMOVE  │ │
│  │ - test_ai_adapters.py     │  │ - Characterization tests  │  │ (35 tests) │ │
│  │ - test_fallback_policy    │  │   (MySQL → SQLite)        │  │ - FastAPI  │ │
│  │ - test_pymupdf_processor  │  │ - Job state retry tests   │  │   routes   │ │
│  │ - test_local_storage      │  │   (HTTP → Service DTOs)   │  │ - JWT auth │ │
│  │ - test_retry_policy       │  │ - Donation tests          │  │ - Telegram │ │
│  │                           │  │   (→ BYOK Slot tests)     │  │   OTP      │ │
│  └───────────────────────────┘  └───────────────────────────┘  └────────────┘ │
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────────┐  │
│  │ 4. ADD NEW PHASE 8 TESTS (~60 tests)                                    │  │
│  │ - test_sqlite_persistence.py (CRUD, transactions, WAL mode)             │  │
│  │ - test_sqlite_concurrency.py (Atomic job claiming, parallel workers)    │  │
│  │ - test_keyring_security.py (Keyring resolution, Fernet KDF fallback)    │  │
│  │ - test_secret_non_persistence.py (AST & DB dumps prove 0 raw keys)      │  │
│  │ - test_persistent_scheduler.py (Due jobs, catch-up, missed schedules)  │  │
│  │ - test_desktop_controllers.py (Controller to application service DTOs)  │  │
│  │ - test_desktop_architecture_invariants.py (AST boundary enforcement)    │  │
│  └─────────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────────┘
```

---

## 18. Packaging & Distribution Considerations

1. **Cross-Platform Storage Resolution:** Use `platformdirs` to resolve standard user paths:
   - Config: `platformdirs.user_config_dir("PolpoT")`
   - Data: `platformdirs.user_data_dir("PolpoT")`
   - Cache: `platformdirs.user_cache_dir("PolpoT")`
2. **Keyring Backend Packaging:** Configure PyInstaller/Nuitka with hidden imports for `keyring.backends.Windows`, `keyring.backends.macOS`, and `keyring.backends.SecretService`.
3. **PyMuPDF C-Extension Bundling:** Ensure `fitz` binary shared libraries are included in build configurations.
4. **First-Run Setup:** Automated creation of app data directories, running SQLite baseline migration, and verifying Keyring connectivity on initial launch.

---

## 19. Detailed Phased Implementation Plan

```
Phase 8A: Architecture Reconciliation & Domain Foundations
    ├── Standardize ApiSlot, create AppSettings, remove QuotaPolicy
    └── Update Application Ports for Desktop (UoW, Repositories, Notifier)
         │
         ▼
Phase 8B: SQLite Persistence Implementation
    ├── SQLiteDatabaseManager, SQLiteUnitOfWork, SQLiteRepositories
    └── Schema migration runner & schema_v1.sql
         │
         ▼
Phase 8C: Credential Security & BYOK Architecture
    ├── KeyringCredentialResolver
    ├── EncryptedFileCredentialStore (Passphrase PBKDF2 fallback)
    └── ApiKeyService (BYOK Keyring Management)
         │
         ▼
Phase 8D: Concurrent Desktop Runtime & Execution Engine
    ├── ThreadSafeMemoryRateLimiter
    ├── Atomic job claiming (claim_next_pending_job)
    └── DesktopJobRuntime (QThreadPool + QRunnable)
         │
         ▼
Phase 8E: Persistent Scheduler & Startup Recovery
    ├── DesktopJobScheduler & QTimer heartbeat
    └── Startup crash reconciler (reclaims stale processing jobs)
         │
         ▼
Phase 8F: PySide6 / QML Presentation Layer
    ├── QtSignalEventBridge
    ├── Desktop Controllers & ViewModels
    └── QML Views (MainWindow, Queue, History, Settings, KeyModal)
         │
         ▼
Phase 8G: REST & Server Decommissioning
    ├── Delete interfaces/api/ and security/token_service.py
    └── Clean requirements.txt
         │
         ▼
Phase 8H: Integration Testing & Architectural Hardening
    ├── SQLite WAL concurrency test suite
    ├── AST architecture invariant enforcement
    └── End-to-end local conversion verification
```

### Phase 8A: Architecture Reconciliation & Domain Foundations
* **Objective:** Clean domain entities, eliminate duplicate `ApiSlot`, introduce `AppSettings`, and update application ports for local desktop operation.
* **Prerequisites:** Pass baseline test suite.
* **Files to Modify:**
  - `core/entities/api_slot.py` (standardize BYOK fields).
  - `core/entities/job.py` (add `scheduled_at`, `cancel_requested`, `claimed_at`).
  - `core/ai/types.py` (remove duplicate `ApiSlot`).
  - `core/policies/job_state_policy.py` (add `CANCELLED` status transitions).
  - `application/ports/repositories.py` (refactor for desktop SQLite repositories).
  - `application/ports/unit_of_work.py` (update repo properties on `IUnitOfWork`).
* **Files to Create:**
  - `core/entities/settings.py` (`AppSettings`).
  - `application/events.py` (pure application events: `JobProgressEvent`, etc.).
* **Files to Delete:**
  - `core/policies/quota_policy.py`.
* **Tests to Add/Update:**
  - `tests/unit/test_domain_reconciliation.py`.
* **Invariants to Verify:** `core/` contains 0 imports from external libraries; `core/ai/types.py` has no `ApiSlot`.
* **Acceptance Criteria:** Domain models instantiate cleanly; all existing policy tests pass.
* **Recommended Commit Message:** `chore(phase-8a): reconcile domain entities and application ports for desktop`

### Phase 8B: SQLite Persistence Implementation
* **Objective:** Implement full SQLite storage engine with WAL mode, busy timeout, atomic transactions, and schema migration runner.
* **Prerequisites:** Phase 8A completed.
* **Files to Create:**
  - `infrastructure/persistence/sqlite/connection.py` (`SQLiteDatabaseManager`).
  - `infrastructure/persistence/sqlite/schema_v1.sql` (DDL).
  - `infrastructure/persistence/sqlite/migration_runner.py` (`SQLiteMigrationRunner`).
  - `infrastructure/persistence/sqlite/repositories.py` (`SQLiteJobRepo`, `SQLitePromptRepo`, `SQLiteApiSlotRepo`, `SQLiteSettingsRepo`, `SQLitePipeline2JobRepo`).
  - `infrastructure/persistence/sqlite/unit_of_work.py` (`SQLiteUnitOfWork`, `SQLiteUnitOfWorkFactory`).
* **Tests to Add:**
  - `tests/unit/test_sqlite_persistence.py` (CRUD, transaction rollback, migration runner).
* **Invariants to Verify:** Every connection sets WAL mode; foreign keys enabled; thread-isolation strictly enforced.
* **Acceptance Criteria:** 100% of SQLite repository and UoW tests pass.
* **Recommended Commit Message:** `feat(phase-8b): implement sqlite wal persistence and repositories`

### Phase 8C: Credential Security & BYOK Architecture
* **Objective:** Implement BYOK secret resolution via OS Keyring with passphrase-derived PBKDF2/Fernet fallback.
* **Prerequisites:** Phase 8B completed.
* **Files to Create:**
  - `infrastructure/security/keyring_resolver.py` (`KeyringCredentialResolver`).
  - `infrastructure/security/encrypted_store.py` (`EncryptedFileCredentialStore`).
  - `application/services/api_key_service.py` (`ApiKeyService`).
* **Tests to Add:**
  - `tests/unit/test_keyring_security.py` (Keyring resolution, Fernet fallback, master password KDF).
  - `tests/unit/test_secret_non_persistence.py` (Prove zero plaintext keys in SQLite tables).
* **Invariants to Verify:** Zero `api_key` values in SQLite; `CredentialRef` is the sole identifier.
* **Acceptance Criteria:** API keys can be saved, resolved, tested, and deleted securely.
* **Recommended Commit Message:** `feat(phase-8c): implement os keyring credential resolver and byok security`

### Phase 8D: Concurrent Desktop Runtime & Execution Engine
* **Objective:** Implement multi-threaded in-process execution with atomic job claiming and cooperative cancellation.
* **Prerequisites:** Phase 8C completed.
* **Files to Create:**
  - `infrastructure/rate_limiting/memory_rate_limiter.py` (`ThreadSafeMemoryRateLimiter`).
  - `interfaces/desktop/workers/job_worker.py` (`JobWorkerRunnable`).
  - `interfaces/desktop/workers/runtime.py` (`DesktopJobRuntime`).
* **Files to Modify:**
  - `application/services/job_execution.py` (integrate cooperative cancellation and event publishing).
  - `application/services/job_submission.py` (copy source PDF to artifact directory).
* **Tests to Add:**
  - `tests/unit/test_desktop_runtime_concurrency.py` (Atomic claiming, 4 concurrent jobs, cancellation).
* **Invariants to Verify:** Zero race conditions; worker thread failures are isolated and never crash the process.
* **Acceptance Criteria:** Multiple jobs process in parallel without database locks or rate-limiter races.
* **Recommended Commit Message:** `feat(phase-8d): implement concurrent desktop job runtime and atomic claiming`

### Phase 8E: Persistent Scheduler & Startup Recovery
* **Objective:** Implement persistent job scheduling and startup recovery for interrupted jobs.
* **Prerequisites:** Phase 8D completed.
* **Files to Create:**
  - `interfaces/desktop/workers/scheduler.py` (`DesktopJobScheduler`).
* **Files to Modify:**
  - `application/services/job_recovery.py` (add `reconcile_stale_processing_jobs()`).
* **Tests to Add:**
  - `tests/unit/test_persistent_scheduler.py` (Scheduled job dispatch, catch-up, startup crash recovery).
* **Invariants to Verify:** Scheduled jobs survive process restart; past-due jobs are cleanly caught up.
* **Acceptance Criteria:** Scheduled jobs execute at target timestamps; crashed jobs recover to `PAUSED`.
* **Recommended Commit Message:** `feat(phase-8e): implement persistent scheduler and startup crash recovery`

### Phase 8F: PySide6 / QML Presentation Layer
* **Objective:** Implement the reactive Qt desktop shell, controllers, view models, and QML views.
* **Prerequisites:** Phase 8E completed.
* **Files to Create:**
  - `interfaces/desktop/app.py`
  - `interfaces/desktop/composition.py` (`DesktopAppContainer`).
  - `interfaces/desktop/notifier.py` (`QtSignalEventBridge`).
  - `interfaces/desktop/controllers/*.py` (Job, ApiKey, Prompt, Settings, QuickConvert).
  - `interfaces/desktop/models/*.py` (JobQueue, JobHistory, ApiSlot, PromptList).
  - `interfaces/desktop/qml/**/*.qml` (Main, Views, Components).
* **Tests to Add:**
  - `tests/unit/test_desktop_controllers.py` (Controller-to-Service delegation).
* **Invariants to Verify:** Zero business logic in controllers; QML imports 0 Python internals directly.
* **Acceptance Criteria:** Full desktop UI boots and operates smoothly with reactive progress.
* **Recommended Commit Message:** `feat(phase-8f): implement pyside6 qml desktop interface and controllers`

### Phase 8G: REST & Server Decommissioning
* **Objective:** Purge obsolete FastAPI/JWT infrastructure and clean dependencies.
* **Prerequisites:** Phase 8F completed.
* **Files to Delete:**
  - `interfaces/api/` (all files).
  - `infrastructure/security/jwt_token_service.py`.
  - `infrastructure/security/token_service.py`.
  - `application/ports/token_service.py`.
  - `application/services/auth_service.py`.
  - `tests/unit/test_phase7_desktop_rest_architecture.py`.
* **Files to Modify:**
  - `requirements.txt` (remove fastapi, uvicorn, python-multipart).
* **Tests to Verify:** Full test suite passes without FastAPI or server modules.
* **Acceptance Criteria:** Zero references to FastAPI or JWT remain in the codebase.
* **Recommended Commit Message:** `chore(phase-8g): decommission obsolete fastapi rest api and jwt auth`

### Phase 8H: Integration Testing & Architectural Hardening
* **Objective:** Final hardening, AST boundary test suite, and end-to-end local conversion verification.
* **Prerequisites:** Phase 8G completed.
* **Files to Create / Update:**
  - `tests/unit/test_desktop_architecture_invariants.py` (AST boundary tests).
  - `tests/integration/test_desktop_pipeline_e2e.py` (End-to-end multi-page conversion).
* **Tests to Run:** Complete test suite (`pytest`).
* **Acceptance Criteria:** 100% of unit, integration, and AST invariant tests pass.
* **Recommended Commit Message:** `chore(phase-8h): add desktop architecture invariant tests and e2e verification`

---

## 20. File-by-File Change Matrix

| File Path | Action | Description / Rationale |
| :--- | :--- | :--- |
| `core/entities/user.py` | **DELETE** | Replaced by `core/entities/settings.py`. |
| `core/entities/settings.py` | **NEW** | `AppSettings` entity for desktop runtime/UI configuration. |
| `core/entities/job.py` | **KEEP + MODIFY** | Add `scheduled_at`, `cancel_requested`, `claimed_at`; remove `user_id`. |
| `core/entities/api_slot.py` | **KEEP + MODIFY** | Purge `public`/`donated` slot types; standardize on `byok`. |
| `core/entities/credential_ref.py` | **KEEP** | Secret reference value object. |
| `core/entities/artifact.py` | **KEEP** | Artifact handles and types. |
| `core/entities/prompt.py` | **KEEP** | Prompts entity. |
| `core/policies/quota_policy.py` | **DELETE** | Server quotas are obsolete. |
| `core/policies/job_state_policy.py` | **KEEP + MODIFY** | Add `CANCELLED` status and transitions. |
| `core/policies/fallback_policy.py` | **KEEP** | API fallback chain resolution. |
| `core/policies/retry_policy.py` | **KEEP** | Auto-retry rules. |
| `core/ai/types.py` | **KEEP + MODIFY** | Delete duplicate `ApiSlot`; retain Vision/Text prompt requests and responses. |
| `core/ai/exceptions.py` | **KEEP** | Provider-neutral AI exceptions. |
| `application/events.py` | **NEW** | Transport-neutral application events. |
| `application/ports/repositories.py` | **KEEP + MODIFY** | Desktop SQLite repository interfaces (`IJobRepository`, etc.). |
| `application/ports/unit_of_work.py` | **KEEP + MODIFY** | SQLite Unit of Work interface. |
| `application/ports/credential_resolver.py`| **KEEP** | Port for resolving `CredentialRef` to raw key. |
| `application/ports/token_service.py` | **DELETE** | Obsolete (no JWT in desktop app). |
| `application/ports/notifier.py` | **KEEP + MODIFY** | Replaced by application event publisher interface. |
| `application/ports/rate_limiter.py` | **KEEP** | Rate limiter port. |
| `application/services/auth_service.py` | **DELETE** | Obsolete. |
| `application/services/user_service.py` | **REPLACE** | Replace with `LocalSettingsService`. |
| `application/services/api_service.py` | **REFACTOR** | Rename to `ApiKeyService` for BYOK Keyring management. |
| `application/services/job_submission.py` | **KEEP + MODIFY** | Ingest source PDF, copy to job artifact folder, support `scheduled_at`. |
| `application/services/job_execution.py` | **KEEP + MODIFY** | In-process execution with cooperative cancellation & event publishing. |
| `application/services/job_recovery.py` | **KEEP + MODIFY** | Desktop resume/retry and startup crash reconciliation. |
| `application/services/job_query.py` | **KEEP + MODIFY** | Query service for desktop UI models. |
| `infrastructure/persistence/sqlite/*` | **NEW** | SQLite WAL database manager, repositories, UoW, and migration runner. |
| `infrastructure/security/keyring_resolver.py` | **NEW** | OS Keyring adapter. |
| `infrastructure/security/encrypted_store.py` | **NEW** | Passphrase-derived PBKDF2/Fernet encrypted store fallback. |
| `infrastructure/rate_limiting/memory_rate_limiter.py` | **NEW** | Thread-safe memory rate limiter. |
| `interfaces/desktop/*` | **NEW** | PySide6 / QML desktop application, controllers, view models, workers. |
| `interfaces/api/*` | **DELETE** | Delete all FastAPI routes, app, dependencies, and OpenAPI code. |
| `interfaces/telegram/*` | **FREEZE** | Legacy transport adapter preserved without modification. |
| `services/worker.py` | **FREEZE / DELETE** | Replaced by `interfaces/desktop/workers/runtime.py`. |
| `utils/rate_limiter.py` | **DELETE** | Replaced by `memory_rate_limiter.py`. |

---

## 21. Risks and Mitigations

| Risk | Severity | Mitigation Strategy |
| :--- | :--- | :--- |
| **OS Keyring Fails in Headless/Linux** | High | Automatic fallback to `EncryptedFileCredentialStore` with PBKDF2 passphrase encryption. |
| **SQLite Lock Contention Under 4+ Workers** | High | WAL mode (`PRAGMA journal_mode=WAL;`), 5000ms busy timeout, and thread-isolated connections. |
| **GUI Thread Hangs During PDF/AI Calls** | High | Strictly delegate all rendering and AI calls to `QRunnable` worker threads in `QThreadPool`. |
| **Original PDF Moved During Processing** | Medium | `JobSubmissionService` copies source PDF into `artifacts/job_{id}/source_document.pdf` on submission. |
| **Stale Jobs Left in Processing After Crash** | Medium | Startup crash reconciler automatically resets un-owned `processing` jobs to `paused` on app boot. |
| **Architectural Boundary Erosion** | High | Strict automated AST invariant tests in `test_desktop_architecture_invariants.py`. |

---

## 22. Acceptance Criteria for Phase 8

1. **Zero Server / Network Dependencies:** The application boots, executes PDF jobs, and stores artifacts with zero HTTP listeners, zero background server processes, zero JWT tokens, and zero external DB daemons.
2. **BYOK Security:** API keys are stored in OS Keyring or encrypted vault; zero plaintext keys exist in SQLite tables, DTO logs, or serialized entities.
3. **100% In-Process PySide6 UI:** The QML desktop interface displays live job progress, allows API key setup, prompt editing, and one-click artifact export without HTTP polling.
4. **Concurrency Safety:** At least 4 PDF jobs can be processed simultaneously on background threads without SQLite lock errors or rate-limiter races.
5. **Persistent Scheduling:** A scheduled job survives application restart and executes when due.
6. **Architectural Purity:** Automated AST tests verify that `core/` and `application/` contain 0 imports from `PySide6`, `Qt`, `sqlite3`, `keyring`, `fastapi`, or `telegram`.
7. **Passing Test Suite:** All converted, migrated, and newly introduced unit, integration, and architecture tests pass cleanly.

---

## 23. Recommended Git Checkpoint Structure

```
chore(phase-8a): reconcile domain entities and application ports for desktop
feat(phase-8b): implement sqlite wal persistence and repositories
feat(phase-8c): implement os keyring credential resolver and byok security
feat(phase-8d): implement concurrent desktop job runtime and atomic claiming
feat(phase-8e): implement persistent scheduler and startup crash recovery
feat(phase-8f): implement pyside6 qml desktop interface and controllers
chore(phase-8g): decommission obsolete fastapi rest api and jwt auth
chore(phase-8h): add desktop architecture invariant tests and e2e verification
```

---

## 24. Reconciled Architectural Summary & Next Steps

### 24.1 Summary of Final Architecture
- **Desktop Application:** Single-user, embedded, local-first software.
- **Frontend:** PySide6 + QML (MVVM / Controller pattern) running in the main GUI thread.
- **Application Core:** Pure Python use cases and domain entities communicating via in-process DTOs and typed Application Events.
- **Storage & State:** SQLite in WAL mode with atomic `BEGIN IMMEDIATE` job claiming and thread-local connections.
- **Credentials:** BYOK stored in OS Keyring with user-passphrase PBKDF2/Fernet fallback.
- **Concurrency:** `QThreadPool` worker execution supporting 4+ concurrent jobs with cooperative cancellation.

### 24.2 Key Assumptions
1. Target operating systems for PolpoT Desktop are **Windows 10/11**, **macOS 12+**, and **modern Linux distributions** (with Secret Service / DBus or fallback passphrase).
2. The user has Python 3.10+ and a compatible Qt6/PySide6 runtime environment.
3. All AI provider calls require direct outbound internet access via HTTPS (TLS 1.3).

### 24.3 Architectural Decisions Requiring Explicit User Approval
1. **Passphrase KDF Fallback:** Confirmation of the explicit user-controlled passphrase + PBKDF2 (600,000 iterations) fallback when OS Keyring is unavailable.
2. **Decommissioning Sequence:** Confirmation that `interfaces/api/` and JWT modules are deleted in Phase 8G after Desktop Presentation is established in Phase 8F.
3. **Source PDF Ingestion:** Confirmation that source PDFs are copied into the local `artifacts/job_{id}/` directory to ensure crash recovery resilience.

### 24.4 Exact First Implementation Step for Phase 8A
Once approved, the very first implementation step is **Phase 8A: Architecture Reconciliation & Domain Foundations**:
1. Create `core/entities/settings.py` (`AppSettings`).
2. Create `application/events.py` (Application Event dataclasses).
3. Modify `core/entities/api_slot.py` and `core/entities/job.py`.
4. Delete `core/policies/quota_policy.py` and remove duplicate `ApiSlot` in `core/ai/types.py`.
5. Update `application/ports/repositories.py` and `application/ports/unit_of_work.py`.
6. Verify and run domain unit tests.
