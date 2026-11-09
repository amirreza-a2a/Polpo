# TICK-ADE-03: SQLite Task Repository, Journaling Schema & Startup Crash Recovery

- **Ticket ID:** `TICK-ADE-03`
- **Milestone:** Milestone A (Agent Foundations)
- **Priority:** P1
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Blocked By:** `TICK-ADE-01`
- **Authoritative ADR:** ADR-007 (Unified Agent Orchestration & Tool Governance)

---

## 1. Objective

Implement persistent SQLite storage and append-only execution journaling for Agent tasks in `infrastructure/persistence/sqlite/agent_task_repository.py`, along with migration runner integration and startup crash recovery.

---

## 2. User Value

Ensures total auditability and crash safety. If the workstation crashes or the user exits during an active task, running network operations are safely reconciled without duplicate API costs or corrupted document states.

---

## 3. Scope

- Create SQLite migration (e.g. Migration 009) defining:
  - Table `agent_tasks`: `task_id TEXT PRIMARY KEY`, `job_id INTEGER`, `status TEXT`, `task_goal TEXT`, `plan_json TEXT`, `current_step_index INTEGER`, `total_steps INTEGER`, `working_copy_id TEXT`, `error_message TEXT`, `created_at REAL`, `updated_at REAL`.
  - Table `agent_task_journal`: `entry_id INTEGER PRIMARY KEY AUTOINCREMENT`, `task_id TEXT`, `step_index INTEGER`, `event_type TEXT`, `payload_json TEXT`, `recorded_at REAL`.
  - Indices on `job_id`, `status`, and `task_id`.
- Define abstract port `IAgentTaskRepository` in `application/ports/agent_task_repository.py`:
  - `save_task(task: AgentTask) -> None`
  - `get_task(task_id: str) -> Optional[AgentTask]`
  - `list_tasks_for_job(job_id: int) -> List[AgentTask]`
  - `append_journal_entry(task_id: str, step_index: int, event_type: str, payload: Dict[str, Any]) -> None`
  - `reconcile_interrupted_tasks() -> List[str]`
- Implement `SqliteAgentTaskRepository` in `infrastructure/persistence/sqlite/agent_task_repository.py`:
  - Connection-scoped operations under WAL mode and `BEGIN IMMEDIATE` transactions.
  - Startup reconciliation: Scans for active non-terminal tasks (`RUNNING`, `PLANNING`, `VALIDATING`) and marks them `INTERRUPTED`.

---

## 4. Explicit Non-Goals

- No storing raw, unredacted document contents in SQLite (documents reside in version files or temporary scratchpads).
- No storing plaintext API credentials or tokens in task payloads.

---

## 5. Codebase References

- `infrastructure/persistence/sqlite/connection.py` (Worker-local connection management).
- `infrastructure/persistence/sqlite/migration_runner.py` (Versioned migration architecture).
- `infrastructure/persistence/sqlite/job_repository.py` (Existing repository patterns).
- `application/services/job_recovery_service.py` (Startup recovery precedent).

---

## 6. Proposed Files & Components

- Create: `application/ports/agent_task_repository.py`
- Create: `infrastructure/persistence/sqlite/migrations/009_agent_task_tables.sql`
- Create: `infrastructure/persistence/sqlite/agent_task_repository.py`
- Modify: `infrastructure/persistence/sqlite/unit_of_work.py` (Add `agent_tasks` repository property)
- Create: `tests/integration/test_agent_task_repository.py`

---

## 7. Domain & Architectural Constraints

- **AGENTS.md Rule 8 & 9:** SQLite connections are worker-local; schema mutations are versioned and reversible; transactions use `BEGIN IMMEDIATE` for state mutations.
- **AGENTS.md Rule 11:** Serialized payloads in `agent_task_journal` must be verified free of secret tokens.

---

## 8. Detailed Implementation Requirements

1. `reconcile_interrupted_tasks()` must be idempotent. If called multiple times, subsequent invocations return an empty list.
2. Interrupted tasks must have their status updated to `TaskStatus.INTERRUPTED` with `error_message = "Task interrupted by application shutdown"`.
3. SQLite operations must set `PRAGMA busy_timeout = 5000;`.

---

## 9. Acceptance Criteria

- [ ] Migration applies cleanly on clean database and existing historical databases.
- [ ] Tasks can be saved, updated, and retrieved by `task_id` with plan steps intact.
- [ ] Journal entries append monotonically.
- [ ] Active tasks in `RUNNING` status transition to `INTERRUPTED` upon calling `reconcile_interrupted_tasks()`.
- [ ] No plaintext secrets found in stored JSON columns.

---

## 10. Required Tests

- Integration tests in `tests/integration/test_agent_task_repository.py` verifying:
  - Task creation and round-trip retrieval.
  - Journal entry ordering.
  - Recovery of simulated crashed tasks.
  - Concurrent writes under SQLite WAL.

---

## 11. Security Requirements

- Scan `payload_json` strings for secret patterns before persistence. Reject records containing API key substrings.

---

## 12. Completion Evidence & Follow-up

- Integration test suite passing 100%.
- Unblocks: `TICK-ADE-04`.
