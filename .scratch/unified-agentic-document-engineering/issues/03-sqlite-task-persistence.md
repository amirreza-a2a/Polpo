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

   | Table | Field | SQL Type & Nullability | Default | Key Role | Data Classification | Sensitive Content Possible? | Persistence Policy | Validation & Size Bounds | Retention & Deletion |
   | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
   | `agent_tasks` | `task_id` | `TEXT NOT NULL` | None | Primary Key | System Identifier | No | Persisted primary key | Canonical UUID4 format string (36 chars) | 30 days from `completed_at` |
   | `agent_tasks` | `job_id` | `INTEGER NOT NULL` | None | Foreign Key | Parent Job Reference | No | References parent `jobs(id)` | Valid positive integer; `ON DELETE CASCADE` | Cascade deleted with parent job |
   | `agent_tasks` | `status` | `TEXT NOT NULL` | None | None | State Machine Enum | No | Current `TaskStatus` string | Allowlisted enum string only (max 32 chars) | 30 days from `completed_at` |
   | `agent_tasks` | `task_goal` | `TEXT NOT NULL` | None | None | Bounded Lifecycle Category | **Yes** (if raw request stored) | **Allowlisted `TaskCategory` enum only** (e.g. `PDF_EXPORT_REPAIR`, `STYLE_PROFILE_APPLY`, `DOCUMENT_ANALYSIS`). Ephemeral human-facing display label remains in RAM only; SQLite persists strictly the allowlisted enum string. Raw user requests or document text are strictly prohibited. | Allowlisted enum string only (max 32 chars) | 30 days from `completed_at` |
   | `agent_tasks` | `plan_json` | `TEXT` | `NULL` | None | Validated Structured Metadata | **Yes** (if model emits raw text) | **Metadata-only persistence: NULL or strictly bounded step metadata only** (step index, allowlisted tool names, risk tiers). Ephemeral plans hold full rationale. Prohibited: prompts, completions, tool arguments, tool outputs, document fragments, credentials. | Schema validation against `AgentPlanMetadataSchema`, max 16 KB (or NULL) | 30 days from `completed_at` |
   | `agent_tasks` | `current_step_index` | `INTEGER` | `0` | None | Execution Counter | No | Monotonic step counter | Integer `0 <= index <= total_steps` | 30 days from `completed_at` |
   | `agent_tasks` | `total_steps` | `INTEGER` | `0` | None | Execution Bound | No | Bounded plan size | Integer `0 <= steps <= 10` | 30 days from `completed_at` |
   | `agent_tasks` | `working_copy_id` | `TEXT` | `NULL` | None | Ephemeral Scratchpad ID | No | Opaque UUID referencing `.polpo/working_copies/{id}/` | Canonical UUID4 format string; disk content strictly ephemeral | 30 days in DB; folder purged on completion/restart |
   | `agent_tasks` | `failure_id` | `TEXT` | `NULL` | None | Ephemeral Diagnostic ID | No | Opaque UUID referencing `.polpo/staging_failures/{id}/` | Canonical UUID4 format string; diagnostic bundle managed by LRU | 30 days in DB; bundle pruned via LRU (max 5) |
   | `agent_tasks` | `error_message` | `TEXT` | `NULL` | None | Bounded Error Code | **Yes** (if raw logs stored) | **Typed `ErrorCode` or sanitized error code string only** (e.g. `PACKAGE_MISSING:soul`, `LATEX_SYNTAX_ERROR`, `FONT_MISSING`, `TEMPLATE_CONFLICT`). Prohibited: arbitrary English sentences, unredacted stack traces, local system paths, source text. | Sanitized error code string, max 64 chars | 30 days from `completed_at` |
   | `agent_tasks` | `created_at` | `REAL NOT NULL` | None | None | Timestamp | No | Epoch creation time | Real/float >= 0 | 30 days from `completed_at` |
   | `agent_tasks` | `updated_at` | `REAL NOT NULL` | None | None | Timestamp | No | Epoch last update time | Real/float >= `created_at` | 30 days from `completed_at` |
   | `agent_tasks` | `completed_at` | `REAL` | `NULL` | None | Set-Once Timestamp | No | Epoch terminal or interrupted transition time; baseline for retention. Set once on terminal or interrupted transition; preserved on repeated recovery and dismissal via `COALESCE(completed_at, :now)`. | Real/float >= `created_at` (NULL for active tasks; set once via COALESCE; never reset on dismissal) | 30 days from `completed_at` |
   | `agent_task_journal` | `entry_id` | `INTEGER PRIMARY KEY AUTOINCREMENT` | Auto | Primary Key | Sequence ID | No | Auto-increment primary key | Positive integer | Cascade deleted with task |
   | `agent_task_journal` | `task_id` | `TEXT NOT NULL` | None | Foreign Key | Parent Task Reference | No | References `agent_tasks(task_id)` | UUID4 format; `ON DELETE CASCADE` | Cascade deleted with task |
   | `agent_task_journal` | `step_index` | `INTEGER` | `NULL` | None | Execution Step | No | Step index in plan | Optional integer `0 <= step <= 10` | Cascade deleted with task |
   | `agent_task_journal` | `event_type` | `TEXT NOT NULL` | None | None | Audit Event Enum | No | Typed audit event | Allowlisted string enum | Cascade deleted with task |
   | `agent_task_journal` | `metadata_json` | `TEXT NOT NULL` | None | None | Allowlisted Audit Record | **Yes** (if arbitrary keys allowed) | **Strict Allowlist only**. Contains typed classifications, allowlisted summary event codes, and opaque non-authorizing references. Prohibited: document hashes, raw source text, secrets. | Schema validation against `JournalMetadataAllowlist`, max 8 KB | Cascade deleted with task |
   | `agent_task_journal` | `recorded_at` | `REAL NOT NULL` | None | None | Timestamp | No | Microsecond epoch timestamp | Real/float >= 0 | Cascade deleted with task |
   | `publication_style_profiles` | `profile_id` | `TEXT PRIMARY KEY` | None | Primary Key | Profile Identifier | No | Key (`builtin:{name}` or `user:{uuid}`) | String, max 64 chars, regex `^(builtin:[a-z0-9_-]+|user:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$` (canonical UUID4) | Permanent (built-in) / user-managed |
   | `publication_style_profiles` | `display_name` | `TEXT NOT NULL` | None | None | Presentation Name | **Yes** (user title) | Human-readable profile title | String, max 128 chars, sanitized | User-managed |
   | `publication_style_profiles` | `description` | `TEXT` | `NULL` | None | Presentation Summary | **Yes** (user summary) | Optional presentation summary | String, max 512 chars, sanitized | User-managed |
   | `publication_style_profiles` | `schema_version` | `INTEGER NOT NULL` | `1` | None | Schema Evolution | No | Schema compatibility version | Positive integer (default 1) | User-managed |
   | `publication_style_profiles` | `is_builtin` | `INTEGER NOT NULL` | `0` | None | Immutability Flag | No | Built-in profile protection | 1 (true) or 0 (false) | Permanent (built-in) / user-managed |
   | `publication_style_profiles` | `profile_json` | `TEXT NOT NULL` | None | None | Declarative Profile Spec | **Yes** (if raw macros injected) | **Strictly validated declarative typesetting JSON**. Prohibit raw LaTeX macros, `header-includes`, shell commands, executable code. | Validated against `PublicationStyleProfileSchema`, max 16 KB | User-managed |
   | `publication_style_profiles` | `created_at` | `REAL NOT NULL` | None | None | Timestamp | No | Epoch creation time | Real/float >= 0 | User-managed |
   | `publication_style_profiles` | `updated_at` | `REAL NOT NULL` | None | None | Timestamp | No | Epoch modification time | Real/float >= `created_at` | User-managed |

   - **Journal Allowlist Keys (`metadata_json`):** `task_id`, `step_index`, `event_type`, `recorded_at`, `duration_ms`, `tool_name`, `step_correlation_id` (opaque UUID; dictionary attack defense), `status`, `sanitized_error_category`, `consent_token_ref` (single-use UUID reference), `approval_token_ref` (single-use UUID reference), `summary` (typed event code from `JournalEventCode` enum or allowlisted closed set only, e.g. `TASK_CREATED`, `PLAN_APPROVAL_REQUIRED`, `DISPATCH_INTENT`, `REQUEST_OUTCOME_UNKNOWN`, `TOOL_ACTION_OUTCOME_UNKNOWN`, `TASK_INTERRUPTED`, `TASK_DISMISSED`; arbitrary free-form English prose strictly prohibited), `request_disposition`.
   - **Persistence Denylist:** Complete user requests with source text, full Markdown drafts, working copies, prompts, completions, unvalidated plans, plan rationales, tool args with source text, raw compiler logs or diagnostic excerpts, `document.tex`, local paths, credentials, unredacted exceptions.
   - **Compiler Diagnostics Lifecycle:** Bounded diagnostic excerpts (up to 10 KB) are held strictly in ephemeral RAM (`DiagnosticBundle`) for approved tool input; persisting raw compiler logs or diagnostic excerpts into SQLite is strictly prohibited.

3. **Exhaustive Startup Recovery & Token Invalidation (Finding A, Finding B, Section 4):**
   - `reconcile_interrupted_tasks()` scans `agent_tasks` under `BEGIN IMMEDIATE` across **all 10 non-terminal states**: `CREATED`, `CONTEXT_GATHERING`, `PLANNING`, `AWAITING_PLAN_APPROVAL`, `RUNNING`, `AWAITING_CONSENT`, `AWAITING_ACTION_APPROVAL`, `PAUSED`, `VALIDATING`, `AWAITING_FINAL_APPROVAL`.
   - Transitions matching tasks to `TaskStatus.INTERRUPTED` with `error_message = ErrorCode.UNKNOWN_ERROR`, `updated_at = :now`, and `completed_at = COALESCE(completed_at, :now)`.
   - **Interruption Timestamp Invariant:** On repeated application launches or subsequent reconciliations, `completed_at` is **never reset or updated**. An already-interrupted task preserves its initial interruption timestamp, preventing retention from being indefinitely extended.
   - **Dismissal Retention Invariant:** When a user dismisses an interrupted task (`INTERRUPTED -> CANCELLED`), `completed_at` is preserved unchanged (`completed_at = COALESCE(completed_at, :now)`). User dismissal CANNOT reset or extend the 30-day retention cutoff. The dismissal action is logged as a typed `TASK_DISMISSED` journal event.
   - **Token Invalidation:** In-memory authorization tokens expire immediately on process shutdown; persisted journal references are non-reusable audit records. Old tokens cannot authorize subsequent operations upon restart.
   - **Network Ambiguity & Dispatch Intent:** In-flight network requests interrupted by restart are recorded as `REQUEST_OUTCOME_UNKNOWN` via write-ahead `DISPATCH_INTENT` verification. Zero automated retries; explicit user confirmation required with potential duplicate billing disclosure.
   - **Restart Semantics:** Restart creates a new `AgentTask` with fresh `task_id`, fresh editor snapshot, and fresh authorization (`completed_at = NULL`); the interrupted task remains persisted for audit.
   - Operation is idempotent and returns the list of reconciled `task_id`s.

4. **Task Retention, Purging, and Two-Phase Filesystem Cleanup Semantics (Finding A, Finding B):**
   - **Retention Calculation:** 30-day retention calculated strictly from `completed_at` (terminal or interrupted transition timestamp), NOT from task creation time.
   - **Protected Active Tasks:** Active or awaiting tasks (`CREATED`, `CONTEXT_GATHERING`, `PLANNING`, `AWAITING_PLAN_APPROVAL`, `RUNNING`, `AWAITING_CONSENT`, `AWAITING_ACTION_APPROVAL`, `PAUSED`, `VALIDATING`, `AWAITING_FINAL_APPROVAL`) have `completed_at IS NULL` and are **never automatically purged**.
   - **Purge Scope Across All Five Terminal & Interrupted States:** Applies to `COMPLETED`, `REJECTED`, `CANCELLED`, `FAILED`, and `INTERRUPTED` where `completed_at IS NOT NULL AND completed_at <= :cutoff`.
   - **Cascade Deletion:** SQLite `ON DELETE CASCADE` automatically purges `agent_task_journal` rows when `agent_tasks` records are deleted.
   - **Two-Phase Idempotent Filesystem Cleanup:**
     Cleanup covers **both** temporary storage directories:
     - Working copy scratchpads: `<canonical_root>/.polpo/working_copies/`
     - Staging failure diagnostic bundles: `<canonical_root>/.polpo/staging_failures/`

     Safety and path normalization rules:
     1. *Canonical Allowed Roots:* Deletion is strictly anchored to validated application-data roots (`working_copies_dir` and `staging_failures_dir`).
     2. *Canonical UUID4 Directory Name Validation:* Every child directory candidate must be validated using Python's standard `uuid.UUID(entry_name)` parser, verifying `str(parsed) == entry_name` (canonical lowercase), `parsed.version == 4`, and `parsed.variant == uuid.RFC_4122`. Malformed strings, non-canonical casing, wrong UUID versions (e.g. v1, v5), or wrong variants are rejected immediately.
     3. *Path Normalization & Anti-Traversal:* The target directory path is validated against the canonical application-data root. Any path containing `..` traversal, directory escape, or unauthorized subpaths is rejected.
     4. *Symlink & Reparse-Point Defense:* Because `Path.resolve()` and `is_relative_to()` alone are vulnerable to time-of-check/time-of-use (TOCTOU) race conditions, cleanup enforces `follow_symlinks=False` and revalidates target identity with `os.lstat()` immediately before removal. On Windows, NTFS reparse points and directory junctions are explicitly checked and never traversed into external targets. If safe target identity cannot be proven, cleanup fails closed.
     5. *No Silent Failure Masks:* `shutil.rmtree(..., ignore_errors=True)` is **strictly prohibited**. The cleanup implementation uses observable, per-item exception handling (`try ... except OSError as err`) that logs structured warnings on deletion failures while recording unlinked targets.
     6. *Two-Phase Ordering:*
        - *Phase 1 (Filesystem):* Unlink disk folders (`working_copies/{id}` and `staging_failures/{id}`) with observable error trapping.
        - *Phase 2 (Database):* Execute SQLite transaction (`BEGIN IMMEDIATE`) deleting expired task rows.
        - If Phase 1 succeeds and Phase 2 fails: SQLite transaction rolls back, preserving the database row; cleanup retries on the next cycle.
        - If Phase 2 succeeds and Phase 1 encounters an unexpected file-lock error: startup orphan reconciliation scans **both** `.polpo/working_copies/` and `.polpo/staging_failures/` and safely purges any directory without an active corresponding ID in `agent_tasks`.
   - **Bounded Cleanup Batches:** Retention cleanup runs in bounded batches of at most 50 tasks per execution cycle (`LIMIT 50`) to prevent SQLite transaction locking or desktop GUI lag.

---

## 4. Acceptance Criteria

- [ ] Migration applies cleanly via `SQLiteMigrationRunner` advancing schema version to 6.
- [ ] Tasks can be saved, updated, and retrieved by `task_id` with plan steps preserved.
- [ ] Journal entries append monotonically and reject payloads containing unallowed keys.
- [ ] Every non-terminal status (`CREATED`, `CONTEXT_GATHERING`, `PLANNING`, `AWAITING_PLAN_APPROVAL`, `RUNNING`, `AWAITING_CONSENT`, `AWAITING_ACTION_APPROVAL`, `PAUSED`, `VALIDATING`, `AWAITING_FINAL_APPROVAL`) transitions to `INTERRUPTED` upon calling `reconcile_interrupted_tasks()`.
- [ ] Stored `completed_at` timestamp is recorded once via `COALESCE` upon terminal and interrupted transitions, never reset on repeated startup reconciliations or user dismissals, and used for 30-day retention calculations.
- [ ] `purge_expired_tasks()` purges terminal and interrupted tasks (`COMPLETED`, `REJECTED`, `CANCELLED`, `FAILED`, `INTERRUPTED`) older than 30 days from `completed_at` and cascade-deletes journal records atomically.
- [ ] Two-phase cleanup unlinks both `.polpo/working_copies/` and `.polpo/staging_failures/` safely without `ignore_errors=True`, enforcing canonical UUID4 parsing, canonical root boundaries, and symlink/reparse-point protection.
- [ ] Zero plaintext secrets, free-form prompt prose, or raw document texts found in stored database columns across `agent_tasks`, `agent_task_journal`, and `publication_style_profiles`.

---

## 5. Test Specifications

### 5.1 Integration Tests
- `tests/integration/test_agent_task_repository.py`:
  - `test_migration_006_execution()`: Verifies migration creates tables and indices cleanly.
  - `test_save_and_get_agent_task()`: Round-trip test of `AgentTask` aggregate.
  - `test_journal_allowlist_filtering()`: Confirms non-allowlisted fields are rejected.
  - `test_reconcile_interrupted_tasks_all_states()`: Simulates crash and verifies all 10 non-terminal states transition to `INTERRUPTED` and that repeated startup does NOT reset `completed_at`.
  - `test_terminal_transition_sets_completed_at_once()`: Asserts terminal transition sets `completed_at` once via `COALESCE`.
  - `test_interrupted_transition_sets_completed_at_once()`: Asserts startup transition to `INTERRUPTED` sets `completed_at` once via `COALESCE`.
  - `test_repeated_reconciliation_preserves_completed_at()`: Asserts subsequent reconciliations leave `completed_at` unchanged.
  - `test_dismiss_interrupted_task_preserves_completed_at()`: Asserts that dismissing an interrupted task (`INTERRUPTED -> CANCELLED`) strictly preserves `completed_at`.
  - `test_dismissed_interrupted_task_retains_original_purge_deadline()`: Verifies that dismissal does not extend the 30-day retention cutoff.
  - `test_restart_creates_fresh_task_without_changing_original_timestamp()`: Verifies restart creates a fresh task row without altering the interrupted task's `completed_at`.
  - `test_purge_expired_tasks_retention()`: Verifies 30-day retention calculated from `completed_at` across all five required states and boundaries:
    1. Expired `COMPLETED` task is purged.
    2. Expired `REJECTED` task is purged.
    3. Expired `CANCELLED` task is purged.
    4. Expired `FAILED` task is purged.
    5. Expired `INTERRUPTED` task is purged.
    6. Non-expired tasks in all terminal/interrupted states are retained.
    7. Active tasks (`RUNNING`, `PLANNING`, etc.) are never purged.
    8. Tasks with `completed_at IS NULL` are strictly protected.
    9. Corresponding `agent_task_journal` rows are deleted via foreign-key cascade.
    10. Cleanup execution is bounded (`LIMIT 50`) and idempotent across repeated runs.
  - `test_uuid4_canonical_validation()`: Verifies standard `uuid.UUID` parser, lowercase format, round-trip equality, version 4, and RFC 4122 variant.
  - `test_reject_non_uuid4_directory_name()`: Asserts directories with non-UUID names in temporary folders are rejected.
  - `test_reject_noncanonical_uuid_directory_name()`: Asserts uppercase or malformed UUID strings are rejected.
  - `test_reject_wrong_uuid_variant()`: Asserts non-RFC-4122 variant UUIDs are rejected.
  - `test_reject_path_traversal()`: Verifies `..` traversal attempts outside canonical roots raise security errors.
  - `test_reject_symlink_escape()`: Verifies symbolic links pointing outside root are unlinked without following target.
  - `test_reject_windows_reparse_point_escape()`: Verifies NTFS junctions/reparse points are detected and not traversed.
  - `test_revalidate_target_before_delete()`: Verifies `os.lstat()` identity revalidation prevents TOCTOU races.
  - `test_cleanup_failure_is_observable()`: Verifies deletion failure logs structured warnings and does not mask errors via `ignore_errors=True`.
  - `test_cleanup_retry_is_idempotent()`: Verifies running cleanup multiple times succeeds cleanly.
  - `test_cascade_delete_journal_rows()`: Asserts deleting an `agent_tasks` row atomically deletes all corresponding `agent_task_journal` rows.

### 5.2 Negative & Security Tests (Verifying Stored Database Values)
- `tests/integration/test_database_security_policies.py`:
  - `test_task_goal_persists_only_allowlisted_category()`: Verifies `task_goal` persists only `TaskCategory` members.
  - `test_task_goal_rejects_arbitrary_user_sentence()`: Asserts arbitrary user prompts in `task_goal` raise privacy errors.
  - `test_error_message_persists_only_error_code()`: Verifies `error_message` persists only `ErrorCode` tokens.
  - `test_journal_summary_accepts_only_allowlisted_event_code()`: Verifies `summary` accepts only `JournalEventCode` members and rejects free-form sentences.
  - `test_plan_rationale_never_persisted()`: Asserts natural-language plan rationales are never persisted in SQLite.
  - `test_plan_json_is_null_or_metadata_only()`: Verifies `plan_json` stores only NULL or bounded step indices and tool names.
  - `test_tool_arguments_never_persisted()`: Asserts tool arguments containing source fragments are not stored.
  - `test_raw_compiler_logs_never_persisted()`: Asserts compiler output logs are never persisted in SQLite.
  - `test_sanitized_diagnostic_excerpt_remains_ephemeral()`: Verifies 10 KB diagnostic excerpt remains in RAM only.
  - `test_database_file_contains_no_raw_user_prompt()`: Performs full text search of the SQLite database file to verify zero prompt text.
  - `test_database_file_contains_no_document_fragment()`: Performs full text search of the SQLite database file to verify zero document text.
  - `test_database_file_contains_no_api_secret()`: Verifies zero API keys or credentials in stored database records.
  - `test_reject_complete_markdown_in_tasks_and_journal()`: Asserts that attempting to persist full Markdown document text raises `JournalPrivacyViolationError`.
  - `test_reject_editor_draft_in_database()`: Verifies active in-memory draft buffers are never persisted into SQLite records.
  - `test_reject_raw_prompt_and_completion_persistence()`: Verifies raw prompts and model completions are rejected.
  - `test_reject_sensitive_paths_persistence()`: Verifies local absolute paths (e.g. `/home/...`) are redacted and rejected in stored fields.
  - `test_reject_oversized_free_text_metadata()`: Verifies `summary` > 256 chars or `metadata_json` > 8 KB is rejected.
  - `test_reject_unvalidated_tool_arguments_in_plan()`: Verifies `plan_json` with unallowlisted keys is rejected.
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
