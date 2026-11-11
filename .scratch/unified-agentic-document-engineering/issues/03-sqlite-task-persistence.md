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
```

---

## 3. Detailed Implementation Requirements

1. **Migration Sequence (Finding I):**
   - The file MUST be named `006_agent_task_tables.sql` because existing migrations run through 005 (`005_pipeline2_source_version.sql`).
2. **Journal Privacy Allowlist (Finding E):**
   - The `metadata_json` column MUST be filtered against an allowlist before insertion:
     Allowed keys: `task_id`, `step_index`, `event_type`, `recorded_at`, `duration_ms`, `tool_name`, `input_hash`, `output_hash`, `status`, `sanitized_error_category`, `consent_token_ref`, `approval_token_ref`, `summary`.
   - Any attempt to persist keys matching `document_text`, `source`, `prompt`, `completion`, `api_key`, `secret` raises `JournalPrivacyViolationError`.
   - Max size per entry: 8 KB. Tasks capped at 50 journal entries.
3. **Conservative Startup Recovery (Finding D):**
   - `reconcile_interrupted_tasks()` scans `agent_tasks` under `BEGIN IMMEDIATE` for active states (`CREATED`, `CONTEXT_GATHERING`, `PLANNING`, `RUNNING`, `VALIDATING`, `PAUSED`).
   - Updates their status to `TaskStatus.INTERRUPTED` with `error_message = "Task interrupted by application shutdown"`.
   - Operation is idempotent and returns the list of reconciled `task_id`s.

---

## 4. Acceptance Criteria

- [ ] Migration applies cleanly via `SQLiteMigrationRunner` advancing schema version to 6.
- [ ] Tasks can be saved, updated, and retrieved by `task_id` with plan steps preserved.
- [ ] Journal entries append monotonically and reject payloads containing unallowed keys.
- [ ] Active tasks in `RUNNING` status transition to `INTERRUPTED` upon calling `reconcile_interrupted_tasks()`.
- [ ] No plaintext secrets or document texts found in stored JSON columns.

---

## 5. Test Specifications

### 5.1 Integration Tests
- `tests/integration/test_agent_task_repository.py`:
  - `test_migration_006_execution()`: Verifies migration creates tables and indices cleanly.
  - `test_save_and_get_agent_task()`: Round-trip test of `AgentTask` aggregate.
  - `test_journal_allowlist_filtering()`: Confirms non-allowlisted fields are rejected.
  - `test_reconcile_interrupted_tasks()`: Simulates crash and verifies transition to `INTERRUPTED`.

### 5.2 Negative & Security Tests
- `test_reject_secret_leakage_in_journal()`: Verifies that passing credentials or document text raises `JournalPrivacyViolationError`.

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
