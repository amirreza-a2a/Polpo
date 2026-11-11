# TICK-ADE-01: Core Domain Agent Task Aggregate, Plan Model & Lifecycle Policy

- **Ticket ID:** `TICK-ADE-01`
- **Title:** Core Domain Agent Task Aggregate, Plan Model & Lifecycle Policy
- **Milestone:** Milestone A (Agent Foundations)
- **Priority:** P1 (Blocker for Milestone A)
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Scope:** Define pure core domain entities, value objects, and deterministic state machine transition policies in `core/entities/agent_task.py` and `core/policies/agent_task_policy.py`.
- **Explicit Non-Goals:**
  - No SQLite persistence or SQL execution (handled in `TICK-ADE-03`).
  - No AI provider network calls or tool execution (handled in `TICK-ADE-02` and `TICK-ADE-04`).
  - No PySide6/Qt signals, QObject models, or QML imports.
- **Prerequisites:** Authoritative directives D-USER-01 and D-USER-02; ADR-007 approval.
- **Dependencies (Blocked By):** None (Foundational ticket).
- **Unblocks:** `TICK-ADE-02`, `TICK-ADE-03`, `TICK-ADE-05`, `TICK-ADE-08`.

---

## 1. Existing Source-Code Integration Points

- `core/entities/job.py`: Architectural reference for domain aggregate dataclasses and monotonic watermark tracking.
- `core/policies/job_state_policy.py`: Architectural precedent for pure deterministic state transition policies and transition validation.
- `core/exceptions/domain_exceptions.py`: Base `DomainError` hierarchy; must be extended with `InvalidStateTransitionError` and `AgentTaskBudgetExceededError`.

---

## 2. Proposed Domain Interfaces & DTOs

```python
class TaskStatus(Enum):
    CREATED = "CREATED"
    CONTEXT_GATHERING = "CONTEXT_GATHERING"
    PLANNING = "PLANNING"
    AWAITING_PLAN_APPROVAL = "AWAITING_PLAN_APPROVAL"
    RUNNING = "RUNNING"
    AWAITING_CONSENT = "AWAITING_CONSENT"
    AWAITING_ACTION_APPROVAL = "AWAITING_ACTION_APPROVAL"
    PAUSED = "PAUSED"
    VALIDATING = "VALIDATING"
    AWAITING_FINAL_APPROVAL = "AWAITING_FINAL_APPROVAL"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"

class PlanStepStatus(Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"

class RequestDisposition(Enum):
    REQUEST_NOT_SUBMITTED = "REQUEST_NOT_SUBMITTED"
    REQUEST_SUBMITTED_SUCCESS = "REQUEST_SUBMITTED_SUCCESS"
    REQUEST_SUBMITTED_FAILED = "REQUEST_SUBMITTED_FAILED"
    REQUEST_OUTCOME_UNKNOWN = "REQUEST_OUTCOME_UNKNOWN"
    TOOL_ACTION_EXECUTED = "TOOL_ACTION_EXECUTED"
    TOOL_ACTION_OUTCOME_UNKNOWN = "TOOL_ACTION_OUTCOME_UNKNOWN"

@dataclass(frozen=True)
class ToolCall:
    call_id: str
    tool_name: str
    arguments: Dict[str, Any]

@dataclass
class PlanStep:
    step_index: int
    tool_name: str
    arguments: Dict[str, Any]
    rationale: str
    status: PlanStepStatus = PlanStepStatus.PENDING
    output: Optional[Dict[str, Any]] = None

@dataclass
class AgentPlan:
    plan_id: str
    task_goal: str                              # Short sanitized display label (max 256 chars)
    assessed_scope: str
    steps: List[PlanStep]
    created_at: float

@dataclass
class AgentTask:
    task_id: str
    job_id: int
    status: TaskStatus
    task_goal: str                              # Short sanitized display label (max 256 chars; never raw private text)
    plan: Optional[AgentPlan] = None
    current_step_index: int = 0
    working_copy_id: Optional[str] = None
    failure_id: Optional[str] = None
    error_message: Optional[str] = None         # Sanitized English summary (max 512 chars)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None        # Set strictly on transition to terminal or interrupted state
```

---

## 3. Detailed Implementation Requirements

1. **State Machine Invariants (`AgentTaskPolicy`):**
   - Valid forward paths:
     - `CREATED` $\to$ `CONTEXT_GATHERING`, `CANCELLED`, `INTERRUPTED`.
     - `CONTEXT_GATHERING` $\to$ `PLANNING`, `FAILED`, `CANCELLED`, `INTERRUPTED`.
     - `PLANNING` $\to$ `AWAITING_PLAN_APPROVAL`, `FAILED`, `CANCELLED`, `INTERRUPTED`.
     - `AWAITING_PLAN_APPROVAL` $\to$ `RUNNING` (upon explicit human approval), `REJECTED`, `CANCELLED`, `INTERRUPTED`.
     - `RUNNING` $\to$ `AWAITING_CONSENT`, `AWAITING_ACTION_APPROVAL`, `PAUSED`, `VALIDATING`, `FAILED`, `CANCELLED`, `INTERRUPTED`.
     - `AWAITING_CONSENT` $\to$ `RUNNING` (upon consent granted), `CANCELLED`, `INTERRUPTED`.
     - `AWAITING_ACTION_APPROVAL` $\to$ `RUNNING` (upon action approval), `CANCELLED`, `INTERRUPTED`.
     - `PAUSED` $\to$ `RUNNING` (resume), `CANCELLED`, `INTERRUPTED`.
     - `VALIDATING` $\to$ `AWAITING_FINAL_APPROVAL` (candidate verified), `RUNNING` (bounded replan), `FAILED`, `CANCELLED`, `INTERRUPTED`.
     - `AWAITING_FINAL_APPROVAL` $\to$ `COMPLETED` (adoption), `REJECTED`, `CANCELLED`, `INTERRUPTED`.
     - `INTERRUPTED` $\to$ `CANCELLED`.
   - Terminal states (`COMPLETED`, `REJECTED`, `CANCELLED`, `FAILED`) are strictly immutable; transitions out of them raise `InvalidStateTransitionError`. Transitioning to any terminal state sets `completed_at = time.time()`.
2. **Exhaustive Crash Recovery Matrix (Finding B):**
   - Every non-terminal state (`CREATED`, `CONTEXT_GATHERING`, `PLANNING`, `AWAITING_PLAN_APPROVAL`, `RUNNING`, `AWAITING_CONSENT`, `AWAITING_ACTION_APPROVAL`, `PAUSED`, `VALIDATING`, `AWAITING_FINAL_APPROVAL`) supports a deterministic transition to `INTERRUPTED` on process restart.
   - Old authorization tokens expire immediately on process exit; no stale tokens authorize subsequent operations.
   - Ambiguous network outcomes are flagged as `REQUEST_OUTCOME_UNKNOWN`, strictly prohibiting automatic retries without user confirmation.
3. **Budget Enforcement:**
   - Tasks reject plans where `len(steps) > MAX_TASK_STEPS = 10` by raising `AgentTaskBudgetExceededError`.

---

## 4. Acceptance Criteria

- [ ] `AgentTask` aggregate initializes with `CREATED` status and empty step list.
- [ ] Transitioning from `CREATED` directly to `COMPLETED` raises `InvalidStateTransitionError`.
- [ ] Transitioning from `AWAITING_PLAN_APPROVAL` to `RUNNING` succeeds only when a valid `AgentPlan` is attached.
- [ ] Every non-terminal status (`CREATED`, `CONTEXT_GATHERING`, `PLANNING`, `AWAITING_PLAN_APPROVAL`, `RUNNING`, `AWAITING_CONSENT`, `AWAITING_ACTION_APPROVAL`, `PAUSED`, `VALIDATING`, `AWAITING_FINAL_APPROVAL`) cleanly transitions to `INTERRUPTED` on startup recovery.
- [ ] Transition to terminal states (or `INTERRUPTED`) sets `completed_at` timestamp.
- [ ] `AgentTaskPolicy.is_terminal()` returns `True` exclusively for `COMPLETED`, `REJECTED`, `CANCELLED`, and `FAILED`.
- [ ] AST check confirms zero imports of `PySide6`, `Qt`, or `sqlite3` in `core/entities/agent_task.py` and `core/policies/agent_task_policy.py`.

---

## 5. Test Specifications

### 5.1 Unit Tests
- `tests/unit/test_agent_task_policy.py`:
  - `test_valid_lifecycle_transitions()`: Verifies step-by-step advance from `CREATED` through `COMPLETED`.
  - `test_all_non_terminal_states_support_interrupted()`: Verifies each of the 10 non-terminal states can transition to `INTERRUPTED`.
  - `test_terminal_transition_sets_completed_at()`: Asserts `completed_at` is populated upon terminal transition.
  - `test_illegal_transition_rejection()`: Verifies illegal jumps raise `InvalidStateTransitionError`.
  - `test_terminal_state_immutability()`: Asserts that no state transitions are permitted out of terminal states.
  - `test_budget_exceeded_rejection()`: Verifies that plans with $> 10$ steps raise `AgentTaskBudgetExceededError`.

### 5.2 Negative & Security Tests
- `test_secret_prohibition_in_entities()`: Ensures task goals, rationale strings, and tool argument dictionaries reject assignment of detected secret keys.

### 5.3 Concurrency & Persistence Considerations
- Pure domain models are immutable or thread-confined; transitions mutate `updated_at` timestamps deterministically.

---

## 6. Error and Recovery Behavior

- Invalid transitions leave entity state completely unchanged and raise `InvalidStateTransitionError`.
- Any active non-terminal state supports deterministic transition to `INTERRUPTED` to support conservative restart recovery (Finding D).

---

## 7. Observability & Audit Requirements

- Every successful state transition updates `updated_at` with high-resolution epoch time.
- Emits structured domain event `AgentTaskStateChangedEvent(task_id, old_status, new_status, timestamp)` for application-layer logging.

---

## 8. Documentation Updates

- Document domain models in `CONTEXT.md` Section 2.
- Verify consistency with ADR-007.

---

## 9. Required Project Skills

- `domain-modeling`: Establishing ubiquitous domain concepts and entity boundaries.
- `tdd`: Implementing unit tests test-first for `AgentTaskPolicy`.

---

## 10. Verification Commands

```bash
pytest tests/unit/test_agent_task_policy.py -v
python3 -c "import ast; tree = ast.parse(open('core/entities/agent_task.py').read()); assert not any('PySide6' in ast.dump(n) or 'sqlite3' in ast.dump(n) for n in ast.walk(tree))"
```

---

## 11. Definition of Done

- All code and unit tests merged to feature branch.
- 100% unit tests pass with zero warnings.
- Clean Architecture AST verification script passes.
