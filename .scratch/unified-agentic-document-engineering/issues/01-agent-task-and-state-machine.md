# TICK-ADE-01: Core Domain Agent Task Aggregate, Plan Model & Lifecycle Policy

- **Ticket ID:** `TICK-ADE-01`
- **Milestone:** Milestone A (Agent Foundations)
- **Priority:** P1 (Blocker for Milestone A)
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Authoritative ADR:** ADR-007 (Unified Agent Orchestration & Tool Governance)

---

## 1. Objective

Define the core domain entities and state transition policies governing the Agent Task lifecycle, multi-step execution plans, and task checkpoints in `core/entities/agent_task.py` and `core/policies/agent_task_policy.py`.

---

## 2. User Value

Establishes transparent, predictable, and supervised task execution. Authors can see the complete multi-step plan before execution begins, monitor step progress, and trust that the assistant cannot transition into unapproved or uncontrolled states.

---

## 3. Scope

- Implement pure Python dataclasses in `core/entities/agent_task.py`:
  - `TaskStatus` enum (`CREATED`, `CONTEXT_GATHERING`, `PLANNING`, `AWAITING_PLAN_APPROVAL`, `RUNNING`, `PAUSED`, `AWAITING_CONSENT`, `AWAITING_ACTION_APPROVAL`, `VALIDATING`, `AWAITING_FINAL_APPROVAL`, `COMPLETED`, `REJECTED`, `CANCELLED`, `FAILED`, `INTERRUPTED`).
  - `PlanStepStatus` enum (`PENDING`, `IN_PROGRESS`, `COMPLETED`, `FAILED`, `SKIPPED`).
  - `ToolCall` value object: `tool_name: str`, `arguments: Dict[str, Any]`, `call_id: str`.
  - `PlanStep` entity: `step_index: int`, `tool_name: str`, `arguments: Dict[str, Any]`, `rationale: str`, `status: PlanStepStatus`, `output: Optional[Dict[str, Any]]`.
  - `AgentPlan` entity: `plan_id: str`, `task_goal: str`, `assessed_scope: str`, `steps: List[PlanStep]`, `created_at: float`.
  - `AgentTask` aggregate root: `task_id: str`, `job_id: int`, `status: TaskStatus`, `plan: Optional[AgentPlan]`, `current_step_index: int`, `working_copy_id: Optional[str]`, `error_message: Optional[str]`, `created_at: float`, `updated_at: float`.
- Implement `AgentTaskPolicy` in `core/policies/agent_task_policy.py`:
  - `validate_transition(current_status: TaskStatus, new_status: TaskStatus) -> bool`: Enforces valid state transitions according to ADR-007. Raises `InvalidStateTransitionError` on illegal moves.
  - `is_terminal(status: TaskStatus) -> bool`: Identifies terminal states (`COMPLETED`, `REJECTED`, `CANCELLED`, `FAILED`).
  - `can_execute_step(task: AgentTask) -> bool`: Enforces that execution is permitted only in `RUNNING` or `VALIDATING` states.

---

## 4. Explicit Non-Goals

- No SQLite persistence or SQL statements (handled in `TICK-ADE-03`).
- No tool execution or AI HTTP requests (handled in `TICK-ADE-02` and `TICK-ADE-04`).
- No PySide6/Qt signals or QML imports.

---

## 5. Codebase References

- `core/entities/job.py` (Existing domain job entity pattern).
- `core/policies/job_state_policy.py` (Existing state transition policy pattern).
- `core/exceptions/domain_exceptions.py` (Base `DomainError` hierarchy).

---

## 6. Proposed Files & Components

- Create: `core/entities/agent_task.py`
- Create: `core/policies/agent_task_policy.py`
- Modify: `core/exceptions/domain_exceptions.py` (Add `InvalidStateTransitionError`, `AgentTaskBudgetExceededError`)
- Create: `tests/unit/test_agent_task_policy.py`

---

## 7. Domain & Architectural Constraints

- **Rule 6.1 Compliance:** `core/` must have zero imports of PySide6, Qt, SQLite, or vendor AI SDKs.
- **Rule 14 Compliance:** Cooperative cancellation must transition to `CANCELLED` cleanly without raising unexpected exceptions.

---

## 8. Detailed Implementation Requirements

1. `AgentTaskPolicy.validate_transition` must enforce:
   - `CREATED` can only transition to `CONTEXT_GATHERING`, `CANCELLED`, or `INTERRUPTED`.
   - `PLANNING` can only transition to `AWAITING_PLAN_APPROVAL`, `FAILED`, `CANCELLED`, or `INTERRUPTED`.
   - `AWAITING_PLAN_APPROVAL` can only transition to `RUNNING` (on approval), `REJECTED`, `CANCELLED`, or `INTERRUPTED`.
   - Terminal states (`COMPLETED`, `REJECTED`, `CANCELLED`, `FAILED`) cannot transition to any other state.
   - Any active state can transition to `INTERRUPTED` on startup recovery.
2. Step budgets: Tasks must reject plans exceeding `MAX_TASK_STEPS = 10`.

---

## 9. Acceptance Criteria

- [ ] `AgentTask` aggregate initializes with `CREATED` status and empty step list.
- [ ] Attempting an invalid transition (e.g. `CREATED` $\to$ `COMPLETED`) raises `InvalidStateTransitionError`.
- [ ] Transition from `AWAITING_PLAN_APPROVAL` to `RUNNING` succeeds only when plan is populated.
- [ ] `AgentTaskPolicy.is_terminal()` returns `True` strictly for `COMPLETED`, `REJECTED`, `CANCELLED`, and `FAILED`.
- [ ] Zero imports of `PySide6`, `Qt`, or `sqlite3` in `core/entities/agent_task.py` and `core/policies/agent_task_policy.py`.

---

## 10. Required Tests

- Unit tests in `tests/unit/test_agent_task_policy.py` verifying:
  - All valid state transitions succeed and update `updated_at`.
  - All invalid transitions raise `InvalidStateTransitionError`.
  - Terminal state immutability.
  - Step budget exhaustion checks.

---

## 11. Security Requirements

- Plaintext API keys or credentials must never be assigned to `task_goal`, `arguments`, or step metadata.

---

## 12. Completion Evidence & Follow-up

- Unit test execution evidence passing 100% in repository test suite.
- Unblocks: `TICK-ADE-02`, `TICK-ADE-03`, `TICK-ADE-05`.
