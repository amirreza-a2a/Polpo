# Phase 8B — SQLite Persistence & Migration Implementation Plan (Remediated)

**Target Architecture:** Desktop-First, Local-First, Single-User, Embedded, Serverless
**Repository State:** Checkpoint Commit `975a7a3` (Phase 8A Completed & Verified, 170/170 Tests Passing)
**Authoritative Specifications:** `AGENTS.md` and `phase_8_desktop_architecture_plan.md`

---

## 1. Executive Summary

Phase 8B establishes the **canonical SQLite persistence engine** for PolpoT Desktop. It provides an embedded, zero-configuration, thread-safe, transactional SQLite database operating in **Write-Ahead Logging (WAL)** mode.

### Core Objectives
1. **Single-User Desktop Persistence:** Fast, local, ACID-compliant storage for application settings, PDF conversion jobs, Pipeline 2 refinement jobs, system/custom prompts, and BYOK AI slots.
2. **Genuinely Atomic Schema Migrations:** A robust migration runner executing versioned SQL migration scripts within explicit database transactions, guaranteeing that schema modifications and version tracking commit or roll back together atomically.
3. **Optimized Concurrency & Transaction Boundaries:** Standard Unit of Work operations use normal deferred transactions, while write-critical job claim operations use dedicated, sub-millisecond `BEGIN IMMEDIATE` transactions to prevent duplicate execution across parallel workers without starving concurrent readers.
4. **Clean Transaction Boundaries Around Expensive Work:** No AI network calls, PDF rendering, or file system-heavy operations occur inside active SQLite write transactions.
5. **Deterministic Non-Persistence of Secrets:** Verifiable non-persistence of API credentials; SQLite stores only `credential_identifier` and `CredentialRef` metadata.

---

## 2. Current Repository Audit & Baseline Checkpoint

### 2.1 Verified Clean State at HEAD (`975a7a3`)
- **Domain Entities:** `Job`, `Pipeline2Job`, `AppSettings`, `Prompt`, `ApiSlot`, `CredentialRef` are fully reconciled and free of `user_id`, server quotas, and raw secret attributes.
- **Canonical Ports:** `application/ports/repositories.py` and `application/ports/unit_of_work.py` define the clean desktop contract with 5 canonical repositories (`settings`, `jobs`, `pipeline2_jobs`, `prompts`, `apis`).
- **Test Suite:** 170 passing tests (100% pass rate).

### 2.2 Component Classification & Boundary Isolation

| Existing File / Component | Classification | Action for Phase 8B |
| :--- | :--- | :--- |
| `infrastructure/persistence/connection.py` | Legacy MySQL Pool | **Isolate for Legacy.** Retained strictly for frozen Telegram compatibility; SQLite engine is built independently under `infrastructure/persistence/sqlite/`. |
| `infrastructure/persistence/migration_runner.py` | Legacy MySQL Runner | **Isolate for Legacy.** Retained for legacy MySQL table management. |
| `infrastructure/persistence/repositories.py` | Legacy MySQL Repos | **Isolate for Legacy.** Retained for frozen Telegram compatibility. |
| `infrastructure/persistence/unit_of_work.py` | Legacy MySQL UoW | **Isolate for Legacy.** Retained for frozen Telegram compatibility. |
| `infrastructure/storage/local_storage.py` | Canonical Storage Adapter | **Reuse.** Manages immutable source documents, cropped images, and Markdown artifacts on local disk. |

---

## 3. Canonical vs. Legacy Persistence Boundary

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CANONICAL DESKTOP PATH                              │
│                                                                             │
│   interfaces/desktop/ (PySide6 / QML ViewModels & Controllers)              │
│          │                                                                  │
│          ▼                                                                  │
│   application/services/ (JobSubmission, JobExecution, LocalSettings, etc.)  │
│          │                                                                  │
│          ▼                                                                  │
│   application/ports/ (IUnitOfWork, IJobRepository, ISettingsRepository, etc.) │
│          │                                                                  │
│          ▼ (implemented by)                                                 │
│   infrastructure/persistence/sqlite/                                        │
│     ├── SQLiteDatabaseManager (WAL, Busy Timeout, Thread Isolation)         │
│     ├── SQLiteMigrationRunner (migrations/001_initial_schema.sql)           │
│     ├── SQLiteUnitOfWork & SQLiteUnitOfWorkFactory (Deferred Transactions)  │
│     └── SQLiteRepositories (Settings, Jobs, P2Jobs, Prompts, ApiSlots)      │
│          │                                                                  │
│          ▼                                                                  │
│   ~/.local/share/polpot/polpot.db (Local SQLite Database)                  │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                   FROZEN LEGACY TELEGRAM / MYSQL BOUNDARY                   │
│                                                                             │
│   interfaces/telegram/ + handlers/                                          │
│          │                                                                  │
│          ▼                                                                  │
│   infrastructure/persistence/ (MySQL repositories, connection pool)         │
│          │                                                                  │
│          ▼                                                                  │
│   Remote MySQL Server / Legacy Telegram Data                                │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Proposed SQLite Architecture & Directory Structure

Authoritative file structure:

```text
infrastructure/persistence/sqlite/
├── migrations/
│   └── 001_initial_schema.sql   # Canonical initial DDL, FKs, and indexes
├── __init__.py
├── connection.py                 # SQLiteDatabaseManager & Connection Factory
├── migration_runner.py           # SQLiteMigrationRunner (Atomic Migration Engine)
├── repositories.py               # SQLiteSettingsRepo, SQLiteJobRepo, SQLiteP2Repo,
│                                 # SQLitePromptRepo, SQLiteApiSlotRepo
└── unit_of_work.py               # SQLiteUnitOfWork & SQLiteUnitOfWorkFactory
```

---

## 5. Connection & Threading Model

### 5.1 Concurrency Strategy
- **WAL Mode (`journal_mode = WAL`):** Enables simultaneous multi-reader execution without blocking writers, and writer execution without blocking readers.
- **`busy_timeout = 5000` ms:** Automatically queues database write operations if another writer is temporarily committing, eliminating transient `database is locked` errors.
- **Connection Isolation:** Each `SQLiteUnitOfWork` instance creates and owns an independent `sqlite3.Connection`, closing it cleanly in `__exit__`. Connections are never shared across concurrent threads.

```python
def _configure_connection(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    return conn
```

---

## 6. Schema Design (`migrations/001_initial_schema.sql`)

Foreign-key dependencies are topologically ordered: `prompts` is defined before `app_settings` and `jobs`; `jobs` is defined before `pipeline2_jobs`.

```sql
-- ============================================================
--  PolpoT Desktop SQLite Initial Schema (001_initial_schema.sql)
-- ============================================================

-- 1. Schema Version Tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

-- 2. System and User Prompts
CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    text TEXT NOT NULL,
    prompt_type TEXT NOT NULL CHECK(prompt_type IN ('pipeline1', 'pipeline2', 'quick_convert')),
    is_default INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 3. Singleton Application Settings
CREATE TABLE IF NOT EXISTS app_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    theme TEXT NOT NULL DEFAULT 'system' CHECK(theme IN ('system', 'dark', 'light')),
    max_concurrent_jobs INTEGER NOT NULL DEFAULT 2 CHECK(max_concurrent_jobs BETWEEN 1 AND 8),
    auto_retry INTEGER NOT NULL DEFAULT 1 CHECK(auto_retry IN (0, 1)),
    auto_pipeline2 INTEGER NOT NULL DEFAULT 0 CHECK(auto_pipeline2 IN (0, 1)),
    default_prompt_id INTEGER,
    default_pipeline2_prompt_id INTEGER,
    artifact_retention_days INTEGER NOT NULL DEFAULT 30 CHECK(artifact_retention_days >= 1),
    missed_schedule_policy TEXT NOT NULL DEFAULT 'prompt' CHECK(missed_schedule_policy IN ('run_immediately', 'prompt', 'mark_paused')),
    updated_at TEXT NOT NULL,
    FOREIGN KEY(default_prompt_id) REFERENCES prompts(id) ON DELETE SET NULL,
    FOREIGN KEY(default_pipeline2_prompt_id) REFERENCES prompts(id) ON DELETE SET NULL
);

-- 4. AI Provider Slots (BYOK Metadata Only — Secrets in OS Keyring)
CREATE TABLE IF NOT EXISTS api_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL CHECK(provider IN ('google', 'openai', 'openrouter', 'custom')),
    label TEXT NOT NULL,
    credential_identifier TEXT NOT NULL UNIQUE,
    slot_type TEXT NOT NULL DEFAULT 'byok' CHECK(slot_type IN ('byok', 'custom')),
    selected_model TEXT,
    base_url TEXT,
    supported_models TEXT,                      -- JSON array of strings
    priority INTEGER NOT NULL DEFAULT 1,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    total_pages_processed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 5. Primary PDF Document Conversion Jobs
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,                    -- Ingested source PDF URI/path
    total_pages INTEGER NOT NULL DEFAULT 0,
    processed_pages INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK(status IN ('pending', 'processing', 'done', 'paused', 'failed', 'cancelled')),
    prompt_id INTEGER,
    prompt_text TEXT,
    api_chain TEXT,                             -- JSON array of ApiSlot descriptors (NO SECRETS)
    current_api_index INTEGER NOT NULL DEFAULT 0,
    api_switch_log TEXT,                        -- JSON array of switch events
    output_path TEXT,                           -- Output markdown artifact URI/path
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    auto_pipeline2 INTEGER NOT NULL DEFAULT 0 CHECK(auto_pipeline2 IN (0, 1)),
    pipeline2_prompt_id INTEGER,
    scheduled_at TEXT,                          -- ISO-8601 UTC timestamp string
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0, 1)),
    claimed_at TEXT,                            -- ISO-8601 UTC timestamp string
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(prompt_id) REFERENCES prompts(id) ON DELETE SET NULL,
    FOREIGN KEY(pipeline2_prompt_id) REFERENCES prompts(id) ON DELETE SET NULL
);

-- 6. Pipeline 2 Typography Refinement Jobs
CREATE TABLE IF NOT EXISTS pipeline2_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_job_id INTEGER NOT NULL,
    prompt_id INTEGER,
    prompt_text TEXT,
    status TEXT NOT NULL CHECK(status IN ('pending', 'processing', 'done', 'paused', 'failed', 'cancelled')),
    input_path TEXT,
    output_path TEXT,
    api_chain TEXT,                             -- JSON array (NO SECRETS)
    current_api_index INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0, 1)),
    claimed_at TEXT,                            -- ISO-8601 UTC timestamp string
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(source_job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    FOREIGN KEY(prompt_id) REFERENCES prompts(id) ON DELETE SET NULL
);

-- 7. Performance Indexes
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_scheduled ON jobs(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_p2_jobs_status ON pipeline2_jobs(status);
CREATE INDEX IF NOT EXISTS idx_p2_jobs_source ON pipeline2_jobs(source_job_id);
CREATE INDEX IF NOT EXISTS idx_prompts_type_default ON prompts(prompt_type, is_default);
CREATE INDEX IF NOT EXISTS idx_api_slots_active ON api_slots(is_active, priority);
```

---

## 7. Migration Strategy & Atomic Execution

### 7.1 Flaw in `executescript()` & Remediation
- **Flaw:** Standard `sqlite3.Connection.executescript()` automatically issues a `COMMIT` before executing scripts, breaking enclosing transactions and leaving partially failed scripts committed.
- **Remediation:** `SQLiteMigrationRunner` parses migration scripts into individual SQL statements and executes them sequentially within an explicit `BEGIN IMMEDIATE` transaction. If any DDL statement fails, a `ROLLBACK` is issued immediately. `schema_version` is updated **only** within that same transaction before final `COMMIT`.

```python
class SQLiteMigrationRunner:
    def __init__(self, db_manager: SQLiteDatabaseManager, migrations_dir: Optional[Path] = None):
        self.db_manager = db_manager
        self.migrations_dir = migrations_dir or (Path(__file__).parent / "migrations")

    def run_migrations(self) -> List[int]:
        conn = self.db_manager.create_connection()
        try:
            # Ensure schema_version table exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );
            """)
            conn.commit()

            cur = conn.cursor()
            cur.execute("SELECT version FROM schema_version")
            applied = {row[0] for row in cur.fetchall()}

            applied_this_run = []
            for migration_file in self.discover_migrations():
                version = int(migration_file.name.split("_")[0])
                if version in applied:
                    continue

                with open(migration_file, "r", encoding="utf-8") as f:
                    content = f.read()

                statements = [s.strip() for s in content.split(";") if s.strip()]

                # Atomic execution per migration file
                conn.execute("BEGIN IMMEDIATE")
                try:
                    for stmt in statements:
                        conn.execute(stmt)
                    now_utc = datetime.now(timezone.utc).isoformat()
                    conn.execute(
                        "INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
                        (version, migration_file.name, now_utc),
                    )
                    conn.execute("COMMIT")
                    applied.add(version)
                    applied_this_run.append(version)
                except Exception as e:
                    conn.execute("ROLLBACK")
                    raise MigrationError(f"Migration {migration_file.name} failed: {e}") from e

            return applied_this_run
        finally:
            conn.close()
```

---

## 8. Repository Mapping & Contract Conformance

### 8.1 Conformance to Canonical Desktop Ports
All SQLite repositories implement **only** the canonical desktop methods defined in `application/ports/repositories.py`:

```
┌───────────────────────────┬────────────────────────────────────────────────────────┐
│ Repository Class          │ Canonical Interface Implemented                        │
├───────────────────────────┼────────────────────────────────────────────────────────┤
│ SQLiteSettingsRepository  │ ISettingsRepository (get, save)                        │
│ SQLitePromptRepository    │ IPromptRepository (get_by_id, get_default, list_all,   │
│                           │ save, delete, set_default, toggle_active, quick_prompt) │
│ SQLiteApiSlotRepository   │ IApiRepository (get_by_id, list_all, save, delete,      │
│                           │ report_pages_used)                                     │
│ SQLiteJobRepository       │ IJobRepository (get_by_id, get_next_pending, save,     │
│                           │ update_progress, update_status, get_queue_position,    │
│                           │ list, claim_job, claim_next_pending, get_due_jobs,     │
│                           │ request_cancellation, reconcile_stale_jobs)             │
│ SQLitePipeline2JobRepository IPipeline2JobRepository (get_by_id, get_next_pending,  │
│                           │ save, update_status, update_paths, update_output_path) │
└───────────────────────────┴────────────────────────────────────────────────────────┘
```

Legacy methods required strictly for frozen Telegram compatibility (`list_by_user`, `count_by_user`, `delete_private`) are implemented as clean transparent delegations to canonical desktop queries (e.g. `list_by_user(...) -> self.list(...)`).

---

## 9. Unit of Work & Transaction Boundaries

### 9.1 Standard UoW with Deferred Transactions
To maximize reader concurrency in WAL mode, `SQLiteUnitOfWork` uses normal **deferred transaction semantics** (`BEGIN` on write) rather than acquiring immediate write locks upon entry.

```python
class SQLiteUnitOfWork(IUnitOfWork):
    """
    Unit of Work managing transaction lifecycle for normal desktop operations.
    Uses deferred transaction semantics to allow concurrent readers without lock contention.
    """
    def __init__(self, db_manager: SQLiteDatabaseManager):
        self.db_manager = db_manager
        self._conn: Optional[sqlite3.Connection] = None
        self._in_transaction = False

    def __enter__(self) -> "SQLiteUnitOfWork":
        self._conn = self.db_manager.create_connection()
        # Normal deferred transaction (begins upon first DML execution)
        self._in_transaction = True

        self.settings = SQLiteSettingsRepository(self._conn)
        self.jobs = SQLiteJobRepository(self._conn)
        self.pipeline2_jobs = SQLitePipeline2JobRepository(self._conn)
        self.prompts = SQLitePromptRepository(self._conn)
        self.apis = SQLiteApiSlotRepository(self._conn)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            if self._in_transaction:
                if exc_type is not None:
                    self.rollback()
                else:
                    self.commit()
        finally:
            if self._conn:
                self._conn.close()
                self._conn = None

    def commit(self) -> None:
        if self._conn and self._in_transaction:
            self._conn.commit()
            self._in_transaction = False

    def rollback(self) -> None:
        if self._conn and self._in_transaction:
            self._conn.rollback()
            self._in_transaction = False
```

---

## 10. Atomic Job Claiming Design

### 10.1 Scoped `BEGIN IMMEDIATE` Claiming Transaction
Atomic job claiming is implemented with a **dedicated, sub-millisecond `BEGIN IMMEDIATE` transaction** scoped strictly to the claim operation.

```python
def claim_next_pending(self, due_before: Optional[datetime] = None) -> Optional[Job]:
    """
    Atomically claims the next eligible pending job.
    Uses a dedicated BEGIN IMMEDIATE transaction to guarantee zero duplicate claims.
    """
    due_limit = (due_before or datetime.now(timezone.utc)).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    # Acquire immediate write lock
    self.conn.execute("BEGIN IMMEDIATE")
    try:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT id FROM jobs
            WHERE status = 'pending'
              AND cancel_requested = 0
              AND (scheduled_at IS NULL OR scheduled_at <= ?)
            ORDER BY id ASC
            LIMIT 1
            """,
            (due_limit,)
        )
        row = cur.fetchone()
        if not row:
            self.conn.execute("COMMIT")
            return None

        job_id = row["id"]
        cur.execute(
            """
            UPDATE jobs
            SET status = 'processing',
                claimed_at = ?,
                updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (now_iso, now_iso, job_id)
        )
        if cur.rowcount != 1:
            self.conn.execute("ROLLBACK")
            return None  # Claimed concurrently

        self.conn.execute("COMMIT")
        return self.get_by_id(job_id)
    except Exception:
        self.conn.execute("ROLLBACK")
        raise
```

### 10.2 Proof of Concurrency Safety
1. Worker 1 executes `BEGIN IMMEDIATE` $\to$ reserved lock acquired.
2. Worker 2 attempts `BEGIN IMMEDIATE` $\to$ queued by `busy_timeout` (5000 ms).
3. Worker 1 selects Job #10, updates `status = 'processing'`, and commits $\to$ lock released (< 1 ms duration).
4. Worker 2's `BEGIN IMMEDIATE` unblocks, selects next pending job $\to$ Job #10 is already `processing`, so Worker 2 receives Job #11.
5. Zero duplicate execution.

---

## 11. Transaction Boundaries & Non-Blocking Workflow

### 11.1 Workload Transaction Matrix
| Operation Type | Transaction Style | Duration | Concurrency Impact |
| :--- | :--- | :--- | :--- |
| **Settings / Prompts / Queue Queries** | Shared Read Lock (WAL) | < 1 ms | Readers do not block any operations |
| **Job Submission / Status Updates** | Normal Deferred Write | < 2 ms | Brief writer lock during commit only |
| **Atomic Job Claiming** | Scoped `BEGIN IMMEDIATE` | < 1 ms | Minimal writer lock during claim update |
| **PDF Page Rendering / Image Cropping** | **Zero DB Transaction** | 50–200 ms | Done in memory/disk outside any DB lock |
| **AI OCR / Text API Network Requests** | **Zero DB Transaction** | 1–15 s | Done via HTTP outside any DB lock |

---

## 12. Timestamp & Serialization Policy

- **Timestamps:** Timezone-aware `datetime(..., tzinfo=timezone.utc)` $\leftrightarrow$ ISO-8601 UTC string (`2026-09-01T13:15:00.000000Z`).
- **Booleans:** `bool` $\leftrightarrow$ `INTEGER` (`0` or `1`).
- **JSON Fields:** `api_chain` and `api_switch_log` serialized as JSON strings without raw secrets.

---

## 13. Security & Secret Non-Persistence Verification

- **Policy:** SQLite database contains zero plaintext API keys.
- **Deterministic Synthetic Secret Test:**
  The test suite registers an API slot using a distinct synthetic test secret:
  `TEST_SYNTHETIC_SECRET_PHASE8B_9A7B3C1D`
  The test commits the entity, then performs a full byte/string scan across all SQLite rows, JSON blobs, and table columns.
  **Assertion:** `TEST_SYNTHETIC_SECRET_PHASE8B_9A7B3C1D` is **NOT FOUND** anywhere in the database.

---

## 14. Comprehensive Test Plan

### 14.1 Unit Test Suite (`tests/unit/test_sqlite_persistence.py`)
1. `test_sqlite_wal_mode_and_pragmas`: Verifies `journal_mode=wal`, `busy_timeout=5000`, `foreign_keys=ON`.
2. `test_atomic_migration_runner_success`: Applies `001_initial_schema.sql` on fresh DB, verifies all tables, indexes, and version tracking.
3. `test_atomic_migration_runner_rollback_on_failure`: Injects broken SQL in a migration step $\to$ verifies complete rollback and schema remains clean.
4. `test_settings_repository_crud`: Verifies singleton `app_settings` defaults and updates.
5. `test_prompt_repository_crud`: Verifies prompts CRUD and default flags.
6. `test_api_slot_repository_crud`: Verifies BYOK slot persistence with `CredentialRef` and zero `api_key`.
7. `test_job_repository_crud`: Verifies job creation, progress updates, list pagination, and JSON serialization.
8. `test_pipeline2_job_repository_crud`: Verifies P2 job creation with foreign key cascade to source job.
9. `test_unit_of_work_transaction_commit_and_rollback`: Tests commit visibility and rollback on exception.
10. `test_reconcile_stale_jobs_on_startup`: Verifies `processing` jobs transition to `paused`.
11. `test_synthetic_secret_non_persistence_audit`: Full database scan proving `TEST_SYNTHETIC_SECRET_PHASE8B_9A7B3C1D` is never written to SQLite.

### 14.2 Concurrency Test Suite (`tests/unit/test_sqlite_concurrency.py`)
1. `test_multi_worker_atomic_claiming_no_duplicates`:
   - Enqueues 10 pending jobs.
   - Launches 5 concurrent threads synchronized via `threading.Barrier`.
   - Each worker loops claiming jobs via `claim_next_pending()`.
   - **Assertions:** Exactly 10 jobs claimed, 0 duplicate claims across workers, all 10 jobs have status `processing`.
2. `test_claim_job_direct_atomicity`:
   - Two workers attempt to claim the same `job_id` concurrently.
   - **Assertions:** Exactly one worker succeeds; the second worker receives `None`.

---

## 15. Implementation Sequence & Next Steps

1. Create `infrastructure/persistence/sqlite/migrations/001_initial_schema.sql`.
2. Create `infrastructure/persistence/sqlite/connection.py` (`SQLiteDatabaseManager`).
3. Create `infrastructure/persistence/sqlite/migration_runner.py` (`SQLiteMigrationRunner`).
4. Create `infrastructure/persistence/sqlite/repositories.py` (5 canonical SQLite repositories).
5. Create `infrastructure/persistence/sqlite/unit_of_work.py` (`SQLiteUnitOfWork` & Factory).
6. Create `tests/unit/test_sqlite_persistence.py` and `tests/unit/test_sqlite_concurrency.py`.
7. Verify all 170 baseline tests + all new SQLite tests pass (100% pass rate).
8. Verify `git diff --check` is completely clean.
9. Commit with `feat(phase-8b): implement sqlite wal persistence and repositories`.
