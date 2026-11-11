# TICK-ADE-04: Provider-Neutral Agent Orchestration Service & JSON Plan Generator

- **Ticket ID:** `TICK-ADE-04`
- **Title:** Provider-Neutral Agent Orchestration Service & JSON Plan Generator
- **Milestone:** Milestone A (Agent Foundations)
- **Priority:** P1
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Scope:** Implement the central `AgentOrchestrationService` in `application/services/agent_orchestration_service.py`, responsible for intent understanding, machine-readable JSON plan generation, step dispatching, cooperative cancellation, and unknown network outcome handling.
- **Explicit Non-Goals:**
  - No PySide6/Qt signals inside the application service (signals are adapted in `AgentController` in Milestone F).
  - No direct canonical document overwrites (modifications stay on `AgentWorkingCopy`).
  - No automated retry loops when external network outcome is unknown.
- **Prerequisites:** `TICK-ADE-02` and `TICK-ADE-03` completed; ADR-007 approved.
- **Dependencies (Blocked By):** `TICK-ADE-02`, `TICK-ADE-03`.
- **Unblocks:** `TICK-ADE-11`.

---

## 1. Existing Source-Code Integration Points

- `application/ports/ai_executor.py`: `IAIExecutionService` text execution with fallback.
- `infrastructure/ai/executor_service.py`: `RateLimitedAIExecutor` for outbound HTTPS provider communication.
- `core/policies/agent_task_policy.py`: Deterministic task lifecycle transitions.
- `application/services/tool_registry.py`: Allowlisted tool discovery and execution.
- `application/ports/agent_task_repository.py`: Task state and allowlisted journal persistence.

---

## 2. Proposed Application Interfaces & DTOs

```python
@dataclass(frozen=True)
class StartTaskCommand:
    job_id: int
    user_prompt: str
    context_overrides: Optional[Dict[str, Any]] = None

@dataclass(frozen=True)
class PlanApprovalCommand:
    task_id: str
    approved: bool
    rejection_reason: Optional[str] = None

@dataclass(frozen=True)
class StepProgressEvent:
    task_id: str
    step_index: int
    total_steps: int
    tool_name: str
    status: str
    duration_ms: int

class AgentOrchestrationService:
    def __init__(
        self,
        ai_executor: IAIExecutionService,
        tool_registry: ToolRegistry,
        task_repo: IAgentTaskRepository,
        policy: AgentTaskPolicy,
    ):
        ...

    def start_task(self, command: StartTaskCommand) -> AgentTask:
        ...

    def approve_plan(self, command: PlanApprovalCommand) -> None:
        ...

    def pause_task(self, task_id: str) -> None:
        ...

    def resume_task(self, task_id: str) -> None:
        ...

    def cancel_task(self, task_id: str) -> None:
        ...

    def grant_consent(self, task_id: str, consent_token: str) -> None:
        ...

    def approve_action(self, task_id: str, approval_token: str) -> None:
        ...
```

---

## 3. Detailed Implementation Requirements

1. **Structured JSON Plan Generation:**
   - Dispatches structured prompt using `prompts/agent/planning_v1.txt`.
   - Strips Markdown code blocks (````json ... ````) before parsing.
   - Validates plan structure against schema version 1.
   - Transitions task from `CREATED` $\to$ `CONTEXT_GATHERING` $\to$ `PLANNING` $\to$ `AWAITING_PLAN_APPROVAL`.
   - Execution halts until `approve_plan()` is called.
2. **Step Execution Loop:**
   - Iterates through `plan.steps` sequentially.
   - Evaluates governance via `ToolRegistry.execute_tool()`.
   - If `AWAITING_CONSENT` or `AWAITING_APPROVAL`, transitions task and yields control.
   - Checks cancellation flag between steps (`task.is_cancelled()`).
3. **Unknown Network Outcome Invariant (Finding D):**
   - If an external AI request times out or disconnects before a response is received, the step outcome is marked `REQUEST_OUTCOME_UNKNOWN`.
   - The orchestrator NEVER automatically repeats the request; it transitions task to `PAUSED` or `INTERRUPTED` and requests explicit user instruction.
4. **Journal Audit Logging (Finding E):**
   - Appends entries to `agent_task_journal` via `task_repo` using only allowlisted fields.

---

## 4. Acceptance Criteria

- [ ] `start_task()` successfully prompts the AI executor and parses a valid `AgentPlan`.
- [ ] Task halts deterministically in `AWAITING_PLAN_APPROVAL` without executing any tool step.
- [ ] Calling `approve_plan(approved=True)` executes plan steps sequentially.
- [ ] Calling `cancel_task()` terminates execution cooperatively and marks task `CANCELLED`.
- [ ] Step outputs and state transitions are recorded in the SQLite journal using allowlisted keys.
- [ ] Unknown network outcomes do not trigger automated request replays.
- [ ] AST check confirms zero imports of `PySide6`, `Qt`, or vendor AI SDKs in `application/services/agent_orchestration_service.py`.

---

## 5. Test Specifications

### 5.1 Unit Tests
- `tests/unit/test_agent_orchestration_service.py`:
  - `test_orchestrator_planning_and_approval()`: Full happy path through plan approval.
  - `test_plan_rejection_halts_task()`: User rejection transitions to `REJECTED`.
  - `test_cooperative_cancellation_between_steps()`: Cancelling halts before next step.
  - `test_consent_gate_pauses_execution()`: External transmission tool halts in `AWAITING_CONSENT`.
  - `test_unknown_network_outcome_handling()`: Network timeout halts without automated retry.

### 5.2 Negative & Security Tests
- `test_reject_plans_with_unregistered_tools()`: Rejects model-generated plans requesting nonexistent tools.

### 5.3 Concurrency & Persistence Considerations
- Safe multi-step execution using worker-scoped UoW sessions.

---

## 6. Error and Recovery Behavior

- Tool execution errors trigger at most 1 replan attempt. If replanning fails or step fails again, task transitions to `FAILED`.
- Application shutdown during execution reconciles task to `INTERRUPTED`.

---

## 7. Observability & Audit Requirements

- Emits transport-neutral step progress events (`StepProgressEvent`).
- Appends allowlisted execution records to SQLite journal.

---

## 8. Documentation Updates

- Document orchestrator protocol in ADR-007.

---

## 9. Required Project Skills

- `codebase-design`: Clean service contracts.
- `tdd`: Test-driven orchestrator implementation with mock executors.

---

## 10. Verification Commands

```bash
pytest tests/unit/test_agent_orchestration_service.py -v
python3 -c "import ast; tree = ast.parse(open('application/services/agent_orchestration_service.py').read()); assert not any('PySide6' in ast.dump(n) or 'google' in ast.dump(n) for n in ast.walk(tree))"
```

---

## 11. Definition of Done

- All unit tests pass with 100% coverage.
- Provider-neutral plan generator and step loop verified.
