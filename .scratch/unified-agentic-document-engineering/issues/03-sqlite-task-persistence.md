# TICK-ADE-03: SQLite Task Repository, Allowlisted Journaling Schema & Conservative Startup Recovery

- **Ticket ID:** `TICK-ADE-03`
- **Title:** SQLite Task Repository, Allowlisted Journaling Schema & Conservative Startup Recovery
- **Milestone:** Milestone A (Agent Foundations)
- **Priority:** P1
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Scope:** Implement persistent SQLite storage and allowlist-governed execution journaling for Agent tasks in `infrastructure/persistence/sqlite/agent_task_repository.py`, migration runner integration for `006_agent_task_tables.sql`, and conservative startup recovery.
- **Explicit Non-Goals:**
  - No storing raw, unredacted document contents in SQLite (documents reside in version files or temporary scratchpads).
  - No storing plaintext API credentials, full AI prompts, or full completions in task journals.
  - No automated re-execution of interrupted tasks on startup.
- **Prerequisites:** `TICK-ADE-01` completed; ADR-007 approved.
- **Dependencies (Blocked By):** `TICK-ADE-01`.
- **Unblocks:** `TICK-ADE-04`, `TICK-ADE-14`.

---

## 1. Existing Source-Code Integration Points

- `infrastructure/persistence/sqlite/connection.py`: Worker-local connection management under SQLite WAL mode.
- `infrastructure/persistence/sqlite/migration_runner.py`: Versioned migration execution and `schema_version` tracking.
- `infrastructure/persistence/sqlite/repositories.py`: Repository pattern precedent using Unit of Work.
- `application/services/job_recovery_service.py`: Startup recovery precedent using instance locks.

---

## 2. Proposed Persistence Schema (Migration 006) & DTOs

### 2.1 SQL Schema (`infrastructure/persistence/sqlite/migrations/006_agent_task_tables.sql`)
```sql
CREATE TABLE IF NOT EXISTS agent_tasks (
    task_id TEXT PRIMARY KEY,
    job_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    task_goal TEXT NOT NULL,
    plan_json TEXT,
    current_step_index INTEGER DEFAULT 0,
    total_steps INTEGER DEFAULT 0,
    working_copy_id TEXT,
    failure_id TEXT,
    error_message TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    completed_at REAL,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_agent_tasks_job_id ON agent_tasks(job_id);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_status ON agent_tasks(status);

CREATE TABLE IF NOT EXISTS agent_task_journal (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    step_index INTEGER,
    event_type TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_agent_journal_task_id ON agent_task_journal(task_id);

CREATE TABLE IF NOT EXISTS publication_style_profiles (
    profile_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    description TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1,
    is_builtin INTEGER NOT NULL DEFAULT 0,
    profile_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
```

### 2.2 Application Port (`application/ports/agent_task_repository.py`)
```python
class IAgentTaskRepository(ABC):
    @abstractmethod
    def save_task(self, task: AgentTask) -> None:
        pass

    @abstractmethod
    def get_task(self, task_id: str) -> Optional[AgentTask]:
        pass

    @abstractmethod
    def list_tasks_for_job(self, job_id: int) -> List[AgentTask]:
        pass

    @abstractmethod
    def append_journal_entry(
        self,
        task_id: str,
        step_index: Optional[int],
        event_type: str,
        metadata: Dict[str, Any]
    ) -> None:
        pass

    @abstractmethod
    def reconcile_interrupted_tasks(self) -> List[str]:
        pass

    @abstractmethod
    def purge_expired_tasks(self, retention_days: int = 30) -> int:
        """Purges terminal and interrupted tasks older than retention_days from completed_at."""
        pass
```

---

## 3. Detailed Implementation Requirements

1. **Migration Sequence (Finding I):**
   - The file MUST be named `006_agent_task_tables.sql` because existing migrations run through 005 (`005_pipeline2_source_version.sql`).
2. **Complete Persistent-Data Privacy Boundary (Finding A):**
   - **Default Policy:** Persist lifecycle metadata, not document content or unrestricted model context.
   - **Field Audit & Policy Across All Tables:**

   | Table | Field | Data Classification | Sensitive Content Possible? | Persistence Policy | Validation & Bounds | Retention |
   | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
   | `agent_tasks` | `task_id` | System Identifier | No | Canonical primary key | UUID4 format check | 30 days from `completed_at` |
   | `agent_tasks` | `job_id` | Foreign Key | No | References `jobs(id)` | Positive integer | Cascade with parent job |
   | `agent_tasks` | `status` | State Enum | No | Current `TaskStatus` | Allowlisted enum | 30 days from `completed_at` |
   | `agent_tasks` | `task_goal` | Lifecycle Label | **Yes** (if raw request stored) | **Short display label / classification only**. Never store raw user request when containing document fragments. | Sanitized string, max 256 chars | 30 days from `completed_at` |
   | `agent_tasks` | `plan_json` | Structured Plan | **Yes** (if model emits raw text) | **Strictly validated safe plan schema only**. Allowlisted tool names and arguments; zero document text, prompts, completions, or secrets. | JSON schema validation against `AgentPlanSchema`, max 16 KB | 30 days from `completed_at` |
   | `agent_tasks` | `current_step_index` | Execution Counter | No | Monotonic counter | `0 <= index <= total_steps` | 30 days from `completed_at` |
   | `agent_tasks` | `total_steps` | Execution Bound | No | Bounded plan size | `0 <= steps <= 10` | 30 days from `completed_at` |
   | `agent_tasks` | `working_copy_id` | Ephemeral Scratchpad ID | No | Opaque UUID referencing `.polpo/working_copies/{id}/` | UUID4 format; content ephemeral on disk | 30 days in DB; folder purged on task end |
   | `agent_tasks` | `failure_id` | Ephemeral Diagnostic ID | No | Opaque UUID referencing `.polpo/staging_failures/{id}/` | UUID4 format; bundle managed by LRU | 30 days in DB; bundle pruned via LRU |
   | `agent_tasks` | `error_message` | Bounded Diagnostic Category | **Yes** (if raw logs stored) | **Sanitized English error category or bounded summary**. Never store raw traces or paths. | Redacted by `TexLogSanitizer`, max 512 chars | 30 days from `completed_at` |
   | `agent_tasks` | `created_at`, `updated_at` | Timestamps | No | Epoch modification times | Real/float >= 0 | 30 days from `completed_at` |
   | `agent_tasks` | `completed_at` | Terminal Timestamp | No | Epoch terminal completion time; baseline for retention window | Real/float >= `created_at` (NULL for active tasks) | 30 days from `completed_at` |
   | `agent_task_journal` | `entry_id` | Sequence ID | No | Auto-increment primary key | Positive integer | Cascade deleted with task |
   | `agent_task_journal` | `task_id` | Foreign Key | No | References `agent_tasks(task_id)` | UUID4 with `ON DELETE CASCADE` | Cascade deleted with task |
   | `agent_task_journal` | `step_index` | Execution Step | No | Step index in plan | Optional integer `0 <= step <= 10` | Cascade deleted with task |
   | `agent_task_journal` | `event_type` | Audit Event Enum | No | Typed audit event | Allowlisted string enum | Cascade deleted with task |
   | `agent_task_journal` | `recorded_at` | Timestamp | No | Microsecond epoch timestamp | Real/float >= 0 | Cascade deleted with task |
   | `agent_task_journal` | `metadata_json` | Strictly Allowlisted Audit Record | **Yes** (if arbitrary keys allowed) | **Strict Allowlist only**. Max 8 KB per entry; max 50 entries per task. Plain content hashes prohibited. | Schema validation against `JournalMetadataAllowlist`, max 8 KB | Cascade deleted with task |
   | `publication_style_profiles` | `profile_id` | Profile Identifier | No | Key (`builtin:{name}` or `user:{uuid}`) | String, max 64 chars, regex `^(builtin:[a-z0-9_-]+|user:[0-9a-f-]{36})$` | Permanent (built-in) / user-managed |
   | `publication_style_profiles` | `display_name`, `description` | Presentation Meta | No | Human-readable title and summary | Strings (max 128 / 512 chars) | User-managed |
   | `publication_style_profiles` | `schema_version`, `is_builtin` | Schema & Type | No | Version and immutability flag | Integers | User-managed |
   | `publication_style_profiles` | `profile_json` | Declarative Profile Spec | **Yes** (if raw macros injected) | **Strictly validated declarative typesetting JSON**. Prohibit raw LaTeX macros, `header-includes`, shell commands, executable code. | Validated against `PublicationStyleProfileSchema`, max 16 KB | User-managed |
   | `publication_style_profiles` | `created_at`, `updated_at` | Timestamps | No | Epoch modification times | Real/float >= 0 | User-managed |

   - **Journal Allowlist Keys (`metadata_json`):** `task_id`, `step_index`, `event_type`, `recorded_at`, `duration_ms`, `tool_name`, `step_correlation_id` (opaque UUID; dictionary attack defense), `status`, `sanitized_error_category`, `consent_token_ref` (single-use UUID reference), `approval_token_ref` (single-use UUID reference), `summary` (generic non-sensitive status <= 256 chars; zero document text), `request_disposition`.
   - **Persistence Denylist:** Complete user requests with source text, full Markdown drafts, working copies, prompts, completions, unvalidated plans, tool args with source text, unredacted compiler diagnostics, `document.tex`, local paths, credentials, unredacted exceptions.

3. **Exhaustive Startup Recovery & Token Invalidation (Finding B):**
   - `reconcile_interrupted_tasks()` scans `agent_tasks` under `BEGIN IMMEDIATE` across **all non-terminal states**: `CREATED`, `CONTEXT_GATHERING`, `PLANNING`, `AWAITING_PLAN_APPROVAL`, `RUNNING`, `AWAITING_CONSENT`, `AWAITING_ACTION_APPROVAL`, `PAUSED`, `VALIDATING`, `AWAITING_FINAL_APPROVAL`.
   - Transitions matching tasks to `TaskStatus.INTERRUPTED` with `error_message = "Task interrupted by application shutdown"`, `updated_at = :now`, and `completed_at = :now` (establishing the 30-day retention cutoff for abandoned interrupted tasks).
   - **Token Invalidation:** In-memory authorization tokens expire immediately on process shutdown; persisted journal references are non-reusable audit records. Old tokens cannot authorize subsequent operations upon restart.
   - **Network Ambiguity:** In-flight network requests interrupted by restart are recorded as `REQUEST_OUTCOME_UNKNOWN`. Zero automated retries; explicit user confirmation required with potential duplicate billing disclosure.
   - Operation is idempotent and returns the list of reconciled `task_id`s.

4. **Task Retention, Purging, and Deletion Semantics (Finding E):**
   - **Retention Calculation:** 30-day retention calculated strictly from `completed_at` (terminal or interrupted transition timestamp), NOT from task creation time.
   - **Protected Active Tasks:** Active or awaiting tasks (`CREATED`, `CONTEXT_GATHERING`, `PLANNING`, `AWAITING_PLAN_APPROVAL`, `RUNNING`, `AWAITING_CONSENT`, `AWAITING_ACTION_APPROVAL`, `PAUSED`, `VALIDATING`, `AWAITING_FINAL_APPROVAL`) have `completed_at IS NULL` and are never automatically purged.
   - **Purge Scope:** Applies to terminal and interrupted tasks (`COMPLETED`, `REJECTED`, `CANCELLED`, `FAILED`, `INTERRUPTED`) where `completed_at IS NOT NULL AND completed_at <= :cutoff`.
   - **Cascade Deletion:** SQLite `ON DELETE CASCADE` automatically purges `agent_task_journal` rows when `agent_tasks` records are deleted.
   - **Two-Phase Idempotent Filesystem Cleanup:**
     1. Unlink scratchpad directories (`.polpo/working_copies/{id}/`) and staging failure bundles (`.polpo/staging_failures/{id}/`) using `shutil.rmtree(..., ignore_errors=True)`.
     2. Delete database records in a SQLite transaction (`BEGIN IMMEDIATE`) with bounded batch limits (`LIMIT 50`).
     3. Startup reconciliation cleans orphan scratchpad directories whose `working_copy_id` is missing in `agent_tasks`.

---

## 4. Acceptance Criteria

- [ ] Migration applies cleanly via `SQLiteMigrationRunner` advancing schema version to 6.
- [ ] Tasks can be saved, updated, and retrieved by `task_id` with plan steps preserved.
- [ ] Journal entries append monotonically and reject payloads containing unallowed keys.
- [ ] Every non-terminal status (`CREATED`, `CONTEXT_GATHERING`, `PLANNING`, `AWAITING_PLAN_APPROVAL`, `RUNNING`, `AWAITING_CONSENT`, `AWAITING_ACTION_APPROVAL`, `PAUSED`, `VALIDATING`, `AWAITING_FINAL_APPROVAL`) transitions to `INTERRUPTED` upon calling `reconcile_interrupted_tasks()`.
- [ ] Stored `completed_at` timestamp is recorded upon terminal transition and used for 30-day retention calculations.
- [ ] `purge_expired_tasks()` purges terminal tasks older than 30 days from `completed_at` and cascade-deletes journal records atomically.
- [ ] Zero plaintext secrets or raw document texts found in stored database columns across `agent_tasks`, `agent_task_journal`, and `publication_style_profiles`.

---

## 5. Test Specifications

### 5.1 Integration Tests
- `tests/integration/test_agent_task_repository.py`:
  - `test_migration_006_execution()`: Verifies migration creates tables and indices cleanly.
  - `test_save_and_get_agent_task()`: Round-trip test of `AgentTask` aggregate.
  - `test_journal_allowlist_filtering()`: Confirms non-allowlisted fields are rejected.
  - `test_reconcile_interrupted_tasks_all_states()`: Simulates crash and verifies all 10 non-terminal states transition to `INTERRUPTED`.
  - `test_purge_expired_tasks_retention()`: Verifies 30-day retention calculated from `completed_at` deletes only expired terminal tasks.
  - `test_cascade_delete_journal_rows()`: Asserts deleting an `agent_tasks` row atomically deletes all corresponding `agent_task_journal` rows.

### 5.2 Negative & Security Tests (Verifying Stored Database Values)
- `tests/integration/test_database_security_policies.py`:
  - `test_reject_complete_markdown_in_tasks_and_journal()`: Asserts that attempting to persist full Markdown document text in `task_goal`, `metadata_json`, or any table column raises `JournalPrivacyViolationError` and zero document text exists in the database file.
  - `test_reject_editor_draft_in_database()`: Verifies active in-memory draft buffers are never persisted into SQLite records.
  - `test_reject_raw_prompt_and_completion_persistence()`: Verifies raw prompts and model completions are rejected and never appear in `agent_tasks.plan_json` or `agent_task_journal.metadata_json`.
  - `test_reject_unredacted_compiler_diagnostics()`: Verifies compiler logs with local paths are rejected; only sanitized excerpts under 10 KB pass.
  - `test_reject_api_credentials_persistence()`: Verifies raw API keys, tokens, or passwords raise `JournalPrivacyViolationError` and inspects actual SQLite records to verify zero credential substrings.
  - `test_reject_sensitive_paths_persistence()`: Verifies local absolute paths (e.g. `/home/...`) are redacted and rejected in stored fields.
  - `test_reject_oversized_free_text_metadata()`: Verifies `summary` > 256 chars or `metadata_json` > 8 KB is rejected.
  - `test_reject_unvalidated_tool_arguments_in_plan()`: Verifies `plan_json` with unallowlisted keys or unvalidated tool arguments is rejected.
  - `test_reject_malicious_style_profile_payload()`: Verifies TeX code injection (`\newcommand`, `\usepackage`, shell commands) in `profile_json` is rejected.

### 5.3 Concurrency & Persistence Considerations
- Operations use worker-local SQLite connections with `BEGIN IMMEDIATE` and `PRAGMA busy_timeout = 5000;`.

---

## 6. Error and Recovery Behavior

- Interrupted tasks are safely quarantined in `INTERRUPTED` status; no automatic re-execution occurs.

---

## 7. Observability & Audit Requirements

- Records structured migration logs upon applying `006_agent_task_tables.sql`.
- Journal entries record monotonically with microsecond timestamps.

---

## 8. Documentation Updates

- Document migration 006 in `CONTEXT.md` Section 1.

---

## 9. Required Project Skills

- `domain-modeling`: Persistence boundaries.
- `tdd`: Test-driven migration and repository implementation.

---

## 10. Verification Commands

```bash
pytest tests/integration/test_agent_task_repository.py -v
python3 -c "import sqlite3; conn = sqlite3.connect(':memory:'); conn.executescript(open('infrastructure/persistence/sqlite/migrations/006_agent_task_tables.sql').read()); print('Migration 006 valid')"
```

---

## 11. Definition of Done

- Migration script valid and integration tests pass 100%.
- Privacy allowlist strictly verified.
